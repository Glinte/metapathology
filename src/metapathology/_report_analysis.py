"""Cutoff-based report capture and analysis.

The monitor records plain event data in import hot paths. This module copies
that data and the relevant live interpreter state exactly once, then performs
report-time analysis. Renderers consume the resulting document and never
inspect the interpreter independently.
"""

import os
import types
from importlib.machinery import PathFinder

from metapathology._module_metadata import ModuleMetadata
from metapathology._records import (
    FinderAPIObservation,
    ImporterCacheChange,
    ImporterCacheEntry,
    ImportMechanismCall,
    ImportResult,
    ImportSearchStarted,
    MetaPathChange,
    MetaPathFinderCall,
    MetaPathReplacement,
    ModuleCacheState,
    ModuleSpecSnapshot,
    MonitorEvent,
    ObjectIdentity,
    PathFinderCall,
    PathHooksChange,
    PathHooksReplacement,
    SysPathChange,
    SysPathReplacement,
    type_name,
)
from metapathology._report_model import (
    CausalExplanation,
    CorrelationEvidence,
    EventEvidence,
    FinderAPIEvidence,
    FinderCallEvidence,
    FinderComparisonEvidence,
    FinderResult,
    FinderResultComparison,
    Finding,
    ImportMechanismEvidence,
    ImportSearch,
    ReportError,
    ReportSummary,
    StandardResolution,
    StructuralComparison,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    from metapathology._records import ImportMechanismOutcome, SearchPathKind
    from metapathology._report_model import (
        FinderResultStatus,
        ImportPresence,
        ImportSearchProgress,
        ResolutionCategory,
    )

    FinderAPIObservationCategory = Literal[
        "indeterminate", "legacy_only", "modern", "modern_and_legacy", "protocol_less"
    ]


_MODULE_FILE_SUFFIXES = (".py", ".pyc", ".pyd", ".so")
_STANDARD_CLASS_FINDER_REASON_PREFIX = "standard CPython class finder;"


class _IdAllocator:
    """Hand out ``prefix:N`` document ids in creation order.

    One allocator per element kind (finding, explanation, result, comparison)
    replaces the fragile ``offset + len(...) + 1`` arithmetic threaded through
    the analysis producers. ``next_id`` is called only when an element is
    actually created, so the numbering stays gap-free and stable.
    """

    __slots__ = ("_count", "_prefix")

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix
        self._count = 0

    def next_id(self) -> str:
        self._count += 1
        return f"{self._prefix}:{self._count}"


class _AnalysisInputs:
    """Copied evidence and state shared by the finding/result producers.

    Bundles the report-time inputs so the wide producer entry points take one
    argument instead of a dozen positionals. ``report_errors`` is a live
    accumulator: checks append to it during analysis.
    """

    __slots__ = (
        "baseline_modules",
        "current_importer_cache",
        "current_path_hooks",
        "events",
        "finder_apis",
        "initial_importer_cache",
        "initial_path_hooks",
        "module_items",
        "module_metadata",
        "report_errors",
        "searches",
    )

    def __init__(
        self,
        *,
        baseline_modules: frozenset[str],
        events: list[MonitorEvent],
        initial_path_hooks: tuple[ObjectIdentity, ...],
        current_path_hooks: tuple[ObjectIdentity, ...] | None,
        initial_importer_cache: tuple[ImporterCacheEntry, ...],
        current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
        module_items: list[tuple[object, object]] | None,
        module_metadata: tuple[ModuleMetadata, ...],
        finder_apis: list[FinderAPIObservation],
        searches: tuple[ImportSearch, ...],
        report_errors: list[ReportError],
    ) -> None:
        self.baseline_modules = baseline_modules
        self.events = events
        self.initial_path_hooks = initial_path_hooks
        self.current_path_hooks = current_path_hooks
        self.initial_importer_cache = initial_importer_cache
        self.current_importer_cache = current_importer_cache
        self.module_items = module_items
        self.module_metadata = module_metadata
        self.finder_apis = finder_apis
        self.searches = searches
        self.report_errors = report_errors


class _ImportStructureComparator:
    """Coordinate report snapshots while keeping external state off value records."""

    __slots__ = (
        "cache_event_seqs_by_path",
        "current_importer_cache",
        "initial_importer_cache",
        "path_hooks_changed",
    )

    def __init__(
        self,
        path_hooks_changed: bool | None,
        initial_importer_cache: dict[str, tuple[int | None, str | None]],
        current_importer_cache: dict[str, tuple[int | None, str | None]] | None,
        cache_event_seqs_by_path: dict[str, tuple[int, ...]],
    ) -> None:
        self.path_hooks_changed = path_hooks_changed
        self.initial_importer_cache = initial_importer_cache
        self.current_importer_cache = current_importer_cache
        self.cache_event_seqs_by_path = cache_event_seqs_by_path

    @classmethod
    def from_snapshots(
        cls,
        events: list[MonitorEvent],
        initial_path_hooks: tuple[ObjectIdentity, ...],
        current_path_hooks: tuple[ObjectIdentity, ...] | None,
        initial_importer_cache: tuple[ImporterCacheEntry, ...],
        current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
    ) -> "_ImportStructureComparator":
        path_hooks_changed = (
            None
            if current_path_hooks is None
            else _import_object_signatures(initial_path_hooks) != _import_object_signatures(current_path_hooks)
        )
        return cls(
            path_hooks_changed,
            _cache_signatures(initial_importer_cache),
            None if current_importer_cache is None else _cache_signatures(current_importer_cache),
            _cache_event_index(events),
        )

    def compare(self, search_path: tuple[str, ...]) -> StructuralComparison:
        """Compare captured identities without calling historical import objects."""
        importer_cache_changed, changed_paths = _changed_cache_paths(
            search_path,
            self.initial_importer_cache,
            self.current_importer_cache,
        )
        event_seqs = tuple(
            sorted({sequence for path in search_path for sequence in self.cache_event_seqs_by_path.get(path, ())})
        )
        return StructuralComparison(self.path_hooks_changed, importer_cache_changed, changed_paths, event_seqs)


class _RepeatedLoadCandidate:
    """Validated earlier load and later same-result failures."""

    __slots__ = ("earlier", "earlier_search", "failures", "first_failure_seq")

    def __init__(
        self,
        first_failure_seq: int,
        earlier: StandardResolution,
        earlier_search: ImportSearch,
        failures: tuple[tuple[StandardResolution, ImportSearch], ...],
    ) -> None:
        self.first_failure_seq = first_failure_seq
        self.earlier = earlier
        self.earlier_search = earlier_search
        self.failures = failures


_SEVERITY_RANK = {"problem": 0, "risk": 1, "note": 2}


def unresolved_searches(searches: tuple[ImportSearch, ...]) -> tuple[ImportSearch, ...]:
    """Searches that started, produced no module by report time, and drew no finder finder_call."""
    return tuple(
        search
        for search in searches
        if search.presence == "absent_at_report" and search.progress in ("started", "failed", "finder_raised")
    )


def _report_summary(
    findings: tuple[Finding, ...],
    explanations: tuple[CausalExplanation, ...],
    searches: tuple[ImportSearch, ...],
) -> ReportSummary:
    """Reduce counts and top references; headline prose stays in the text renderer."""
    counts = {"problem": 0, "risk": 0, "note": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    top_finding = min(
        (finding for finding in findings if finding.severity != "note"),
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 1), item.finding_id),
        default=None,
    )
    top_explanation = next(
        (
            explanation
            for explanation in explanations
            if top_finding is not None and explanation.cause_finding_id == top_finding.finding_id
        ),
        None,
    )
    return ReportSummary(
        problem=counts["problem"],
        risk=counts["risk"],
        note=counts["note"],
        unresolved_import_count=len(unresolved_searches(searches)),
        top_finding_id=None if top_finding is None else top_finding.finding_id,
        top_explanation_id=None if top_explanation is None else top_explanation.explanation_id,
    )


