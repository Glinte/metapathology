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
    DeepDiagnosticCall,
    DeepImportEvent,
    FinderContract,
    FindSpecCall,
    ImportAuditStart,
    ImporterCacheDiff,
    ImporterCacheEntry,
    MetaPathMutation,
    MetaPathReassignment,
    ModuleCacheState,
    MonitorEvent,
    ObjectRef,
    PathHooksMutation,
    PathHooksReassignment,
    SpecSummary,
    StandardFinderCall,
    SysPathMutation,
    SysPathReassignment,
    type_name,
)
from metapathology._report_model import (
    CausalExplanation,
    Finding,
    ImportAttempt,
    ReportError,
    ReportSummary,
    ResolutionRoute,
    RouteComparison,
    StandardResolution,
    StructuralComparison,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    from metapathology._records import DeepOutcome, SearchPathKind
    from metapathology._report_model import (
        ImportProgress,
        ResolutionCategory,
        RouteStatus,
    )

    FinderContractCategory = Literal["indeterminate", "legacy_only", "modern", "modern_and_legacy", "protocol_less"]


_MODULE_FILE_SUFFIXES = (".py", ".pyc", ".pyd", ".so")
_STANDARD_CLASS_FINDER_REASON_PREFIX = "standard CPython class finder;"


class _IdAllocator:
    """Hand out ``prefix:N`` document ids in creation order.

    One allocator per element kind (finding, explanation, route, comparison)
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
    """Copied evidence and state shared by the finding/route producers.

    Bundles the report-time inputs so the wide producer entry points take one
    argument instead of a dozen positionals. ``report_errors`` is a live
    accumulator: probes append to it during analysis.
    """

    __slots__ = (
        "attempts",
        "baseline_modules",
        "current_importer_cache",
        "current_path_hooks",
        "events",
        "finder_contracts",
        "initial_importer_cache",
        "initial_path_hooks",
        "module_items",
        "module_metadata",
        "report_errors",
    )

    def __init__(
        self,
        *,
        baseline_modules: frozenset[str],
        events: list[MonitorEvent],
        initial_path_hooks: tuple[ObjectRef, ...],
        current_path_hooks: tuple[ObjectRef, ...] | None,
        initial_importer_cache: tuple[ImporterCacheEntry, ...],
        current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
        module_items: list[tuple[object, object]] | None,
        module_metadata: tuple[ModuleMetadata, ...],
        finder_contracts: list[FinderContract],
        attempts: tuple[ImportAttempt, ...],
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
        self.finder_contracts = finder_contracts
        self.attempts = attempts
        self.report_errors = report_errors


class _StructuralContext:
    """One report's primitive-only index for per-finding comparisons."""

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


_SEVERITY_RANK = {"actionable": 0, "warning": 1, "informational": 2}


def unresolved_attempts(attempts: tuple[ImportAttempt, ...]) -> tuple[ImportAttempt, ...]:
    """Attempts that started, produced no module by report time, and drew no finder claim."""
    return tuple(
        attempt
        for attempt in attempts
        if attempt.presence == "absent_at_report" and attempt.progress in ("started", "failed", "finder_raised")
    )


def _report_summary(
    findings: tuple[Finding, ...],
    explanations: tuple[CausalExplanation, ...],
    attempts: tuple[ImportAttempt, ...],
) -> ReportSummary:
    """Reduce counts and top references; headline prose stays in the text renderer."""
    counts = {"actionable": 0, "warning": 0, "informational": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    top_finding = min(
        (finding for finding in findings if finding.severity != "informational"),
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
        actionable=counts["actionable"],
        warning=counts["warning"],
        informational=counts["informational"],
        unresolved_import_count=len(unresolved_attempts(attempts)),
        top_finding_id=None if top_finding is None else top_finding.finding_id,
        top_explanation_id=None if top_explanation is None else top_explanation.explanation_id,
    )


def _import_attempts(
    events: list[MonitorEvent], module_items: list[tuple[object, object]] | None
) -> tuple[ImportAttempt, ...]:
    """Join calls only to the matching latest audit start on their thread."""
    present = None if module_items is None else {name for name, _module in module_items if type(name) is str}
    starts: dict[int, tuple[str, int, int, str]] = {}
    order: list[int] = []
    latest_by_thread: dict[int, tuple[int, str]] = {}
    linked: dict[int, list[FindSpecCall | DeepDiagnosticCall | DeepImportEvent | StandardFinderCall]] = {}
    for event in events:
        if isinstance(event, DeepImportEvent):
            if event.outcome == "started":
                if event.attempt_id not in starts:
                    starts[event.attempt_id] = (event.fullname, event.seq, event.thread_id, event.thread_name)
                    order.append(event.attempt_id)
                    linked[event.attempt_id] = []
                latest_by_thread[event.thread_id] = (event.attempt_id, event.fullname)
                linked[event.attempt_id].append(event)
            elif event.attempt_id in linked:
                linked[event.attempt_id].append(event)
        elif isinstance(event, ImportAuditStart):
            if event.attempt_id not in starts:
                starts[event.attempt_id] = (event.fullname, event.seq, event.thread_id, event.thread_name)
                order.append(event.attempt_id)
                linked[event.attempt_id] = []
            else:
                fullname, _seq, thread_id, thread_name = starts[event.attempt_id]
                starts[event.attempt_id] = (fullname, event.seq, thread_id, thread_name)
            latest_by_thread[event.thread_id] = (event.attempt_id, event.fullname)
        elif isinstance(event, (FindSpecCall, DeepDiagnosticCall)):
            latest = latest_by_thread.get(event.thread_id)
            if latest is not None and event.fullname == latest[1]:
                linked[latest[0]].append(event)
        elif isinstance(event, StandardFinderCall) and event.attempt_id in linked:
            linked[event.attempt_id].append(event)
    attempts: list[ImportAttempt] = []
    for attempt_id in order:
        fullname, start_seq, thread_id, thread_name = starts[attempt_id]
        evidence = linked[attempt_id]
        exact: DeepOutcome | None = None
        for event in reversed(evidence):
            if isinstance(event, DeepImportEvent) and event.outcome != "started":
                exact = event.outcome
                break
        claimed = any(isinstance(event, FindSpecCall) and event.found for event in evidence)
        raised = any(isinstance(event, FindSpecCall) and event.exception_type_name is not None for event in evidence)
        progress: ImportProgress
        if exact is not None:
            progress = exact
        elif claimed and raised:
            progress = "unknown"
        elif claimed:
            progress = "finder_claimed"
        elif raised:
            progress = "finder_raised"
        else:
            progress = "started"
        presence = "unknown" if present is None else "present_at_report" if fullname in present else "absent_at_report"
        attempts.append(
            ImportAttempt(
                attempt_id,
                fullname,
                start_seq,
                tuple(event.seq for event in evidence if event.seq != start_seq),
                thread_id,
                thread_name,
                progress,
                presence,
            )
        )
    return tuple(attempts)


def _standard_resolutions(
    events: list[MonitorEvent],
    attempts: tuple[ImportAttempt, ...],
    inventory: tuple[ModuleMetadata, ...],
) -> tuple[StandardResolution, ...]:
    """Join exact aggregate results or conservative post-hoc standard evidence."""
    by_seq = {event.seq: event for event in events}
    audits = {event.attempt_id: event for event in events if isinstance(event, ImportAuditStart)}
    captured = {event.attempt_id: event for event in events if isinstance(event, StandardFinderCall)}
    metadata = {entry.name: entry for entry in inventory}
    resolutions: list[StandardResolution] = []
    for attempt in attempts:
        audit = audits.get(attempt.attempt_id)
        aggregate = captured.get(attempt.attempt_id)
        summary = aggregate.spec_summary if aggregate is not None else None
        if summary is None:
            entry = metadata.get(attempt.fullname)
            if entry is None or entry.inspection != "available":
                continue
            summary = entry.spec_summary
        classified = _standard_spec_classification(summary)
        if classified is None:
            continue
        finder_type_name, category, loader_type_name, origin = classified
        event_values = tuple(by_seq[seq] for seq in attempt.event_seqs if seq in by_seq)
        if aggregate is None and any(isinstance(event, FindSpecCall) and event.found for event in event_values):
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
            event.seq
            for event in event_values
            if isinstance(event, DeepDiagnosticCall)
            and event.boundary == "path_entry_finder"
            and event.fullname == attempt.fullname
        )
        resolutions.append(
            StandardResolution(
                attempt_id=attempt.attempt_id,
                fullname=attempt.fullname,
                finder_type_name=finder_type_name,
                category=category,
                loader_type_name=loader_type_name,
                origin=origin,
                evidence_level="captured" if aggregate is not None else "inferred",
                state_phase="import" if aggregate is not None else "report",
                event_seq=None if aggregate is None else aggregate.seq,
                component_event_seqs=components,
                later_finders=later_finders,
            )
        )
    return tuple(resolutions)


def _standard_spec_classification(
    summary: SpecSummary | None,
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
    route_ids: _IdAllocator,
    comparison_ids: _IdAllocator,
) -> tuple[tuple[Finding, ...], tuple[ResolutionRoute, ...], tuple[RouteComparison, ...]]:
    """Build neutral resolution routes, then promote only corroborated effects."""
    baseline_modules = inputs.baseline_modules
    events = inputs.events
    initial_path_hooks = inputs.initial_path_hooks
    current_path_hooks = inputs.current_path_hooks
    initial_importer_cache = inputs.initial_importer_cache
    current_importer_cache = inputs.current_importer_cache
    module_items = inputs.module_items
    module_metadata = inputs.module_metadata
    finder_contracts = inputs.finder_contracts
    attempts = inputs.attempts
    report_errors = inputs.report_errors
    winners = {event.fullname: event for event in events if isinstance(event, FindSpecCall) and event.found}
    structural_context = _structural_context(
        events,
        initial_path_hooks,
        current_path_hooks,
        initial_importer_cache,
        current_importer_cache,
    )
    meta_short_circuit_seqs = _meta_short_circuit_claim_seqs(events)
    findings = _finder_contract_findings(finder_contracts, finding_ids)
    findings.extend(_finder_side_effect_findings(events, finding_ids))
    findings.extend(_module_replacement_findings(events, finding_ids))
    routes: list[ResolutionRoute] = []
    comparisons: list[RouteComparison] = []
    if module_items is None:
        findings.extend(_path_hook_shadow_findings(events, finding_ids))
        findings.extend(_failed_after_mutation_findings(events, finding_ids))
        return tuple(findings), (), ()
    metadata_by_name = {entry.name: entry for entry in module_metadata}
    for name, module in module_items:
        if type(name) is not str or name in baseline_modules or name == "__main__":
            continue
        winner = winners.get(name)
        if winner is not None:
            captured_route, standard_route, comparison, structural_comparison = _resolution_routes(
                name,
                module,
                winner,
                route_ids,
                comparison_ids,
                structural_context,
                winner.seq in meta_short_circuit_seqs,
                report_errors,
            )
            routes.extend((captured_route, standard_route))
            comparisons.append(comparison)
            finding = _corroborated_route_finding(
                name,
                winner,
                captured_route,
                standard_route,
                comparison,
                structural_comparison,
                attempts,
                finding_ids,
                winner.seq in meta_short_circuit_seqs,
            )
            if finding is not None:
                findings.append(finding)
            continue
        metadata = metadata_by_name.get(name)
        if metadata is not None and metadata.inspection == "available" and metadata.spec_is_none:
            findings.append(
                Finding(
                    finding_ids.next_id(),
                    "no_spec",
                    name,
                    evidence_level="post_hoc",
                    limitations=("report_snapshot_cannot_identify_load_mechanism",),
                    severity="informational",
                )
            )
    findings.extend(_path_hook_shadow_findings(events, finding_ids))
    findings.extend(_failed_after_mutation_findings(events, finding_ids))
    return tuple(findings), tuple(routes), tuple(comparisons)


def _finder_contract_category(contract: FinderContract) -> "FinderContractCategory":
    """Summarize the two independently inspected protocol states."""
    modern = contract.find_spec.availability
    legacy = contract.find_module.availability
    if "indeterminate" in (modern, legacy):
        return "indeterminate"
    if modern == "callable":
        return "modern_and_legacy" if legacy == "callable" else "modern"
    return "legacy_only" if legacy == "callable" else "protocol_less"


def _finder_contract_findings(contracts: list[FinderContract], finding_ids: _IdAllocator) -> list[Finding]:
    """Expose nonstandard legacy-only contracts as actionable findings."""
    findings: list[Finding] = []
    for contract in contracts:
        if _finder_contract_category(contract) != "legacy_only":
            continue
        findings.append(
            Finding(
                finding_ids.next_id(),
                "legacy_finder_contract",
                contract.finder_type_name,
                evidence_level="captured",
                limitations=("protocols_were_inspected_not_invoked",),
                subject_kind="finder",
                finder_contract=contract,
            )
        )
    return findings


def _causal_explanations(
    findings: tuple[Finding, ...],
    attempts: tuple[ImportAttempt, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    route_comparisons: tuple[RouteComparison, ...],
    explanation_ids: _IdAllocator,
    events: list[MonitorEvent] | None = None,
) -> tuple[CausalExplanation, ...]:
    """Join namespace loss to descendant attempts and report-time path evidence."""
    explanations: list[CausalExplanation] = []
    events_by_seq = {} if events is None else {event.seq: event for event in events}
    comparisons_by_id = {comparison.comparison_id: comparison for comparison in route_comparisons}
    displacement_findings = {
        finding.module: finding for finding in findings if finding.kind == "regular_module_shadows_namespace"
    }
    for finding in findings:
        comparison = None if finding.route_comparison_id is None else comparisons_by_id.get(finding.route_comparison_id)
        if finding.kind != "namespace_truncation" or comparison is None or finding.claim is None:
            continue
        prefix = finding.module + "."
        descendant_groups: dict[str, list[ImportAttempt]] = {}
        for attempt in attempts:
            if attempt.fullname.startswith(prefix) and attempt.presence != "present_at_report":
                descendant_groups.setdefault(attempt.fullname, []).append(attempt)
        descendants = [
            next((attempt for attempt in group if attempt.progress == "failed"), group[0])
            for group in descendant_groups.values()
            if not any(attempt.progress == "loaded" for attempt in group)
        ]
        for attempt in descendants:
            relative_parts = attempt.fullname[len(prefix) :].split(".")
            if attempt.progress != "failed" or not any(seq > finding.claim.seq for seq in attempt.event_seqs):
                continue
            match = _omitted_module_candidate(comparison.only_in_right_route, relative_parts)
            if match is None:
                continue
            omitted_location, candidate_path = match
            event_seqs = (
                finding.claim.seq,
                *(seq for seq in attempt.event_seqs if seq > finding.claim.seq),
            )
            explanations.append(
                CausalExplanation(
                    explanation_id=explanation_ids.next_id(),
                    kind="namespace_truncation_failure",
                    confidence="correlated",
                    subject=attempt.fullname,
                    effect_status="failed",
                    cause_finding_id=finding.finding_id,
                    finder_type_name=finding.claim.finder_type_name,
                    omitted_location=omitted_location,
                    candidate_path=candidate_path,
                    event_seqs=tuple(dict.fromkeys(event_seqs)),
                    next_observation=None,
                )
            )
    for resolution in standard_resolutions:
        if resolution.category != "namespace" and resolution.origin is not None:
            components = [
                event
                for seq in resolution.component_event_seqs
                if isinstance((event := events_by_seq.get(seq)), DeepDiagnosticCall)
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
            if selected_index is not None and selected_index > 0:
                candidate = components[0]
                selected = components[selected_index]
                displacement_finding = displacement_findings.get(resolution.fullname)
                descendants = () if displacement_finding is None else _failed_descendants_after(resolution, attempts)
                subjects: tuple[ImportAttempt | None, ...] = descendants or (None,)
                for descendant in subjects:
                    event_seqs = tuple(
                        dict.fromkeys(
                            (
                                *((resolution.event_seq,) if resolution.event_seq is not None else ()),
                                candidate.seq,
                                selected.seq,
                                *(() if descendant is None else descendant.event_seqs),
                            )
                        )
                    )
                    explanations.append(
                        CausalExplanation(
                            explanation_id=explanation_ids.next_id(),
                            kind="namespace_candidate_displaced",
                            confidence="captured" if descendant is None else "correlated",
                            subject=resolution.fullname if descendant is None else descendant.fullname,
                            effect_status="regular_module_selected" if descendant is None else "descendant_failed",
                            cause_finding_id=(
                                None if displacement_finding is None else displacement_finding.finding_id
                            ),
                            finder_type_name=resolution.finder_type_name,
                            omitted_location="",
                            candidate_path=candidate.path or "",
                            event_seqs=event_seqs,
                            next_observation=None,
                            origin=resolution.origin,
                        )
                    )
        if not resolution.later_finders:
            continue
        event_seqs = (() if resolution.event_seq is None else (resolution.event_seq,)) + resolution.component_event_seqs
        explanations.append(
            CausalExplanation(
                explanation_id=explanation_ids.next_id(),
                kind="standard_winner_precedence",
                confidence="captured" if resolution.evidence_level == "captured" else "inferred",
                subject=resolution.fullname,
                effect_status=resolution.category,
                cause_finding_id=None,
                finder_type_name=resolution.finder_type_name,
                omitted_location="",
                candidate_path="",
                event_seqs=event_seqs,
                next_observation=(None if resolution.evidence_level == "captured" else "enable_deep_import_outcomes"),
                origin=resolution.origin,
                standard_attempt_id=resolution.attempt_id,
                later_finders=resolution.later_finders,
            )
        )
    for finding in findings:
        if finding.kind == "finder_side_effect" and finding.claim is not None:
            claim = finding.claim
            outcome = "finder_raised" if claim.exception_type_name is not None else "finder_returned_none"
            explanations.append(
                CausalExplanation(
                    explanation_id=explanation_ids.next_id(),
                    kind="finder_side_effect",
                    confidence="captured",
                    subject=finding.module,
                    effect_status=outcome,
                    cause_finding_id=finding.finding_id,
                    finder_type_name=claim.finder_type_name,
                    omitted_location="",
                    candidate_path="",
                    event_seqs=(claim.seq,),
                    next_observation=None,
                    boundary="find_spec",
                    state_before=claim.module_state_before,
                    state_after=claim.module_state_after,
                )
            )
        elif finding.kind in ("module_replacement", "repeated_loader_execution") and finding.deep_call is not None:
            call = finding.deep_call
            baseline = finding.module_state_baseline or call.module_state_before
            target_diverged = (
                baseline is not None
                and baseline.state == "object"
                and call.target_state is not None
                and call.target_state.state == "object"
                and baseline.object_id != call.target_state.object_id
                and (
                    finding.module_state_baseline is not None
                    or not (
                        call.module_state_after is not None
                        and call.module_state_after.state == "object"
                        and baseline.object_id != call.module_state_after.object_id
                    )
                )
            )
            explanations.append(
                CausalExplanation(
                    explanation_id=explanation_ids.next_id(),
                    kind=finding.kind,
                    confidence="captured",
                    subject=finding.module,
                    effect_status="separate_module_executed" if target_diverged else "module_identity_replaced",
                    cause_finding_id=finding.finding_id,
                    finder_type_name=call.object_type_name,
                    omitted_location="",
                    candidate_path="",
                    event_seqs=tuple(dict.fromkeys((*finding.supporting_event_seqs, call.seq))),
                    next_observation=None,
                    boundary=call.boundary,
                    state_before=baseline,
                    state_after=call.target_state if target_diverged else call.module_state_after,
                )
            )
        elif finding.kind == "repeated_load_failure":
            matched = [
                resolution
                for resolution in standard_resolutions
                if resolution.fullname == finding.module
                and resolution.event_seq is not None
                and resolution.event_seq in finding.supporting_event_seqs
            ]
            if len(matched) < 2:
                continue
            later = matched[-1]
            explanations.append(
                CausalExplanation(
                    explanation_id=explanation_ids.next_id(),
                    kind="repeated_load_failure",
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
                    standard_attempt_id=later.attempt_id,
                )
            )
    return _append_ambiguous_explanations(tuple(explanations), explanation_ids)


def _failed_descendants_after(
    resolution: StandardResolution,
    attempts: tuple[ImportAttempt, ...],
) -> tuple[ImportAttempt, ...]:
    """Return every exact descendant failure after a parent resolution."""
    boundary = resolution.event_seq
    if boundary is None:
        return ()
    prefix = resolution.fullname + "."
    return tuple(
        attempt
        for attempt in attempts
        if attempt.fullname.startswith(prefix)
        and attempt.progress == "failed"
        and any(seq > boundary for seq in attempt.event_seqs)
    )


def _namespace_displacement_findings(
    attempts: tuple[ImportAttempt, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    events: list[MonitorEvent],
    finding_ids: _IdAllocator,
) -> tuple[Finding, ...]:
    """Promote a displaced namespace only when a descendant then fails."""
    events_by_seq = {event.seq: event for event in events}
    findings: list[Finding] = []
    for resolution in standard_resolutions:
        if resolution.category == "namespace" or resolution.origin is None:
            continue
        components = [
            event
            for seq in resolution.component_event_seqs
            if isinstance((event := events_by_seq.get(seq)), DeepDiagnosticCall)
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
        descendants = _failed_descendants_after(resolution, attempts)
        if selected_index is None or selected_index == 0 or not descendants:
            continue
        candidate = components[0]
        selected = components[selected_index]
        event_seqs = tuple(
            dict.fromkeys(
                (
                    *((resolution.event_seq,) if resolution.event_seq is not None else ()),
                    candidate.seq,
                    selected.seq,
                    *(seq for descendant in descendants for seq in descendant.event_seqs),
                )
            )
        )
        findings.append(
            Finding(
                finding_ids.next_id(),
                "regular_module_shadows_namespace",
                resolution.fullname,
                evidence_level="correlated",
                limitations=("exception_message_not_captured",),
                attempt_ids=tuple(descendant.attempt_id for descendant in descendants),
                supporting_event_seqs=event_seqs,
                severity="actionable",
                signals=("namespace_candidate_found", "regular_module_selected", "descendant_failed"),
            )
        )
    return tuple(findings)


def _repeated_load_failure_findings(
    attempts: tuple[ImportAttempt, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    finding_ids: _IdAllocator,
) -> tuple[Finding, ...]:
    """Correlate a later failure with an earlier load from the same origin."""
    attempts_by_id = {attempt.attempt_id: attempt for attempt in attempts}
    groups: dict[tuple[str, str, str], list[tuple[StandardResolution, ImportAttempt]]] = {}
    findings: list[Finding] = []
    ordered = sorted(
        standard_resolutions,
        key=lambda resolution: -1 if resolution.event_seq is None else resolution.event_seq,
    )
    for resolution in ordered:
        if (
            resolution.evidence_level != "captured"
            or resolution.origin is None
            or resolution.loader_type_name is None
            or resolution.event_seq is None
        ):
            continue
        attempt = attempts_by_id.get(resolution.attempt_id)
        if attempt is None:
            continue
        if attempt.progress in ("loaded", "failed"):
            key = (resolution.fullname, resolution.loader_type_name, _path_key(resolution.origin))
            groups.setdefault(key, []).append((resolution, attempt))
    candidates: list[
        tuple[
            int,
            StandardResolution,
            ImportAttempt,
            list[tuple[StandardResolution, ImportAttempt]],
        ]
    ] = []
    for group in groups.values():
        earlier_pair = next((pair for pair in group if pair[1].progress == "loaded"), None)
        if earlier_pair is None:
            continue
        earlier, earlier_attempt = earlier_pair
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
            max(attempt.event_seqs, default=resolution.event_seq or 0) for resolution, attempt in failures
        )
        candidates.append((first_failure_seq, earlier, earlier_attempt, failures))
    for _failure_seq, earlier, earlier_attempt, failures in sorted(candidates, key=lambda item: item[0]):
        earlier_event_seq = earlier.event_seq
        if earlier_event_seq is None:
            continue
        failed_attempts = [
            attempt
            for attempt in attempts
            if attempt.fullname == earlier.fullname
            and attempt.progress == "failed"
            and any(seq > earlier_event_seq for seq in attempt.event_seqs)
        ]
        event_seqs = tuple(
            dict.fromkeys(
                (
                    earlier_event_seq,
                    *earlier_attempt.event_seqs,
                    *(resolution.event_seq for resolution, _attempt in failures if resolution.event_seq is not None),
                    *(seq for attempt in failed_attempts for seq in attempt.event_seqs),
                )
            )
        )
        findings.append(
            Finding(
                finding_ids.next_id(),
                "repeated_load_failure",
                earlier.fullname,
                evidence_level="correlated",
                limitations=("exception_message_not_captured",),
                attempt_ids=(
                    earlier_attempt.attempt_id,
                    *(attempt.attempt_id for attempt in failed_attempts),
                ),
                supporting_event_seqs=event_seqs,
                severity="actionable",
                signals=("same_loader", "same_origin", "earlier_attempt_loaded", "later_attempt_failed"),
            )
        )
    return tuple(findings)


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


def _meta_short_circuit_claim_seqs(events: list[MonitorEvent]) -> set[int]:
    """Identify claims captured before the import-time PathFinder position."""
    latest_audit: dict[tuple[int, str], ImportAuditStart] = {}
    claim_seqs: set[int] = set()
    for event in events:
        if isinstance(event, ImportAuditStart):
            latest_audit[(event.thread_id, event.fullname)] = event
            continue
        if not isinstance(event, FindSpecCall) or not event.found:
            continue
        audit = latest_audit.get((event.thread_id, event.fullname))
        if audit is None or audit.seq >= event.seq or "PathFinder" not in audit.meta_path_type_names:
            continue
        try:
            custom_index = audit.meta_path_type_names.index(event.finder_type_name)
            path_finder_index = audit.meta_path_type_names.index("PathFinder")
        except ValueError:
            continue
        if custom_index >= path_finder_index:
            continue
        claim_seqs.add(event.seq)
    return claim_seqs


def _path_hook_shadow_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Report paths accepted by distinct hooks across captured deep calls."""
    accepted: dict[str, DeepDiagnosticCall] = {}
    findings: list[Finding] = []
    emitted: set[tuple[str, int, int]] = set()
    for event in events:
        if (
            not isinstance(event, DeepDiagnosticCall)
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
                "path_hook_shadow",
                event.path,
                deep_call=event,
                evidence_level="structural_inference",
                limitations=("acceptance_was_captured_across_distinct_resolution_states",),
                subject_kind="path",
                supporting_event_seqs=(earlier.seq,),
            )
        )
    return findings


def _failed_after_mutation_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Pair failures only with structural mutations captured inside that attempt."""
    mutation_seqs: list[int] = []
    started: dict[int, DeepImportEvent] = {}
    findings: list[Finding] = []
    for event in events:
        if isinstance(
            event,
            (
                MetaPathMutation,
                MetaPathReassignment,
                PathHooksMutation,
                PathHooksReassignment,
                SysPathMutation,
                SysPathReassignment,
                ImporterCacheDiff,
            ),
        ):
            mutation_seqs.append(event.seq)
            continue
        if not isinstance(event, DeepImportEvent):
            continue
        if event.outcome == "started":
            started[event.attempt_id] = event
            continue
        if event.outcome != "failed":
            continue
        start = started.get(event.attempt_id)
        if start is None:
            continue
        mutation_seq = next((seq for seq in reversed(mutation_seqs) if start.seq < seq < event.seq), None)
        if mutation_seq is None:
            continue
        findings.append(
            Finding(
                finding_ids.next_id(),
                "failed_after_mutation",
                event.fullname,
                evidence_level="structural_inference",
                limitations=("temporal_correlation_does_not_prove_mutation_caused_failure",),
                supporting_event_seqs=(mutation_seq, start.seq, event.seq),
            )
        )
    return findings


def _finder_side_effect_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Return captured target-cache changes made by passing or raising finders."""
    findings: list[Finding] = []
    for event in events:
        if not isinstance(event, FindSpecCall) or event.found:
            continue
        before = event.module_state_before
        after = event.module_state_after
        if before is None or after is None or not _module_state_changed(before, after):
            continue
        findings.append(
            Finding(
                finding_ids.next_id(),
                "finder_side_effect",
                event.fullname,
                event,
                evidence_level="captured",
                limitations=("boundary_delta_does_not_identify_nested_cause",),
            )
        )
    return findings


def _module_state_changed(before: ModuleCacheState, after: ModuleCacheState) -> bool:
    """Compare available identity states without conflating unavailable evidence."""
    if before.state == "unavailable" or after.state == "unavailable":
        return False
    return before.state != after.state or before.object_id != after.object_id or before.type_name != after.type_name


def _resolution_routes(
    name: str,
    module: object,
    winner: FindSpecCall,
    route_ids: _IdAllocator,
    comparison_ids: _IdAllocator,
    structural_context: _StructuralContext,
    meta_short_circuit: bool,
    report_errors: list[ReportError],
) -> tuple[ResolutionRoute, ResolutionRoute, RouteComparison, StructuralComparison]:
    """Describe a captured claim and an independent standard-path probe."""
    observed = winner.spec_summary
    if observed is not None:
        observed = _post_hoc_spec_summary(module, observed)
    structural_comparison = _structural_comparison(winner.search_path, structural_context)
    captured_route = ResolutionRoute(
        route_id=route_ids.next_id(),
        module=name,
        kind="captured_claim",
        purpose="record_selected_custom_meta_path_route",
        limitations=(),
        evidence_level="captured",
        state_phase="import",
        predicts_alternative_winner=False,
        finder_type_name=winner.finder_type_name,
        finder_id=winner.finder_id,
        status="found",
        spec_summary=observed,
        exception_type_name=None,
        event_seq=winner.seq,
        search_path=winner.search_path,
        search_path_kind=winner.search_path_kind,
        search_path_phase="import",
        signals=("meta_path_short_circuit",) if meta_short_circuit else (),
    )
    target: types.ModuleType | None = None
    target_available = winner.target_state is None
    if (
        winner.target_state is not None
        and type(module) is types.ModuleType
        and id(module) == winner.target_state.object_id
    ):
        target = module
        target_available = True
    standard_route = _probe_standard_path(
        route_ids.next_id(),
        name,
        winner.search_path,
        winner.search_path_kind,
        target,
        target_available,
    )
    if standard_route.status == "failed":
        report_errors.append(ReportError("standard_path_probe", standard_route.exception_type_name or "Exception"))
    comparison = _compare_routes(
        comparison_ids.next_id(),
        captured_route.route_id,
        standard_route.route_id,
        observed,
        standard_route.spec_summary,
        structural_comparison,
        left_status=captured_route.status,
        right_status=standard_route.status,
    )
    return captured_route, standard_route, comparison, structural_comparison


def _corroborated_route_finding(
    name: str,
    winner: FindSpecCall,
    captured_route: ResolutionRoute,
    standard_route: ResolutionRoute,
    comparison: RouteComparison,
    structural_comparison: StructuralComparison,
    attempts: tuple[ImportAttempt, ...],
    finding_ids: _IdAllocator,
    meta_short_circuit: bool,
) -> Finding | None:
    """Promote a route difference only when a descendant effect corroborates it."""
    captured = captured_route.spec_summary
    standard = standard_route.spec_summary
    if (
        captured is None
        or standard is None
        or captured.is_namespace is not True
        or standard.is_namespace is not True
        or not comparison.only_in_right_route
    ):
        return None
    prefix = name + "."
    exact_failure = False
    for attempt in attempts:
        if (
            not attempt.fullname.startswith(prefix)
            or attempt.progress != "failed"
            or not any(seq > winner.seq for seq in attempt.event_seqs)
        ):
            continue
        relative_parts = attempt.fullname[len(prefix) :].split(".")
        if _omitted_module_candidate(comparison.only_in_right_route, relative_parts) is None:
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
        "namespace_truncation",
        name,
        winner,
        route_ids=(captured_route.route_id, standard_route.route_id),
        route_comparison_id=comparison.comparison_id,
        structural_comparison=structural_comparison,
        evidence_level="correlated",
        limitations=(
            "standard_path_probe_uses_report_time_importer_state",
            "standard_path_probe_does_not_predict_alternative_winner",
            "standard_path_probe_skips_intervening_meta_path_finders",
        ),
        severity="actionable",
        signals=tuple(signals),
    )


def _post_hoc_spec_summary(module: object, observed: SpecSummary) -> SpecSummary:
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


def _compare_routes(
    comparison_id: str,
    left_route_id: str,
    right_route_id: str,
    left: SpecSummary | None,
    right: SpecSummary | None,
    structural_comparison: StructuralComparison,
    *,
    left_status: str = "found",
    right_status: str = "found",
) -> RouteComparison:
    """Compare two independent routes without assigning either one authority."""
    if left is None or right is None:
        return RouteComparison(
            comparison_id,
            left_route_id,
            right_route_id,
            left_status != right_status,
            False,
            None,
            None,
            None,
            None,
            (),
            (),
            None,
            "unavailable" if left is None else left.locations_state,
            "unavailable" if right is None else right.locations_state,
            structural_comparison,
        )
    loader_differs = (
        None
        if "loader:missing" in left.unavailable_fields or "loader:missing" in right.unavailable_fields
        else _loader_type(left) != _loader_type(right)
    )
    origin_differs = _safe_path_value_changed(left.origin, right.origin)
    cached_differs = _safe_path_value_changed(left.cached, right.cached)
    package_differs = (
        None if left.is_package is None or right.is_package is None else left.is_package != right.is_package
    )
    left_locations = _string_locations(left)
    right_locations = _string_locations(right)
    only_in_right: tuple[str, ...] = ()
    only_in_left: tuple[str, ...] = ()
    reordered: bool | None = None
    if left_locations is not None and right_locations is not None:
        left_keys = tuple(_path_key(path) for path in left_locations)
        right_keys = tuple(_path_key(path) for path in right_locations)
        only_in_right, only_in_left = _location_delta(left_locations, left_keys, right_locations, right_keys)
        reordered = not only_in_right and not only_in_left and left_keys != right_keys
    locations_complete = not (left.is_package is True and left.locations_state in ("deferred", "failed")) and not (
        right.is_package is True and right.locations_state in ("deferred", "failed")
    )
    complete = not left.unavailable_fields and not right.unavailable_fields and locations_complete
    return RouteComparison(
        comparison_id,
        left_route_id,
        right_route_id,
        left_status != right_status,
        complete,
        loader_differs,
        origin_differs,
        cached_differs,
        package_differs,
        only_in_left,
        only_in_right,
        reordered,
        left.locations_state,
        right.locations_state,
        structural_comparison,
    )


def _compare_specs(left: SpecSummary, right: SpecSummary) -> RouteComparison:
    """Compatibility helper for focused unit tests of neutral spec comparison."""
    return _compare_routes(
        "comparison:test",
        "route:left",
        "route:right",
        left,
        right,
        StructuralComparison(None, None, (), ()),
    )


def _module_replacement_findings(events: list[MonitorEvent], finding_ids: _IdAllocator) -> list[Finding]:
    """Return exact object replacements captured across deep loader boundaries."""
    findings: list[Finding] = []
    previous_exec: dict[str, DeepDiagnosticCall] = {}
    for event in events:
        if not isinstance(event, DeepDiagnosticCall) or not event.boundary.startswith("loader_"):
            continue
        before = event.module_state_before
        after = event.module_state_after
        target = event.target_state
        prior = previous_exec.get(event.fullname or "")
        if event.boundary == "loader_exec_module" and event.fullname is not None:
            previous_exec[event.fullname] = event
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
        prior_state = None if prior is None else (prior.target_state or prior.module_state_after)
        prior_diverged = (
            prior_state is not None
            and prior is not None
            and prior.object_id == event.object_id
            and prior_state.state == "object"
            and target is not None
            and target.state == "object"
            and prior_state.object_id != target.object_id
        )
        if event.fullname is None or not (cache_replaced or target_diverged or prior_diverged):
            continue
        kind = "repeated_loader_execution" if prior_diverged else "module_replacement"
        findings.append(
            Finding(
                finding_ids.next_id(),
                kind,
                event.fullname,
                deep_call=event,
                module_state_baseline=prior_state if prior_diverged else None,
                supporting_event_seqs=((prior.seq,) if prior_diverged and prior is not None else ()),
                evidence_level="captured",
                limitations=("boundary_delta_does_not_reconstruct_intermediate_states",),
                severity="actionable",
            )
        )
    return findings


def _location_delta(
    observed: tuple[str, ...],
    observed_keys: tuple[str, ...],
    replayed: tuple[str, ...],
    replayed_keys: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return multiset differences while preserving each side's display order."""
    unmatched_observed = list(zip(observed_keys, observed, strict=True))
    omitted: list[str] = []
    for replayed_key, replayed_path in zip(replayed_keys, replayed, strict=True):
        for index, (observed_key, _observed_path) in enumerate(unmatched_observed):
            if replayed_key == observed_key:
                del unmatched_observed[index]
                break
        else:
            omitted.append(replayed_path)
    return tuple(omitted), tuple(path for _key, path in unmatched_observed)


def _loader_type(summary: SpecSummary) -> str | None:
    return None if summary.loader is None else summary.loader.type_name


def _safe_path_value_changed(
    observed: str | ObjectRef | None,
    replayed: str | ObjectRef | None,
) -> bool | None:
    if type(observed) is str and type(replayed) is str:
        return not _same_path(observed, replayed)
    if observed is None and replayed is None:
        return False
    if isinstance(observed, ObjectRef) or isinstance(replayed, ObjectRef):
        return None
    return True


def _string_locations(summary: SpecSummary) -> tuple[str, ...] | None:
    locations = summary.submodule_search_locations
    if locations is None or any(type(location) is not str for location in locations):
        return None
    return tuple(location for location in locations if type(location) is str)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _structural_context(
    events: list[MonitorEvent],
    initial_path_hooks: tuple[ObjectRef, ...],
    current_path_hooks: tuple[ObjectRef, ...] | None,
    initial_importer_cache: tuple[ImporterCacheEntry, ...],
    current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
) -> _StructuralContext:
    """Index captured structure once so per-finding analysis remains linear."""
    path_hooks_changed = (
        None
        if current_path_hooks is None
        else _import_object_signatures(initial_path_hooks) != _import_object_signatures(current_path_hooks)
    )
    return _StructuralContext(
        path_hooks_changed,
        _cache_signatures(initial_importer_cache),
        None if current_importer_cache is None else _cache_signatures(current_importer_cache),
        _cache_event_index(events),
    )


def _structural_comparison(
    search_path: tuple[str, ...],
    context: _StructuralContext,
) -> StructuralComparison:
    """Compare captured identities without calling historical import objects."""
    importer_cache_changed, changed_paths = _changed_cache_paths(
        search_path,
        context.initial_importer_cache,
        context.current_importer_cache,
    )
    event_seqs = tuple(sorted({seq for path in search_path for seq in context.cache_event_seqs_by_path.get(path, ())}))
    return StructuralComparison(
        context.path_hooks_changed,
        importer_cache_changed,
        changed_paths,
        event_seqs,
    )


def _cache_event_index(events: list[MonitorEvent]) -> dict[str, tuple[int, ...]]:
    """Index passive cache-diff event sequences by affected path."""
    indexed: dict[str, list[int]] = {}
    for event in events:
        if not isinstance(event, ImporterCacheDiff):
            continue
        paths = {entry.path for entry in event.added}
        paths.update(entry.path for entry in event.removed)
        paths.update(replacement.path for replacement in event.replaced)
        for path in paths:
            indexed.setdefault(path, []).append(event.seq)
    return {path: tuple(seqs) for path, seqs in indexed.items()}


def _cache_signatures(
    entries: tuple[ImporterCacheEntry, ...],
) -> dict[str, tuple[int | None, str | None]]:
    """Reduce one cache snapshot to path and safe finder identity/type data."""
    return {entry.path: _cache_finder_signature(entry.finder) for entry in entries}


def _import_object_signatures(references: tuple[ObjectRef, ...]) -> tuple[tuple[int, str], ...]:
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


def _cache_finder_signature(finder: ObjectRef | None) -> tuple[int | None, str | None]:
    """Describe a cached finder identity while preserving negative entries."""
    if finder is None:
        return None, None
    return finder.object_id, finder.type_name


def _probe_standard_path(
    route_id: str,
    name: str,
    search_path: tuple[str, ...],
    search_path_kind: "SearchPathKind",
    target: types.ModuleType | None,
    target_available: bool,
) -> ResolutionRoute:
    """Probe PathFinder as one route without treating it as the alternative winner."""
    if not target_available:
        return _standard_path_route(
            route_id,
            name,
            search_path,
            search_path_kind,
            "target_unavailable",
        )
    try:
        spec = PathFinder.find_spec(name, search_path, target)
        if spec is None:
            return _standard_path_route(route_id, name, search_path, search_path_kind, "not_found")
        summary, _loader = summarize_spec(spec, iterate_foreign_locations=True)
        return _standard_path_route(route_id, name, search_path, search_path_kind, "found", summary)
    except BaseException as exc:  # A broken finder chain must not break the report.
        return _standard_path_route(
            route_id,
            name,
            search_path,
            search_path_kind,
            "failed",
            exception_type_name=type_name(exc),
        )


def _standard_path_route(
    route_id: str,
    name: str,
    search_path: tuple[str, ...],
    search_path_kind: "SearchPathKind",
    status: "RouteStatus",
    spec_summary: SpecSummary | None = None,
    exception_type_name: str | None = None,
) -> ResolutionRoute:
    """Construct one uniformly qualified standard-path probe result."""
    return ResolutionRoute(
        route_id=route_id,
        module=name,
        kind="standard_path_probe",
        purpose="show_standard_path_route_bypassed_by_captured_claim",
        limitations=(
            "captured_search_path_with_report_time_path_hook_and_cache_state",
            "does_not_predict_alternative_winner",
            "skips_intervening_meta_path_finders",
        ),
        evidence_level="live_probe",
        state_phase="report",
        predicts_alternative_winner=False,
        finder_type_name="PathFinder",
        finder_id=id(PathFinder),
        status=status,
        spec_summary=spec_summary,
        exception_type_name=exception_type_name,
        event_seq=None,
        search_path=search_path,
        search_path_kind=search_path_kind,
        search_path_phase="import",
    )


def _same_path(a: str | None, b: str | None) -> bool:
    """Compare filesystem paths after absolutization and case normalization."""
    if a is None or b is None:
        return a == b
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