class _ImportSearchBuilder:
    """Index starts, link evidence, and derive conservative search outcomes."""

    def __init__(self, module_items: list[tuple[object, object]] | None) -> None:
        self._present = None if module_items is None else {name for name, _ in module_items if type(name) is str}
        self._starts: dict[int, tuple[str, int, int, str]] = {}
        self._order: list[int] = []
        self._latest_by_thread: dict[int, tuple[int, str]] = {}
        self._linked: dict[int, list[MetaPathFinderCall | ImportMechanismCall | ImportResult | PathFinderCall]] = {}

    def build(self, events: list[MonitorEvent]) -> tuple[ImportSearch, ...]:
        for event in events:
            self._index_event(event)
        return tuple(self._build_search(search_id) for search_id in self._order)

    def _index_event(self, event: MonitorEvent) -> None:
        if isinstance(event, ImportResult):
            self._index_detailed_import(event)
        elif isinstance(event, ImportSearchStarted):
            self._index_audit_start(event)
        elif isinstance(event, (MetaPathFinderCall, ImportMechanismCall)):
            self._link_thread_event(event)
        elif isinstance(event, PathFinderCall) and event.search_id in self._linked:
            self._linked[event.search_id].append(event)

    def _add_start(self, search_id: int, fullname: str, sequence: int, thread_id: int, thread_name: str) -> None:
        if search_id in self._starts:
            return
        self._starts[search_id] = (fullname, sequence, thread_id, thread_name)
        self._order.append(search_id)
        self._linked[search_id] = []

    def _index_detailed_import(self, event: ImportResult) -> None:
        if event.outcome == "started":
            self._add_start(event.search_id, event.fullname, event.sequence, event.thread_id, event.thread_name)
            self._latest_by_thread[event.thread_id] = (event.search_id, event.fullname)
        if event.search_id in self._linked:
            self._linked[event.search_id].append(event)

    def _index_audit_start(self, event: ImportSearchStarted) -> None:
        self._add_start(event.search_id, event.fullname, event.sequence, event.thread_id, event.thread_name)
        fullname, _seq, thread_id, thread_name = self._starts[event.search_id]
        self._starts[event.search_id] = (fullname, event.sequence, thread_id, thread_name)
        self._latest_by_thread[event.thread_id] = (event.search_id, event.fullname)

    def _link_thread_event(self, event: MetaPathFinderCall | ImportMechanismCall) -> None:
        latest = self._latest_by_thread.get(event.thread_id)
        if latest is not None and event.fullname == latest[1]:
            self._linked[latest[0]].append(event)

    def _build_search(self, search_id: int) -> ImportSearch:
        fullname, start_seq, thread_id, thread_name = self._starts[search_id]
        evidence = self._linked[search_id]
        return ImportSearch(
            search_id,
            fullname,
            start_seq,
            tuple(event.sequence for event in evidence if event.sequence != start_seq),
            thread_id,
            thread_name,
            self._progress(evidence),
            self._presence(fullname),
        )

    def _presence(self, fullname: str) -> "ImportPresence":
        if self._present is None:
            return "unknown"
        return "present_at_report" if fullname in self._present else "absent_at_report"

    @staticmethod
    def _progress(
        evidence: list[MetaPathFinderCall | ImportMechanismCall | ImportResult | PathFinderCall],
    ) -> "ImportSearchProgress":
        exact: ImportMechanismOutcome | None = None
        for event in reversed(evidence):
            if isinstance(event, ImportResult) and event.outcome != "started":
                exact = event.outcome
                break
        if exact is not None:
            return exact
        finder_called = any(isinstance(event, MetaPathFinderCall) and event.found for event in evidence)
        raised = any(
            isinstance(event, MetaPathFinderCall) and event.exception_type_name is not None for event in evidence
        )
        if finder_called and raised:
            return "unknown"
        if finder_called:
            return "finder_found"
        if raised:
            return "finder_raised"
        return "started"


def _import_searches(
    events: list[MonitorEvent], module_items: list[tuple[object, object]] | None
) -> tuple[ImportSearch, ...]:
    """Join calls only to the matching latest audit start on their thread."""
    return _ImportSearchBuilder(module_items).build(events)


def _standard_resolutions(
    events: list[MonitorEvent],
    searches: tuple[ImportSearch, ...],
    inventory: tuple[ModuleMetadata, ...],
) -> tuple[StandardResolution, ...]:
    """Join exact aggregate results or conservative post-hoc standard evidence."""
    by_seq = {event.sequence: event for event in events}
    audits = {event.search_id: event for event in events if isinstance(event, ImportSearchStarted)}
    captured = {event.search_id: event for event in events if isinstance(event, PathFinderCall)}
    metadata = {entry.name: entry for entry in inventory}
    resolutions: list[StandardResolution] = []
    for search in searches:
        audit = audits.get(search.search_id)
        aggregate = captured.get(search.search_id)
        summary = aggregate.spec_summary if aggregate is not None else None
        if summary is None:
            entry = metadata.get(search.fullname)
            if entry is None or entry.inspection != "available":
                continue
            summary = entry.spec_summary
        classified = _standard_spec_classification(summary)
        if classified is None:
            continue
        finder_type_name, category, loader_type_name, origin = classified
        event_values = tuple(by_seq[sequence] for sequence in search.event_seqs if sequence in by_seq)
        if aggregate is None and any(isinstance(event, MetaPathFinderCall) and event.found for event in event_values):
            continue
        if audit is None or finder_type_name not in audit.meta_path_type_names or category != "namespace":
            if aggregate is None:
                continue
            later_finders: tuple[str, ...] = ()
        else:
            finder_index = audit.meta_path_type_names.index(finder_type_name)
            later_finders = tuple(
                name
                for name in audit.meta_path_type_names[finder_index + 1 :]
                if name not in ("BuiltinImporter", "FrozenImporter", "PathFinder")
            )
        components = tuple(
            event.sequence
            for event in event_values
            if isinstance(event, ImportMechanismCall)
            and event.boundary == "path_entry_finder"
            and event.fullname == search.fullname
        )
        resolutions.append(
            StandardResolution(
                search_id=search.search_id,
                fullname=search.fullname,
                finder_type_name=finder_type_name,
                category=category,
                loader_type_name=loader_type_name,
                origin=origin,
                evidence_level="observed" if aggregate is not None else "inferred",
                state_phase="import" if aggregate is not None else "report",
                event_seq=None if aggregate is None else aggregate.sequence,
                component_event_seqs=components,
                later_finders=later_finders,
            )
        )
    return tuple(resolutions)


def _standard_spec_classification(
    summary: ModuleSpecSnapshot | None,
) -> "tuple[str, ResolutionCategory, str | None, str | None] | None":
    """Classify only loader shapes owned by CPython's standard finders."""
    if summary is None:
        return None
    loader_type_name = None if summary.loader is None else summary.loader.type_name
    origin = summary.origin if type(summary.origin) is str else None
    if summary.is_namespace:
        return "PathFinder", "namespace", loader_type_name, origin
    if loader_type_name == "BuiltinImporter":
        return "BuiltinImporter", "built_in", loader_type_name, origin
    if loader_type_name == "FrozenImporter":
        return "FrozenImporter", "frozen", loader_type_name, origin
    categories: dict[str, ResolutionCategory] = {
        "SourceFileLoader": "source",
        "SourcelessFileLoader": "bytecode",
        "ExtensionFileLoader": "extension",
        "zipimporter": "zip",
    }
    category = categories.get(loader_type_name or "")
    if category is None:
        return None
    return "PathFinder", category, loader_type_name, origin


def _suspicious_findings(
    inputs: _AnalysisInputs,
    finding_ids: _IdAllocator,
    result_ids: _IdAllocator,
    comparison_ids: _IdAllocator,
    standard_path_check: bool = True,
) -> tuple[tuple[Finding, ...], tuple[FinderResult, ...], tuple[FinderResultComparison, ...]]:
    """Build neutral resolution results, then promote only corroborated effects."""
    baseline_modules = inputs.baseline_modules
    events = inputs.events
    initial_path_hooks = inputs.initial_path_hooks
    current_path_hooks = inputs.current_path_hooks
    initial_importer_cache = inputs.initial_importer_cache
    current_importer_cache = inputs.current_importer_cache
    module_items = inputs.module_items
    module_metadata = inputs.module_metadata
    finder_apis = inputs.finder_apis
    searches = inputs.searches
    report_errors = inputs.report_errors
    winners = {event.fullname: event for event in events if isinstance(event, MetaPathFinderCall) and event.found}
    structural_context = _ImportStructureComparator.from_snapshots(
        events,
        initial_path_hooks,
        current_path_hooks,
        initial_importer_cache,
        current_importer_cache,
    )
    meta_short_circuit_seqs = _meta_short_circuit_finder_call_seqs(events)
    findings = _finder_api_findings(finder_apis, finding_ids)
    findings.extend(_finder_changed_module_cache_findings(events, finding_ids))
    findings.extend(_module_replacement_findings(events, finding_ids))
    results: list[FinderResult] = []
    comparisons: list[FinderResultComparison] = []
    if module_items is None:
        findings.extend(_competing_path_hooks_findings(events, finding_ids))
        findings.extend(_import_failed_after_state_change_findings(events, finding_ids))
        return tuple(findings), (), ()
    metadata_by_name = {entry.name: entry for entry in module_metadata}
    for name, module in module_items:
        if type(name) is not str or name in baseline_modules or name == "__main__":
            continue
        winner = winners.get(name)
        if winner is not None:
            captured_result, standard_result, comparison, structural_comparison = _finder_results(
                name,
                module,
                winner,
                result_ids,
                comparison_ids,
                structural_context,
                winner.sequence in meta_short_circuit_seqs,
                report_errors,
                standard_path_check,
            )
            results.append(captured_result)
            if standard_result is not None and comparison is not None:
                results.append(standard_result)
                comparisons.append(comparison)
                finding = _corroborated_result_finding(
                    name,
                    winner,
                    captured_result,
                    standard_result,
                    comparison,
                    structural_comparison,
                    searches,
                    finding_ids,
                    winner.sequence in meta_short_circuit_seqs,
                )
                if finding is not None:
                    findings.append(finding)
            continue
        metadata = metadata_by_name.get(name)
        if metadata is not None and metadata.inspection == "available" and metadata.spec_is_none:
            findings.append(
                Finding(
                    finding_ids.next_id(),
                    "module_without_spec",
                    name,
                    evidence_level="current_state",
                    limitations=("report_snapshot_cannot_identify_load_mechanism",),
                    severity="note",
                    evidence=EventEvidence(),
                )
            )
    findings.extend(_competing_path_hooks_findings(events, finding_ids))
    findings.extend(_import_failed_after_state_change_findings(events, finding_ids))
    return tuple(findings), tuple(results), tuple(comparisons)


def _finder_api_category(observation: FinderAPIObservation) -> "FinderAPIObservationCategory":
    """Summarize the two independently inspected protocol states."""
    modern = observation.find_spec.availability
    legacy = observation.find_module.availability
    if "indeterminate" in (modern, legacy):
        return "indeterminate"
    if modern == "callable":
        return "modern_and_legacy" if legacy == "callable" else "modern"
    return "legacy_only" if legacy == "callable" else "protocol_less"


def _finder_api_findings(observations: list[FinderAPIObservation], finding_ids: _IdAllocator) -> list[Finding]:
    """Expose nonstandard legacy-only observations as problem findings."""
    findings: list[Finding] = []
    for observation in observations:
        if _finder_api_category(observation) != "legacy_only":
            continue
        findings.append(
            Finding(
                finding_ids.next_id(),
                "legacy_finder_api",
                observation.finder_type_name,
                evidence_level="observed",
                limitations=("protocols_were_inspected_not_invoked",),
                subject_kind="finder",
                evidence=FinderAPIEvidence(finder_api=observation),
            )
        )
    return findings


class _CausalExplanationBuilder:
    """Build each causal explanation family independently in stable order."""

    def __init__(
        self,
        findings: tuple[Finding, ...],
        searches: tuple[ImportSearch, ...],
        standard_resolutions: tuple[StandardResolution, ...],
        finder_result_comparisons: tuple[FinderResultComparison, ...],
        explanation_ids: _IdAllocator,
        events: list[MonitorEvent] | None,
    ) -> None:
        self.findings = findings
        self.searches = searches
        self.standard_resolutions = standard_resolutions
        self.comparisons = {item.comparison_id: item for item in finder_result_comparisons}
        self.ids = explanation_ids
        self.events_by_seq = {} if events is None else {event.sequence: event for event in events}
        self.displacement_findings = {
            finding.module: finding for finding in findings if finding.kind == "module_hides_namespace"
        }

    def build(self) -> tuple[CausalExplanation, ...]:
        explanations = self._missing_namespace_locations_explanations()
        explanations.extend(self._standard_resolution_explanations())
        explanations.extend(self._finding_explanations())
        return _append_ambiguous_explanations(tuple(explanations), self.ids)

    def _missing_namespace_locations_explanations(self) -> list[CausalExplanation]:
        explanations: list[CausalExplanation] = []
        for finding in self.findings:
            evidence = finding.evidence
            if finding.kind != "missing_namespace_locations" or not isinstance(evidence, FinderComparisonEvidence):
                continue
            comparison = self.comparisons.get(evidence.result_comparison_id)
            if comparison is None:
                continue
            explanations.extend(self._truncation_failures(finding, evidence.finder_call, comparison))
        return explanations

    def _truncation_failures(
        self, finding: Finding, finder_call: MetaPathFinderCall, comparison: FinderResultComparison
    ) -> list[CausalExplanation]:
        prefix = finding.module + "."
        groups: dict[str, list[ImportSearch]] = {}
        for search in self.searches:
            if search.fullname.startswith(prefix) and search.presence != "present_at_report":
                groups.setdefault(search.fullname, []).append(search)
        descendants = [
            next((search for search in group if search.progress == "failed"), group[0])
            for group in groups.values()
            if not any(search.progress == "loaded" for search in group)
        ]
        return [
            explanation
            for search in descendants
            if (explanation := self._truncation_failure(finding, comparison, finder_call, prefix, search)) is not None
        ]

    def _truncation_failure(
        self,
        finding: Finding,
        comparison: FinderResultComparison,
        finder_call: MetaPathFinderCall,
        prefix: str,
        search: ImportSearch,
    ) -> CausalExplanation | None:
        if search.progress != "failed" or not any(sequence > finder_call.sequence for sequence in search.event_seqs):
            return None
        match = _omitted_module_candidate(comparison.only_in_right_result, search.fullname[len(prefix) :].split("."))
        if match is None:
            return None
        omitted_location, candidate_path = match
        event_seqs = (
            finder_call.sequence,
            *(sequence for sequence in search.event_seqs if sequence > finder_call.sequence),
        )
        return CausalExplanation(
            self.ids.next_id(),
            "missing_namespace_locations_failure",
            "correlated",
            search.fullname,
            "failed",
            finding.finding_id,
            finder_call.finder_type_name,
            omitted_location,
            candidate_path,
            tuple(dict.fromkeys(event_seqs)),
            None,
        )

    def _standard_resolution_explanations(self) -> list[CausalExplanation]:
        explanations: list[CausalExplanation] = []
        for resolution in self.standard_resolutions:
            explanations.extend(self._namespace_candidate_explanations(resolution))
            precedence = self._standard_precedence_explanation(resolution)
            if precedence is not None:
                explanations.append(precedence)
        return explanations

    def _namespace_candidate_explanations(self, resolution: StandardResolution) -> list[CausalExplanation]:
        if resolution.category == "namespace" or resolution.origin is None:
            return []
        components = [
            event
            for sequence in resolution.component_event_seqs
            if isinstance((event := self.events_by_seq.get(sequence)), ImportMechanismCall)
            and event.boundary == "path_entry_finder"
            and event.outcome == "found"
            and event.path is not None
        ]
        selected_index = next(
            (
                index
                for index in range(len(components) - 1, -1, -1)
                if _path_contains(components[index].path, resolution.origin)
            ),
            None,
        )
        if selected_index is None or selected_index == 0:
            return []
        displacement = self.displacement_findings.get(resolution.fullname)
        descendants = () if displacement is None else _failed_descendants_after(resolution, self.searches)
        return [
            self._namespace_candidate_explanation(
                resolution, components[0], components[selected_index], displacement, item
            )
            for item in (descendants or (None,))
        ]

    def _namespace_candidate_explanation(
        self,
        resolution: StandardResolution,
        candidate: ImportMechanismCall,
        selected: ImportMechanismCall,
        finding: Finding | None,
        descendant: ImportSearch | None,
    ) -> CausalExplanation:
        event_seqs = tuple(
            dict.fromkeys(
                (
                    *((resolution.event_seq,) if resolution.event_seq is not None else ()),
                    candidate.sequence,
                    selected.sequence,
                    *(() if descendant is None else descendant.event_seqs),
                )
            )
        )
        return CausalExplanation(
            explanation_id=self.ids.next_id(),
            kind="namespace_candidate_hidden",
            confidence="observed" if descendant is None else "correlated",
            subject=resolution.fullname if descendant is None else descendant.fullname,
            effect_status="regular_module_selected" if descendant is None else "descendant_failed",
            cause_finding_id=None if finding is None else finding.finding_id,
            finder_type_name=resolution.finder_type_name,
            omitted_location="",
            candidate_path=candidate.path or "",
            event_seqs=event_seqs,
            next_observation=None,
            origin=resolution.origin,
        )

    def _standard_precedence_explanation(self, resolution: StandardResolution) -> CausalExplanation | None:
        if not resolution.later_finders:
            return None
        event_seqs = (() if resolution.event_seq is None else (resolution.event_seq,)) + resolution.component_event_seqs
        return CausalExplanation(
            explanation_id=self.ids.next_id(),
            kind="path_finder_precedence",
            confidence="observed" if resolution.evidence_level == "observed" else "inferred",
            subject=resolution.fullname,
            effect_status=resolution.category,
            cause_finding_id=None,
            finder_type_name=resolution.finder_type_name,
            omitted_location="",
            candidate_path="",
            event_seqs=event_seqs,
            next_observation=None if resolution.evidence_level == "observed" else "capture_import_results",
            origin=resolution.origin,
            standard_search_id=resolution.search_id,
            later_finders=resolution.later_finders,
        )

    def _finding_explanations(self) -> list[CausalExplanation]:
        explanations: list[CausalExplanation] = []
        for finding in self.findings:
            explanation = self._finding_explanation(finding)
            if explanation is not None:
                explanations.append(explanation)
        return explanations

    def _finding_explanation(self, finding: Finding) -> CausalExplanation | None:
        evidence = finding.evidence
        if finding.kind == "finder_changed_module_cache" and isinstance(evidence, FinderCallEvidence):
            return self._finder_changed_module_cache_explanation(finding, evidence.finder_call)
        if finding.kind in ("module_replacement", "module_executed_again") and isinstance(
            evidence, ImportMechanismEvidence
        ):
            return self._module_identity_explanation(finding, evidence)
        if finding.kind == "module_failed_after_loading":
            return self._repeated_load_explanation(finding)
        return None

    def _finder_changed_module_cache_explanation(
        self, finding: Finding, finder_call: MetaPathFinderCall
    ) -> CausalExplanation:
        outcome = "finder_raised" if finder_call.exception_type_name is not None else "finder_returned_none"
        return CausalExplanation(
            self.ids.next_id(),
            "finder_changed_module_cache",
            "observed",
            finding.module,
            outcome,
            finding.finding_id,
            finder_call.finder_type_name,
            "",
            "",
            (finder_call.sequence,),
            None,
            boundary="find_spec",
            state_before=finder_call.module_state_before,
            state_after=finder_call.module_state_after,
        )

    def _module_identity_explanation(self, finding: Finding, evidence: ImportMechanismEvidence) -> CausalExplanation:
        call = evidence.import_mechanism_call
        baseline = evidence.module_state_baseline or call.module_state_before
        target_diverged = _target_identity_diverged(evidence.module_state_baseline, call, baseline)
        kind = "module_replacement" if finding.kind == "module_replacement" else "module_executed_again"
        return CausalExplanation(
            explanation_id=self.ids.next_id(),
            kind=kind,
            confidence="observed",
            subject=finding.module,
            effect_status="separate_module_executed" if target_diverged else "module_identity_replaced",
            cause_finding_id=finding.finding_id,
            finder_type_name=call.object_type_name,
            omitted_location="",
            candidate_path="",
            event_seqs=tuple(dict.fromkeys((*finding.supporting_event_seqs, call.sequence))),
            next_observation=None,
            boundary=call.boundary,
            state_before=baseline,
            state_after=call.target_state if target_diverged else call.module_state_after,
        )

    def _repeated_load_explanation(self, finding: Finding) -> CausalExplanation | None:
        matched = [
            resolution
            for resolution in self.standard_resolutions
            if resolution.fullname == finding.module
            and resolution.event_seq is not None
            and resolution.event_seq in finding.supporting_event_seqs
        ]
        if len(matched) < 2:
            return None
        later = matched[-1]
        return CausalExplanation(
            explanation_id=self.ids.next_id(),
            kind="module_failed_after_loading",
            confidence="correlated",
            subject=finding.module,
            effect_status="later_import_failed",
            cause_finding_id=finding.finding_id,
            finder_type_name=later.loader_type_name or later.finder_type_name,
            omitted_location="",
            candidate_path="",
            event_seqs=finding.supporting_event_seqs,
            next_observation=None,
            origin=later.origin,
            standard_search_id=later.search_id,
        )


def _target_identity_diverged(
    module_state_baseline: ModuleCacheState | None,
    call: ImportMechanismCall,
    baseline: ModuleCacheState | None,
) -> bool:
    if (
        baseline is None
        or baseline.state != "object"
        or call.target_state is None
        or call.target_state.state != "object"
    ):
        return False
    if baseline.object_id == call.target_state.object_id:
        return False
    cache_replaced = (
        call.module_state_after is not None
        and call.module_state_after.state == "object"
        and baseline.object_id != call.module_state_after.object_id
    )
    return module_state_baseline is not None or not cache_replaced


def _causal_explanations(
    findings: tuple[Finding, ...],
    searches: tuple[ImportSearch, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    finder_result_comparisons: tuple[FinderResultComparison, ...],
    explanation_ids: _IdAllocator,
    events: list[MonitorEvent] | None = None,
) -> tuple[CausalExplanation, ...]:
    """Join namespace loss to descendant searches and report-time path evidence."""
    return _CausalExplanationBuilder(
        findings, searches, standard_resolutions, finder_result_comparisons, explanation_ids, events
    ).build()


def _failed_descendants_after(
    resolution: StandardResolution,
    searches: tuple[ImportSearch, ...],
) -> tuple[ImportSearch, ...]:
    """Return every exact descendant failure after a parent resolution."""
    boundary = resolution.event_seq
    if boundary is None:
        return ()
    prefix = resolution.fullname + "."
    return tuple(
        search
        for search in searches
        if search.fullname.startswith(prefix)
        and search.progress == "failed"
        and any(sequence > boundary for sequence in search.event_seqs)
    )


def _namespace_displacement_findings(
    searches: tuple[ImportSearch, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    events: list[MonitorEvent],
    finding_ids: _IdAllocator,
) -> tuple[Finding, ...]:
    """Promote a displaced namespace only when a descendant then fails."""
    events_by_seq = {event.sequence: event for event in events}
    findings: list[Finding] = []
    for resolution in standard_resolutions:
        if resolution.category == "namespace" or resolution.origin is None:
            continue
        components = [
            event
            for sequence in resolution.component_event_seqs
            if isinstance((event := events_by_seq.get(sequence)), ImportMechanismCall)
            and event.boundary == "path_entry_finder"
            and event.outcome == "found"
            and event.path is not None
        ]
        selected_index = next(
            (
                index
                for index in range(len(components) - 1, -1, -1)
                if _path_contains(components[index].path, resolution.origin)
            ),
            None,
        )
        descendants = _failed_descendants_after(resolution, searches)
        if selected_index is None or selected_index == 0 or not descendants:
            continue
        candidate = components[0]
        selected = components[selected_index]
        event_seqs = tuple(
            dict.fromkeys(
                (
                    *((resolution.event_seq,) if resolution.event_seq is not None else ()),
                    candidate.sequence,
                    selected.sequence,
                    *(sequence for descendant in descendants for sequence in descendant.event_seqs),
                )
            )
        )
        findings.append(
            Finding(
                finding_ids.next_id(),
                "module_hides_namespace",
                resolution.fullname,
                evidence_level="correlated",
                limitations=("exception_message_not_captured",),
                evidence=CorrelationEvidence(search_ids=tuple(descendant.search_id for descendant in descendants)),
                supporting_event_seqs=event_seqs,
                severity="problem",
                signals=("namespace_candidate_found", "regular_module_selected", "descendant_failed"),
            )
        )
    return tuple(findings)


def _module_failed_after_loading_findings(
    searches: tuple[ImportSearch, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    finding_ids: _IdAllocator,
) -> tuple[Finding, ...]:
    """Correlate a later failure with an earlier load from the same origin."""
    groups = _captured_load_groups(searches, standard_resolutions)
    candidates = sorted(_repeated_load_candidates(groups), key=lambda item: item.first_failure_seq)
    return tuple(_repeated_load_finding(candidate, searches, finding_ids) for candidate in candidates)


def _captured_load_groups(
    searches: tuple[ImportSearch, ...],
    standard_resolutions: tuple[StandardResolution, ...],
) -> dict[tuple[str, str, str], list[tuple[StandardResolution, ImportSearch]]]:
    """Extract captured loaded/failed searches grouped by semantic result."""
    searches_by_id = {search.search_id: search for search in searches}
    groups: dict[tuple[str, str, str], list[tuple[StandardResolution, ImportSearch]]] = {}
    ordered = sorted(
        standard_resolutions,
        key=lambda resolution: -1 if resolution.event_seq is None else resolution.event_seq,
    )
    for resolution in ordered:
        if (
            resolution.evidence_level != "observed"
            or resolution.origin is None
            or resolution.loader_type_name is None
            or resolution.event_seq is None
        ):
            continue
        search = searches_by_id.get(resolution.search_id)
        if search is None:
            continue
        if search.progress in ("loaded", "failed"):
            key = (resolution.fullname, resolution.loader_type_name, _path_key(resolution.origin))
            groups.setdefault(key, []).append((resolution, search))
    return groups


def _repeated_load_candidates(
    groups: dict[tuple[str, str, str], list[tuple[StandardResolution, ImportSearch]]],
) -> list[_RepeatedLoadCandidate]:
    """Validate groups containing an earlier load and at least one later failure."""
    candidates: list[_RepeatedLoadCandidate] = []
    for group in groups.values():
        earlier_pair = next((pair for pair in group if pair[1].progress == "loaded"), None)
        if earlier_pair is None:
            continue
        earlier, earlier_search = earlier_pair
        earlier_event_seq = earlier.event_seq
        if earlier_event_seq is None:
            continue
        failures = [
            pair
            for pair in group
            if pair[1].progress == "failed" and pair[0].event_seq is not None and pair[0].event_seq > earlier_event_seq
        ]
        if not failures:
            continue
        first_failure_seq = min(
            max(search.event_seqs, default=resolution.event_seq or 0) for resolution, search in failures
        )
        candidates.append(_RepeatedLoadCandidate(first_failure_seq, earlier, earlier_search, tuple(failures)))
    return candidates


def _repeated_load_finding(
    candidate: _RepeatedLoadCandidate,
    searches: tuple[ImportSearch, ...],
    finding_ids: _IdAllocator,
) -> Finding:
    """Construct one finding from a validated repeated-load candidate."""
    earlier = candidate.earlier
    earlier_event_seq = earlier.event_seq
    assert earlier_event_seq is not None
    failed_searches = [
        search
        for search in searches
        if search.fullname == earlier.fullname
        and search.progress == "failed"
        and any(sequence > earlier_event_seq for sequence in search.event_seqs)
    ]
    event_seqs = tuple(
        dict.fromkeys(
            (
                earlier_event_seq,
                *candidate.earlier_search.event_seqs,
                *(
                    resolution.event_seq
                    for resolution, _attempt in candidate.failures
                    if resolution.event_seq is not None
                ),
                *(sequence for search in failed_searches for sequence in search.event_seqs),
            )
        )
    )
    return Finding(
        finding_ids.next_id(),
        "module_failed_after_loading",
        earlier.fullname,
        evidence_level="correlated",
        limitations=("exception_message_not_captured",),
        evidence=CorrelationEvidence(
            search_ids=(candidate.earlier_search.search_id, *(search.search_id for search in failed_searches))
        ),
        supporting_event_seqs=event_seqs,
        severity="problem",
        signals=("same_loader", "same_origin", "earlier_search_loaded", "later_search_failed"),
    )


def _path_contains(directory: str | None, path: str) -> bool:
    """Return whether an exact captured path entry contains a selected origin."""
    if directory is None:
        return False
    try:
        return os.path.commonpath((_path_key(directory), _path_key(path))) == _path_key(directory)
    except (OSError, ValueError):
        return False


def _append_ambiguous_explanations(
    explanations: tuple[CausalExplanation, ...],
    explanation_ids: _IdAllocator,
) -> tuple[CausalExplanation, ...]:
    """Expose equally supported, contradictory conclusions without selecting one."""
    groups: dict[tuple[str, tuple[int, ...]], list[CausalExplanation]] = {}
    for explanation in explanations:
        if explanation.event_seqs:
            groups.setdefault((explanation.subject, explanation.event_seqs), []).append(explanation)

    ambiguous: list[CausalExplanation] = []
    for (subject, event_seqs), group in groups.items():
        conclusions = {(item.kind, item.effect_status) for item in group}
        confidences = {item.confidence for item in group}
        if len(group) < 2 or len(conclusions) < 2 or len(confidences) != 1:
            continue
        ambiguous.append(
            CausalExplanation(
                explanation_id=explanation_ids.next_id(),
                kind="ambiguous_contention",
                confidence="unknown",
                subject=subject,
                effect_status="conflicting_explanations",
                cause_finding_id=None,
                finder_type_name="",
                omitted_location="",
                candidate_path="",
                event_seqs=event_seqs,
                next_observation="capture_a_more_specific_boundary",
                alternatives=tuple(item.explanation_id for item in group),
            )
        )
    return explanations + tuple(ambiguous)


def _omitted_module_candidate(omitted_locations: tuple[str, ...], relative_parts: list[str]) -> tuple[str, str] | None:
    """Find a report-time child directory or module file under an omitted path."""
    for location in omitted_locations:
        stem = os.path.join(location, *relative_parts)
        try:
            if os.path.isdir(stem):
                return location, stem
            for suffix in _MODULE_FILE_SUFFIXES:
                candidate = stem + suffix
                if os.path.isfile(candidate):
                    return location, candidate
        except OSError:
            continue
    return None


def _meta_short_circuit_finder_call_seqs(events: list[MonitorEvent]) -> set[int]:
    """Identify finder_calls captured before the import-time PathFinder position."""
    latest_audit: dict[tuple[int, str], ImportSearchStarted] = {}
    finder_call_seqs: set[int] = set()
    for event in events:
        if isinstance(event, ImportSearchStarted):
            latest_audit[(event.thread_id, event.fullname)] = event
            continue
        if not isinstance(event, MetaPathFinderCall) or not event.found:
            continue
        audit = latest_audit.get((event.thread_id, event.fullname))
        if audit is None or audit.sequence >= event.sequence or "PathFinder" not in audit.meta_path_type_names:
            continue
        try:
            custom_index = audit.meta_path_type_names.index(event.finder_type_name)
            path_finder_index = audit.meta_path_type_names.index("PathFinder")
        except ValueError:
            continue
        if custom_index >= path_finder_index:
            continue
        finder_call_seqs.add(event.sequence)
    return finder_call_seqs


def _competing_path_hooks_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Report paths accepted by distinct hooks across captured detailed calls."""
    accepted: dict[str, ImportMechanismCall] = {}
    findings: list[Finding] = []
    emitted: set[tuple[str, int, int]] = set()
    for event in events:
        if (
            not isinstance(event, ImportMechanismCall)
            or event.boundary != "path_hook"
            or event.outcome != "returned"
            or event.path is None
        ):
            continue
        earlier = accepted.get(event.path)
        if earlier is None:
            accepted[event.path] = event
            continue
        if earlier.object_id == event.object_id:
            continue
        key = (event.path, earlier.object_id, event.object_id)
        if key in emitted:
            continue
        emitted.add(key)
        findings.append(
            Finding(
                finding_ids.next_id(),
                "competing_path_hooks",
                event.path,
                evidence=ImportMechanismEvidence(import_mechanism_call=event),
                evidence_level="inferred_from_state",
                limitations=("acceptance_was_captured_across_distinct_resolution_states",),
                subject_kind="path",
                supporting_event_seqs=(earlier.sequence,),
            )
        )
    return findings


def _import_failed_after_state_change_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Pair failures only with structural mutations captured inside that search."""
    mutation_seqs: list[int] = []
    started: dict[int, ImportResult] = {}
    findings: list[Finding] = []
    for event in events:
        if isinstance(
            event,
            (
                MetaPathChange,
                MetaPathReplacement,
                PathHooksChange,
                PathHooksReplacement,
                SysPathChange,
                SysPathReplacement,
                ImporterCacheChange,
            ),
        ):
            mutation_seqs.append(event.sequence)
            continue
        if not isinstance(event, ImportResult):
            continue
        if event.outcome == "started":
            started[event.search_id] = event
            continue
        if event.outcome != "failed":
            continue
        start = started.get(event.search_id)
        if start is None:
            continue
        mutation_seq = next(
            (sequence for sequence in reversed(mutation_seqs) if start.sequence < sequence < event.sequence), None
        )
        if mutation_seq is None:
            continue
        findings.append(
            Finding(
                finding_ids.next_id(),
                "import_failed_after_state_change",
                event.fullname,
                evidence=EventEvidence(),
                evidence_level="inferred_from_state",
                limitations=("temporal_correlation_does_not_prove_mutation_caused_failure",),
                supporting_event_seqs=(mutation_seq, start.sequence, event.sequence),
            )
        )
    return findings


def _finder_changed_module_cache_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Return captured target-cache changes made by passing or raising finders."""
    findings: list[Finding] = []
    for event in events:
        if not isinstance(event, MetaPathFinderCall) or event.found:
            continue
        before = event.module_state_before
        after = event.module_state_after
        if before is None or after is None or not _module_state_changed(before, after):
            continue
        findings.append(
            Finding(
                finding_ids.next_id(),
                "finder_changed_module_cache",
                event.fullname,
                evidence=FinderCallEvidence(finder_call=event),
                evidence_level="observed",
                limitations=("boundary_delta_does_not_identify_nested_cause",),
            )
        )
    return findings


def _module_state_changed(before: ModuleCacheState, after: ModuleCacheState) -> bool:
    """Compare available identity states without conflating unavailable evidence."""
    return before.compare(after) == "different"


def _finder_results(
    name: str,
    module: object,
    winner: MetaPathFinderCall,
    result_ids: _IdAllocator,
    comparison_ids: _IdAllocator,
    structural_context: _ImportStructureComparator,
    meta_short_circuit: bool,
    report_errors: list[ReportError],
    standard_path_check: bool,
) -> tuple[FinderResult, FinderResult | None, FinderResultComparison | None, StructuralComparison]:
    """Describe a captured finder_call and an independent standard-path check."""
    observed = winner.spec_summary
    if observed is not None:
        observed = _current_state_spec_summary(module, observed)
    structural_comparison = structural_context.compare(winner.search_path)
    captured_result = FinderResult(
        result_id=result_ids.next_id(),
        module=name,
        kind="observed_finder_result",
        purpose="record_custom_finder_result",
        limitations=(),
        evidence_level="observed",
        state_phase="import",
        predicts_alternative_winner=False,
        finder_type_name=winner.finder_type_name,
        finder_id=winner.finder_id,
        status="found",
        spec_summary=observed,
        exception_type_name=None,
        source_event_seqs=(winner.sequence,),
        search_path=winner.search_path,
        search_path_kind=winner.search_path_kind,
        search_path_phase="import",
        signals=("meta_path_short_circuit",) if meta_short_circuit else (),
    )
    if not standard_path_check:
        return captured_result, None, None, structural_comparison
    target: types.ModuleType | None = None
    target_available = winner.target_state is None
    if (
        winner.target_state is not None
        and type(module) is types.ModuleType
        and id(module) == winner.target_state.object_id
    ):
        target = module
        target_available = True
    standard_result = _check_standard_path(
        result_ids.next_id(),
        name,
        winner.search_path,
        winner.search_path_kind,
        target,
        target_available,
        (winner.sequence,),
    )
    if standard_result.status == "failed":
        report_errors.append(ReportError("standard_path_check", standard_result.exception_type_name or "Exception"))
    comparison = captured_result.compare(
        standard_result,
        comparison_id=comparison_ids.next_id(),
        structural_comparison=structural_comparison,
    )
    return captured_result, standard_result, comparison, structural_comparison


def _corroborated_result_finding(
    name: str,
    winner: MetaPathFinderCall,
    captured_result: FinderResult,
    standard_result: FinderResult,
    comparison: FinderResultComparison,
    structural_comparison: StructuralComparison,
    searches: tuple[ImportSearch, ...],
    finding_ids: _IdAllocator,
    meta_short_circuit: bool,
) -> Finding | None:
    """Promote a result difference only when a descendant effect corroborates it."""
    captured = captured_result.spec_summary
    standard = standard_result.spec_summary
    if (
        captured is None
        or standard is None
        or captured.is_namespace is not True
        or standard.is_namespace is not True
        or not comparison.only_in_right_result
    ):
        return None
    prefix = name + "."
    exact_failure = False
    for search in searches:
        if (
            not search.fullname.startswith(prefix)
            or search.progress != "failed"
            or not any(sequence > winner.sequence for sequence in search.event_seqs)
        ):
            continue
        relative_parts = search.fullname[len(prefix) :].split(".")
        if _omitted_module_candidate(comparison.only_in_right_result, relative_parts) is None:
            continue
        exact_failure = True
    if not exact_failure:
        return None
    signals: list[str] = []
    if meta_short_circuit:
        signals.append("meta_path_short_circuit")
    if structural_comparison.importer_cache_changed_paths:
        signals.append("importer_cache_changed")
    return Finding(
        finding_ids.next_id(),
        "missing_namespace_locations",
        name,
        evidence=FinderComparisonEvidence(
            finder_call=winner,
            result_ids=(captured_result.result_id, standard_result.result_id),
            result_comparison_id=comparison.comparison_id,
            structural_comparison=structural_comparison,
        ),
        evidence_level="correlated",
        limitations=(
            "standard_path_check_uses_report_time_importer_state",
            "standard_path_check_does_not_predict_alternative_winner",
            "standard_path_check_skips_intervening_meta_path_finders",
        ),
        severity="problem",
        signals=tuple(signals),
    )


def _current_state_spec_summary(module: object, observed: ModuleSpecSnapshot) -> ModuleSpecSnapshot:
    """Enrich a deferred package path only when the loaded module retained the same spec."""
    if observed.locations_state != "deferred" or type(module) is not types.ModuleType:
        return observed
    try:
        namespace = types.ModuleType.__getattribute__(module, "__dict__")
        current = namespace.get("__spec__")
        if current is None or id(current) != observed.spec.object_id:
            return observed
        summary, _loader = summarize_spec(current, iterate_foreign_locations=True)
        return summary
    except Exception:
        return observed


def _compare_specs(left: ModuleSpecSnapshot, right: ModuleSpecSnapshot) -> FinderResultComparison:
    """Compatibility helper for focused unit tests of neutral spec comparison."""
    left_result = FinderResult._for_comparison("result:left", left)
    right_result = FinderResult._for_comparison("result:right", right)
    return left_result.compare(
        right_result,
        comparison_id="comparison:test",
        structural_comparison=StructuralComparison(None, None, (), ()),
    )


def _module_replacement_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Return exact object replacements captured across detailed loader boundaries."""
    findings: list[Finding] = []
    previous_exec: dict[str, ImportMechanismCall] = {}
    for event in events:
        if not isinstance(event, ImportMechanismCall) or not event.boundary.startswith("loader_"):
            continue
        before = event.module_state_before
        after = event.module_state_after
        target = event.target_state
        prior = previous_exec.get(event.fullname or "")
        if event.boundary == "loader_exec_module" and event.fullname is not None:
            previous_exec[event.fullname] = event
        prior_state = None if prior is None else (prior.target_state or prior.module_state_after)
        prior_diverged = _prior_loader_target_diverged(event, prior, prior_state, target)
        if event.fullname is None or not _is_module_replacement(event, before, after, target, prior_diverged):
            continue
        findings.append(_module_replacement_finding(event, prior, prior_state, prior_diverged, finding_ids))
    return findings


def _prior_loader_target_diverged(
    event: ImportMechanismCall,
    prior: ImportMechanismCall | None,
    prior_state: ModuleCacheState | None,
    target: ModuleCacheState | None,
) -> bool:
    return bool(
        prior_state is not None
        and prior is not None
        and prior.object_id == event.object_id
        and prior_state.state == "object"
        and target is not None
        and target.state == "object"
        and prior_state.object_id != target.object_id
    )


def _is_module_replacement(
    event: ImportMechanismCall,
    before: ModuleCacheState | None,
    after: ModuleCacheState | None,
    target: ModuleCacheState | None,
    prior_diverged: bool,
) -> bool:
    cache_replaced = (
        before is not None
        and after is not None
        and before.state == "object"
        and after.state == "object"
        and before.object_id != after.object_id
    )
    target_diverged = (
        event.boundary == "loader_exec_module"
        and before is not None
        and before.state == "object"
        and target is not None
        and target.state == "object"
        and before.object_id != target.object_id
    )
    return cache_replaced or target_diverged or prior_diverged


def _module_replacement_finding(
    event: ImportMechanismCall,
    prior: ImportMechanismCall | None,
    prior_state: ModuleCacheState | None,
    prior_diverged: bool,
    finding_ids: _IdAllocator,
) -> Finding:
    assert event.fullname is not None
    kind = "module_executed_again" if prior_diverged else "module_replacement"
    return Finding(
        finding_ids.next_id(),
        kind,
        event.fullname,
        evidence=ImportMechanismEvidence(
            import_mechanism_call=event,
            module_state_baseline=prior_state if prior_diverged else None,
        ),
        supporting_event_seqs=((prior.sequence,) if prior_diverged and prior is not None else ()),
        evidence_level="observed",
        limitations=("boundary_delta_does_not_reconstruct_intermediate_states",),
        severity="problem",
    )


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _cache_event_index(events: list[MonitorEvent]) -> dict[str, tuple[int, ...]]:
    """Index passive cache-diff event sequences by affected path."""
    indexed: dict[str, list[int]] = {}
    for event in events:
        if not isinstance(event, ImporterCacheChange):
            continue
        paths = {entry.path for entry in event.added}
        paths.update(entry.path for entry in event.removed)
        paths.update(replacement.path for replacement in event.replaced)
        for path in paths:
            indexed.setdefault(path, []).append(event.sequence)
    return {path: tuple(seqs) for path, seqs in indexed.items()}


def _cache_signatures(
    entries: tuple[ImporterCacheEntry, ...],
) -> dict[str, tuple[int | None, str | None]]:
    """Reduce one cache snapshot to path and safe finder identity/type data."""
    return {entry.path: _cache_finder_signature(entry.finder) for entry in entries}


def _import_object_signatures(references: tuple[ObjectIdentity, ...]) -> tuple[tuple[int, str], ...]:
    """Reduce safe object references to comparable identity/type tuples."""
    return tuple((reference.object_id, reference.type_name) for reference in references)


def _changed_cache_paths(
    search_path: tuple[str, ...],
    initial: dict[str, tuple[int | None, str | None]],
    current: dict[str, tuple[int | None, str | None]] | None,
) -> tuple[bool | None, tuple[str, ...]]:
    """Compare cache structure only for the captured effective search path."""
    if current is None:
        return None, ()
    changed: list[str] = []
    seen: set[str] = set()
    for path in search_path:
        if path in seen:
            continue
        seen.add(path)
        if (path in initial) != (path in current) or initial.get(path) != current.get(path):
            changed.append(path)
    return bool(changed), tuple(changed)


def _cache_finder_signature(finder: ObjectIdentity | None) -> tuple[int | None, str | None]:
    """Describe a cached finder identity while preserving negative entries."""
    if finder is None:
        return None, None
    return finder.object_id, finder.type_name


def _check_standard_path(
    result_id: str,
    name: str,
    search_path: tuple[str, ...],
    search_path_kind: "SearchPathKind",
    target: types.ModuleType | None,
    target_available: bool,
    source_event_seqs: tuple[int, ...],
) -> FinderResult:
    """Check PathFinder as one result without treating it as the alternative winner."""
    if not target_available:
        return _standard_path_result(
            result_id,
            name,
            search_path,
            search_path_kind,
            "target_unavailable",
            source_event_seqs=source_event_seqs,
        )
    try:
        spec = PathFinder.find_spec(name, search_path, target)
        if spec is None:
            return _standard_path_result(
                result_id, name, search_path, search_path_kind, "not_found", source_event_seqs=source_event_seqs
            )
        summary, _loader = summarize_spec(spec, iterate_foreign_locations=True)
        return _standard_path_result(
            result_id,
            name,
            search_path,
            search_path_kind,
            "found",
            summary,
            source_event_seqs=source_event_seqs,
        )
    except BaseException as exc:  # A broken finder chain must not break the report.
        return _standard_path_result(
            result_id,
            name,
            search_path,
            search_path_kind,
            "failed",
            exception_type_name=type_name(exc),
            source_event_seqs=source_event_seqs,
        )


def _standard_path_result(
    result_id: str,
    name: str,
    search_path: tuple[str, ...],
    search_path_kind: "SearchPathKind",
    status: "FinderResultStatus",
    spec_summary: ModuleSpecSnapshot | None = None,
    exception_type_name: str | None = None,
    source_event_seqs: tuple[int, ...] = (),
) -> FinderResult:
    """Construct one uniformly qualified standard-path check result."""
    return FinderResult(
        result_id=result_id,
        module=name,
        kind="standard_path_check",
        purpose="compare_with_current_pathfinder",
        limitations=(
            "captured_search_path_with_report_time_path_hook_and_cache_state",
            "does_not_predict_alternative_winner",
            "skips_intervening_meta_path_finders",
        ),
        evidence_level="current_state_check",
        state_phase="report",
        predicts_alternative_winner=False,
        finder_type_name="PathFinder",
        finder_id=id(PathFinder),
        status=status,
        spec_summary=spec_summary,
        exception_type_name=exception_type_name,
        source_event_seqs=source_event_seqs,
        search_path=search_path,
        search_path_kind=search_path_kind,
        search_path_phase="import",
    )
