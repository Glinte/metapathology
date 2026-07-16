"""Human-readable projection of a cutoff-based report document."""

import os
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder

from metapathology._records import (
    DeepDiagnosticCall,
    DeepImportEvent,
    FinderContract,
    FindSpecCall,
    ImportAuditStart,
    ImporterCacheDiff,
    ImporterCacheEntry,
    ImportObjectRef,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    ModuleCacheState,
    MonitorEvent,
    PathHooksMutation,
    PathHooksReassignment,
    StandardFinderCall,
)
from metapathology._report_data import CausalExplanation, Finding, ReportDocument, StandardResolution

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Iterable
    from traceback import StackSummary
    from typing import TypeVar

    from metapathology._module_metadata import ModuleMetadata

    _EventT = TypeVar("_EventT")


# This package's own directory, normcased for comparison: used to drop our
# frames from displayed stacks.
_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
# Max non-noise frames shown per stack in the report.
_STACK_DISPLAY_FRAMES = 5
_STANDARD_RESOLUTION_DISPLAY_LIMIT = 50
_FINDER_CONTRACT_DISPLAY_LIMIT = 50
_STANDARD_FINDER_IDS = frozenset((id(BuiltinImporter), id(FrozenImporter), id(PathFinder)))
# Max claimed modules listed per finder in the attribution section.
_MAX_LISTED_MODULES = 25
_MAX_CACHE_CHANGES_PER_DIFF = 25
# Loader type names shipped by CPython's import machinery (zipimport
# included). Their inventory groups are summarized as counts in text; a class
# merely named like one of these still shows up because any metadata
# disagreement is always listed.
_STANDARD_LOADER_NAMES = frozenset(
    (
        "BuiltinImporter",
        "ExtensionFileLoader",
        "FrozenImporter",
        "NamespaceLoader",
        "SourceFileLoader",
        "SourcelessFileLoader",
        "_NamespaceLoader",
        "zipimporter",
    )
)


class _RenderContext:
    """Per-document display state computed once before rendering.

    Holds the base directory for path shortening, whether the per-line
    ``[thread ...]`` suffix can be elided (single-threaded capture), and which
    display labels need an ``id 0x...`` suffix because several distinct
    objects share them. Ambiguity is tracked per display domain (finders vs
    path hooks) so that, for example, many per-module loader instances do not
    force hex ids back onto unrelated sections.
    """

    __slots__ = ("_base", "_base_prefix", "finder_ambiguous", "hook_ambiguous", "only_thread")

    def __init__(self, document: ReportDocument) -> None:
        cwd = document.cwd
        if cwd:
            base = os.path.normcase(cwd).rstrip(os.sep)
            self._base: str | None = base
            self._base_prefix: str | None = base + os.sep
        else:
            self._base = None
            self._base_prefix = None

        threads = {
            thread
            for thread in (getattr(event, "thread_name", None) for event in document.events)
            if thread is not None
        }
        self.only_thread: str | None = next(iter(threads)) if len(threads) == 1 else None

        finder_ids: dict[str, set[int]] = {}
        hook_ids: dict[str, set[int]] = {}
        for event in document.events:
            if isinstance(event, FindSpecCall):
                finder_ids.setdefault(event.finder_type_name, set()).add(event.finder_id)
            elif isinstance(event, PathHooksMutation):
                for reference in (*event.added, *event.removed, *event.contents_after):
                    _note_label(hook_ids, reference)
            elif isinstance(event, PathHooksReassignment):
                for reference in (*event.old_contents, *event.new_contents):
                    _note_label(hook_ids, reference)
        for reference in (*document.initial_path_hooks, *(document.current_path_hooks or ())):
            _note_label(hook_ids, reference)
        self.finder_ambiguous = {label for label, ids in finder_ids.items() if len(ids) > 1}
        self.hook_ambiguous = {label for label, ids in hook_ids.items() if len(ids) > 1}

    def display_path(self, path: str) -> str:
        """Shorten a path under the report's base directory."""
        if self._base is None or self._base_prefix is None:
            return path
        # normcase preserves length, so slicing the original keeps its casing.
        normalized = os.path.normcase(path)
        if normalized.rstrip(os.sep) == self._base:
            return "<project>"
        if normalized.startswith(self._base_prefix):
            return path[len(self._base_prefix) :]
        return path

    def quoted_path(self, path: str) -> str:
        """Quote a shortened path without repr's backslash escaping."""
        return f"'{self.display_path(path)}'"

    def finder_label(self, type_name: str, finder_id: int) -> str:
        """Name an instrumented finder, adding its id only when the type name is ambiguous."""
        if type_name in self.finder_ambiguous:
            return f"{type_name} id 0x{finder_id:x}"
        return type_name

    def hook_ref(self, reference: ImportObjectRef) -> str:
        """Name a path hook, adding its id only when the display label is ambiguous."""
        label = _ref_label(reference)
        if label in self.hook_ambiguous:
            return f"{label} id 0x{reference.object_id:x}"
        return label

    def hook_refs(self, references: tuple[ImportObjectRef, ...]) -> str:
        """Format a path-hook snapshot."""
        return "[" + ", ".join(self.hook_ref(reference) for reference in references) + "]"

    def thread_suffix(self, thread_name: str) -> str:
        """Per-line thread marker, elided when the whole capture is single-threaded."""
        return "" if self.only_thread is not None else f" [thread {thread_name}]"


def _note_label(ids_by_label: dict[str, set[int]], reference: ImportObjectRef) -> None:
    """Record one displayed label/id pair for ambiguity detection."""
    ids_by_label.setdefault(_ref_label(reference), set()).add(reference.object_id)


def _ref_label(reference: ImportObjectRef) -> str:
    """Preferred display label: the callable's own name, else its type name."""
    return reference.type_name if reference.name is None else reference.name


def render_lines(document: ReportDocument) -> list[str]:
    """Build the report body as a list of lines; the caller adds the trailing newline."""
    context = _RenderContext(document)
    mutations = _events_of_type(document.events, MetaPathMutation)
    reassignments = _events_of_type(document.events, MetaPathReassignment)
    path_hook_mutations = _events_of_type(document.events, PathHooksMutation)
    path_hook_reassignments = _events_of_type(document.events, PathHooksReassignment)
    importer_cache_diffs = _events_of_type(document.events, ImporterCacheDiff)
    calls = _events_of_type(document.events, FindSpecCall)
    errors = _events_of_type(document.events, InternalError)

    lines = _header_lines(document, context)

    if document.explanations:
        shown_explanations = _primary_explanations(document.explanations)
        lines.append("")
        lines.append(f"-- causal explanations ({len(shown_explanations)}) --")
        for explanation in shown_explanations:
            lines.extend(_explanation_lines(explanation, context))

    lines.append("")
    counts = {severity: 0 for severity in ("actionable", "warning", "informational")}
    for finding in document.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    summary = ", ".join(f"{count} {severity}" for severity, count in counts.items() if count)
    lines.append(f"-- findings ({len(document.findings)}{': ' + summary if summary else ''}) --")
    if not document.findings:
        lines.append("(none)")
    for finding in sorted(
        document.findings,
        key=lambda item: ({"actionable": 0, "warning": 1, "informational": 2}.get(item.severity, 1), item.finding_id),
    ):
        lines.extend(_finding_lines(finding, context))
        if finding.signals:
            lines.append(f"    corroborating signals: {', '.join(item.replace('_', ' ') for item in finding.signals)}")
        limitations = ", ".join(item.replace("_", " ") for item in finding.limitations)
        suffix = f"; limitations: {limitations}" if limitations else ""
        lines.append(f"    severity: {finding.severity}; evidence: {finding.evidence_level}{suffix}")

    lines.append("")
    lines.append("-- finder attribution (instrumented finders only) --")
    lines.extend(_attribution_lines(calls, context))

    contract_lines = _finder_contract_lines(document.finder_contracts)
    if contract_lines:
        lines.append("")
        lines.extend(contract_lines)

    lines.append("")
    lines.extend(_standard_resolution_lines(document.standard_resolutions, context))

    lines.append("")
    lines.extend(_timeline_lines(document, context))

    empty_sections: list[str] = []

    if mutations:
        lines.append("")
        lines.append(f"-- sys.meta_path mutations ({len(mutations)}) --")
        for mutation in mutations:
            lines.extend(_mutation_lines(mutation, context))
    else:
        empty_sections.append("sys.meta_path mutations")

    if reassignments:
        lines.append("")
        lines.append(f"-- sys.meta_path reassignments ({len(reassignments)}) --")
        for reassignment in reassignments:
            lines.extend(_reassignment_lines(reassignment, context))
    else:
        empty_sections.append("sys.meta_path reassignments")

    if path_hook_mutations:
        lines.append("")
        lines.append(f"-- sys.path_hooks mutations ({len(path_hook_mutations)}) --")
        for mutation in path_hook_mutations:
            lines.extend(_path_hooks_mutation_lines(mutation, context))
    else:
        empty_sections.append("sys.path_hooks mutations")

    if path_hook_reassignments:
        lines.append("")
        lines.append(f"-- sys.path_hooks reassignments ({len(path_hook_reassignments)}) --")
        for reassignment in path_hook_reassignments:
            lines.extend(_path_hooks_reassignment_lines(reassignment, context))
    else:
        empty_sections.append("sys.path_hooks reassignments")

    if importer_cache_diffs:
        lines.append("")
        lines.append(f"-- sys.path_importer_cache changes ({len(importer_cache_diffs)}) --")
        for diff in importer_cache_diffs:
            lines.extend(_importer_cache_diff_lines(diff, context))
    else:
        empty_sections.append("sys.path_importer_cache changes")

    error_count = len(errors) + len(document.report_errors)
    if error_count:
        lines.append("")
        lines.append(f"-- internal errors ({error_count}) --")
        lines.extend(_internal_error_line(error) for error in errors)
        lines.extend(f"during report in {error.where}: {error.exception_type_name}" for error in document.report_errors)
    else:
        empty_sections.append("internal errors")

    inventory_lines = _loader_inventory_lines(document, context)
    if inventory_lines:
        lines.append("")
        lines.extend(inventory_lines)

    if empty_sections:
        lines.append("")
        lines.append("nothing recorded: " + ", ".join(empty_sections))
    lines.append("")
    return lines


def _explanation_lines(explanation: CausalExplanation, context: _RenderContext) -> list[str]:
    """Render one deterministic evidence join before its atomic findings."""
    if explanation.kind == "namespace_truncation_failure":
        status = "failed" if explanation.effect_status == "failed" else "was attempted with outcome unknown"
        lines = [
            f"[{explanation.confidence}] '{explanation.subject}' {status} after {explanation.finder_type_name} truncated its parent namespace",
            f"    omitted location {context.quoted_path(explanation.omitted_location)} contains {context.quoted_path(explanation.candidate_path)}",
            f"    cause: {explanation.cause_finding_id}; supporting events: "
            + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
        ]
        if explanation.next_observation == "enable_deep_import_outcomes":
            lines.append(
                "    next observation: enable deep import outcomes to confirm whether the descendant import failed"
            )
        return lines
    if explanation.kind == "custom_claim_displacement":
        return [
            f"[counterfactual] {explanation.finder_type_name} claimed '{explanation.subject}' before PathFinder",
            f"    captured origin: {_origin_display(explanation.observed_origin, context)}",
            f"    PathFinder replay origin: {_origin_display(explanation.replayed_origin, context)}",
            f"    cause: {explanation.cause_finding_id}; supporting events: "
            + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
        ]
    if explanation.kind == "standard_winner_precedence":
        later = ", ".join(explanation.later_finders)
        qualifier = "produced" if explanation.confidence == "captured" else "likely produced"
        lines = [
            f"[{explanation.confidence}] {explanation.finder_type_name} {qualifier} {explanation.effect_status} for '{explanation.subject}' before later finders",
            f"    later unreachable finders: {later}; origin: {_origin_display(explanation.observed_origin, context)}",
        ]
        if explanation.standard_attempt_id is not None:
            lines.append(f"    attempt: attempt:{explanation.standard_attempt_id}")
        if explanation.next_observation == "enable_deep_import_outcomes":
            lines.append("    next observation: enable deep import outcomes to capture the standard winner")
        return lines
    if explanation.kind == "finder_side_effect":
        outcome = "raised" if explanation.effect_status == "finder_raised" else "returned None"
        return [
            f"[captured] {explanation.finder_type_name} {outcome} after changing sys.modules['{explanation.subject}']",
            f"    {explanation.boundary} boundary: {_module_transition(explanation.state_before, explanation.state_after)}",
            "    nested activity was not observed; the boundary delta does not identify the internal cause",
        ]
    if explanation.kind == "module_replacement":
        return [
            f"[captured] {explanation.finder_type_name} replaced the module identity for '{explanation.subject}'",
            f"    {explanation.boundary} boundary: {_module_transition(explanation.state_before, explanation.state_after)}",
            "    both endpoint identities are exact; intermediate steps remain unknown",
        ]
    return [f"[{explanation.confidence}] {explanation.kind}: '{explanation.subject}'"]


def _primary_explanations(explanations: tuple[CausalExplanation, ...]) -> tuple[CausalExplanation, ...]:
    """Keep lower-value corroboration in JSON without crowding human diagnoses."""
    namespace = tuple(item for item in explanations if item.kind == "namespace_truncation_failure")
    if namespace:
        return tuple(sorted(namespace, key=lambda item: item.effect_status != "failed"))
    exact = tuple(item for item in explanations if item.confidence in ("captured", "correlated"))
    return exact or explanations


def _header_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Summarize mechanism status and interpreter state, collapsing unchanged snapshots."""
    lines = ["== metapathology report =="]
    lines.append("report guide: https://glinte.github.io/metapathology/report/")
    lines.append(_monitoring_line(document))
    if document.deep_diagnostics:
        lines.append("WARNING: opt-in deep diagnostics replace foreign callables and may perturb identity checks")
        lines.append(f"deep mechanisms enabled: {', '.join(document.deep_diagnostics)}")
    if document.deep_import_outcomes_status != "disabled":
        lines.append(f"deep import outcome coverage: {document.deep_import_outcomes_status}")
    if document.standard_finder_status != "disabled":
        lines.append(f"standard finder aggregate coverage: {document.standard_finder_status}")
    bootstrap = document.early_site_bootstrap
    if bootstrap is not None:
        lines.append(f"early site bootstrap: {context.display_path(bootstrap.path)}")
        lines.append(f"bootstrap site-packages: {context.display_path(bootstrap.site_packages)}")
        lines.append(f"bootstrap activation: {bootstrap.activation_source}")
        earlier = _names_line(bootstrap.earlier_pth_files) if bootstrap.earlier_pth_files else "(none)"
        lines.append(f"earlier .pth files outside capture: {earlier}")

    if document.current_meta_path == document.initial_meta_path:
        lines.append(f"sys.meta_path (unchanged since install): {_names_line(document.initial_meta_path)}")
    else:
        current_meta_path = ("<unavailable>",) if document.current_meta_path is None else document.current_meta_path
        lines.append(f"sys.meta_path at install: {_names_line(document.initial_meta_path)}")
        lines.append(f"sys.meta_path now: {_names_line(current_meta_path)}")

    if document.path_hooks_enabled:
        current_hooks = document.current_path_hooks
        if current_hooks is not None and _ref_signatures(current_hooks) == _ref_signatures(document.initial_path_hooks):
            lines.append(f"sys.path_hooks (unchanged since install): {context.hook_refs(document.initial_path_hooks)}")
        else:
            now = "<unavailable>" if current_hooks is None else context.hook_refs(current_hooks)
            lines.append(f"sys.path_hooks at install: {context.hook_refs(document.initial_path_hooks)}")
            lines.append(f"sys.path_hooks now: {now}")

    if document.importer_cache_enabled:
        initial_count = len(document.initial_importer_cache)
        cache = document.current_importer_cache
        current_count = "<unavailable>" if cache is None else str(len(cache))
        line = f"sys.path_importer_cache: {initial_count} -> {current_count} string-keyed entries"
        initial_non_string = document.initial_importer_cache_non_string_keys
        current_non_string = document.current_importer_cache_non_string_keys or 0
        if initial_non_string or current_non_string:
            if initial_non_string == current_non_string:
                line += f" ({initial_non_string} non-string keys omitted)"
            else:
                line += f" ({initial_non_string} -> {current_non_string} non-string keys omitted)"
        lines.append(line)

    standard_skipped = [item for item in document.skipped_finders if item.expected]
    other_skipped = [item for item in document.skipped_finders if not item.expected]
    if standard_skipped:
        lines.append(
            "standard CPython finders left unwrapped (expected): "
            + _names_line(tuple(item.finder_type_name for item in standard_skipped))
        )
        lines.append("    These interpreter-shared classes handle built-in, frozen, and sys.path imports;")
        lines.append("    metapathology does not modify them. Custom claims are checked against PathFinder below.")
    if other_skipped:
        lines.append("other finders observed but not instrumented (direct attribution unavailable):")
        lines.extend(f"    {item.finder_type_name}: {item.reason}" for item in other_skipped)

    module_count = 0 if document.modules_since_install is None else len(document.modules_since_install)
    lines.append(f"modules imported since install: {module_count}")
    if document.cwd:
        lines.append(f"paths shown relative to: {document.cwd}")
    return lines


def _monitoring_line(document: ReportDocument) -> str:
    """One-line mechanism summary replacing the per-mechanism enabled/disabled lines."""
    if not document.monitor_enabled:
        return "monitoring: disabled"
    mechanisms = ["sys.meta_path"]
    notes: list[str] = []
    for name, enabled in (
        ("sys.path_hooks", document.path_hooks_enabled),
        ("sys.path_importer_cache", document.importer_cache_enabled),
    ):
        if enabled:
            mechanisms.append(name)
        else:
            notes.append(f"{name} off")
    if not document.deep_diagnostics:
        notes.append("deep diagnostics off")
    if document.early_site_bootstrap is None:
        notes.append("early site bootstrap inactive")
    line = "monitoring: " + ", ".join(mechanisms)
    if notes:
        line += " (" + "; ".join(notes) + ")"
    return line


def _ref_signatures(references: tuple[ImportObjectRef, ...]) -> tuple[tuple[int, str, str | None], ...]:
    """Comparable identity tuples for detecting an unchanged snapshot."""
    return tuple((reference.object_id, reference.type_name, reference.name) for reference in references)


def _events_of_type(events: "Iterable[object]", event_type: "type[_EventT]") -> "list[_EventT]":
    """Return only events of ``event_type``, preserving that concrete type for callers."""
    return [event for event in events if isinstance(event, event_type)]


def _internal_error_line(error: InternalError) -> str:
    """Format an internal error without requiring captured foreign exception text."""
    line = f"#{error.seq} in {error.where}: {error.exception_type_name}"
    return line if error.message is None else f"{line}: {error.message}"


def _contract_category(contract: FinderContract) -> str:
    """Summarize modern and legacy protocol availability for text."""
    modern = contract.find_spec.availability
    legacy = contract.find_module.availability
    if "indeterminate" in (modern, legacy):
        return "indeterminate"
    if modern == "callable":
        return "modern + legacy" if legacy == "callable" else "modern"
    return "legacy-only" if legacy == "callable" else "protocol-less"


def _finder_contract_lines(contracts: tuple[FinderContract, ...]) -> list[str]:
    """Render compatibility risks first, with a fixed text-output bound."""
    risky = [
        contract
        for contract in contracts
        if contract.finder_id not in _STANDARD_FINDER_IDS
        and _contract_category(contract) in {"legacy-only", "protocol-less", "indeterminate"}
    ]
    standard = [contract for contract in contracts if contract.finder_id in _STANDARD_FINDER_IDS]
    if not risky:
        return []
    shown = (risky + standard)[:_FINDER_CONTRACT_DISPLAY_LIMIT]
    lines = [f"-- finder API contracts ({len(contracts)} observed entries) --"]
    if not shown:
        lines.append("(no compatibility risks or standard class entries observed)")
        return lines
    for contract in shown:
        category = _contract_category(contract)
        location = (
            "at install" if contract.observation_seq is None else f"at mutation event #{contract.observation_seq}"
        )
        if contract.finder_id in _STANDARD_FINDER_IDS:
            lines.append(f"{contract.finder_type_name}: {category} ({location}; standard CPython finder)")
            continue
        lines.append(f"[{category}] {contract.finder_type_name} ({location}, position {contract.position})")
        if category == "legacy-only":
            lines.append(
                "    compatibility risk: CPython 3.12+ has no legacy find_module fallback; "
                "direct meta-path consumers may require find_spec on every supported version"
            )
        elif category == "protocol-less":
            lines.append("    compatibility risk: neither finder protocol is callable")
        else:
            lines.append("    compatibility could not be determined without binding a descriptor or dynamic attribute")
    omitted = len(risky) + len(standard) - len(shown)
    if omitted:
        lines.append(f"... {omitted} more risk/standard entries omitted from text; JSON is exhaustive")
    return lines


def _standard_resolution_lines(resolutions: tuple[StandardResolution, ...], context: _RenderContext) -> list[str]:
    """Render a bounded projection without hiding evidence provenance."""
    lines = [f"-- standard resolution outcomes ({len(resolutions)}) --"]
    if not resolutions:
        lines.append("(none)")
        return lines
    for resolution in resolutions[:_STANDARD_RESOLUTION_DISPLAY_LIMIT]:
        origin = "None" if resolution.origin is None else context.quoted_path(resolution.origin)
        line = (
            f"[{resolution.evidence_level} standard resolution] {resolution.fullname!r}: "
            f"{resolution.finder_type_name} produced {resolution.category} "
            f"(loader {resolution.loader_type_name}, origin {origin})"
        )
        lines.append(line)
        if resolution.later_finders:
            lines.append(f"    later meta-path entries were unreachable: {_names_line(resolution.later_finders)}")
        if resolution.component_event_seqs:
            references = ", ".join(f"#{seq}" for seq in resolution.component_event_seqs)
            lines.append(f"    captured path-entry components: {references}")
        if resolution.evidence_level == "inferred":
            lines.append("    based on import-time ordering and post-hoc loader inventory; no finder call was captured")
    omitted = len(resolutions) - _STANDARD_RESOLUTION_DISPLAY_LIMIT
    if omitted > 0:
        lines.append(f"... {omitted} additional standard outcomes omitted from text; JSON remains exhaustive")
    return lines


def _timeline_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Render the timeline with shared context hoisted into a preamble."""
    events = document.events
    lines = [f"-- chronological evidence timeline ({len(events)}) --"]
    lines.append("Sequence numbers are capture order; concurrent events are not a global wall-clock order.")
    if not events:
        lines.append("(none)")
        return lines
    if context.only_thread is not None:
        lines.append(f"All events on thread {context.only_thread}.")

    audits = _events_of_type(events, ImportAuditStart)
    meta_path_stable = len({(audit.meta_path_id, audit.meta_path_type_names) for audit in audits}) <= 1
    path_hooks_stable = len({audit.path_hooks_id for audit in audits}) <= 1
    importer_cache_stable = len({audit.importer_cache_id for audit in audits}) <= 1
    if audits:
        lines.append(
            "Audit lines mark the start of uncached resolution; the audit event has no outcome or winner signal."
        )
        stable_names: list[str] = []
        if meta_path_stable:
            stable_names.append("sys.meta_path")
        if path_hooks_stable and any(audit.path_hooks_id is not None for audit in audits):
            stable_names.append("sys.path_hooks")
        if importer_cache_stable and any(audit.importer_cache_id is not None for audit in audits):
            stable_names.append("sys.path_importer_cache")
        if stable_names:
            noun = "identity" if len(stable_names) == 1 else "identities"
            lines.append(f"{_join_names(stable_names)} {noun} stable across all audited imports.")

    previous_audit: ImportAuditStart | None = None
    for event in events:
        lines.append(_timeline_line(event, context))
        if isinstance(event, ImportAuditStart):
            lines.extend(
                _audit_deviation_lines(
                    event, previous_audit, meta_path_stable, path_hooks_stable, importer_cache_stable
                )
            )
            previous_audit = event
    return lines


def _join_names(names: list[str]) -> str:
    """Join names as English prose: 'a', 'a and b', or 'a, b, and c'."""
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _audit_deviation_lines(
    event: ImportAuditStart,
    previous: ImportAuditStart | None,
    meta_path_stable: bool,
    path_hooks_stable: bool,
    importer_cache_stable: bool,
) -> list[str]:
    """Detail only the mechanisms whose identity deviates within the audit stream."""
    lines: list[str] = []
    if not meta_path_stable and (
        previous is None
        or (previous.meta_path_id, previous.meta_path_type_names) != (event.meta_path_id, event.meta_path_type_names)
    ):
        lines.append(f"    meta_path 0x{event.meta_path_id:x}: {_names_line(event.meta_path_type_names)}")
    if not path_hooks_stable and (previous is None or previous.path_hooks_id != event.path_hooks_id):
        value = "disabled" if event.path_hooks_id is None else f"0x{event.path_hooks_id:x}"
        lines.append(f"    path_hooks {value}")
    if not importer_cache_stable and (previous is None or previous.importer_cache_id != event.importer_cache_id):
        if event.importer_cache_id is None:
            value = "disabled"
        else:
            value = f"0x{event.importer_cache_id:x} size {event.importer_cache_size}"
        lines.append(f"    importer_cache {value}")
    return lines


def _timeline_line(event: MonitorEvent, context: _RenderContext) -> str:
    """Render one compact line using only data captured in the event record."""
    if isinstance(event, DeepDiagnosticCall):
        subject = event.fullname if event.fullname is not None else event.path
        transition = _module_transition_suffix(event.module_state_before, event.module_state_after)
        return f"#{event.seq} deep {event.boundary} {subject or '<unknown>'}: {event.outcome}{transition}"
    if isinstance(event, DeepImportEvent):
        return f"#{event.seq} deep import {event.fullname!r}: {event.outcome}{context.thread_suffix(event.thread_name)}"
    if isinstance(event, ImportAuditStart):
        # Stable snapshot context lives in the timeline preamble; deviations
        # are appended by _timeline_lines as continuation lines.
        return (
            f"#{event.seq} import audit: resolution started for {event.fullname!r}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, FindSpecCall):
        if event.exception_type_name is not None:
            outcome = f"raised {event.exception_type_name}"
        elif event.found:
            origin = "None" if event.origin is None else context.quoted_path(event.origin)
            outcome = f"claimed (loader {event.loader_type_name}, origin {origin})"
        else:
            outcome = "passed"
        finder = context.finder_label(event.finder_type_name, event.finder_id)
        transition = _module_transition_suffix(event.module_state_before, event.module_state_after)
        return (
            f"#{event.seq} {finder} probed {event.fullname!r}: {outcome}{transition}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, StandardFinderCall):
        loader = event.spec_summary.loader
        loader_name = "None" if loader is None else loader.type_name
        return (
            f"#{event.seq} captured {event.finder_type_name} result for {event.fullname!r}: loader {loader_name}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, ImporterCacheDiff):
        deltas: list[str] = []
        if event.added:
            deltas.append(f"+{len(event.added)}")
        if event.removed:
            deltas.append(f"-{len(event.removed)}")
        if event.replaced:
            deltas.append(f"~{len(event.replaced)}")
        delta = " ".join(deltas) if deltas else "(no string-key changes)"
        return (
            f"#{event.seq} importer cache diff at {event.observation}: {delta}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, MetaPathMutation):
        return (
            f"#{event.seq} sys.meta_path {event.op} {_timeline_delta(event.added, event.removed)}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, MetaPathReassignment):
        return (
            f"#{event.seq} sys.meta_path reassignment detected during {event.during_import!r}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, PathHooksMutation):
        return (
            f"#{event.seq} sys.path_hooks {event.op}: +{len(event.added)} -{len(event.removed)}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    if isinstance(event, PathHooksReassignment):
        return (
            f"#{event.seq} sys.path_hooks reassignment detected during {event.during_import!r}"
            f"{context.thread_suffix(event.thread_name)}"
        )
    return _internal_error_line(event)


def _timeline_delta(added: tuple[str, ...], removed: tuple[str, ...]) -> str:
    """Format a compact meta-path delta."""
    parts: list[str] = []
    if added:
        parts.append("+" + _names_line(added))
    if removed:
        parts.append("-" + _names_line(removed))
    return " ".join(parts) if parts else "(order change)"


def _names_line(names: tuple[str, ...]) -> str:
    """Format finder names as a bracketed, comma-separated list."""
    return "[" + ", ".join(names) + "]"


def _loader_inventory_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Render a bounded view of the exhaustive post-hoc loader inventory.

    Text shows loader groups that participated in captured custom claims and
    any metadata disagreements; JSON retains the exhaustive inventory.
    """
    inventory = document.loader_inventory
    if not inventory.available:
        return ["-- post-hoc loader inventory --", "(sys.modules snapshot unavailable)"]
    groups: dict[str | None, list[ModuleMetadata]] = {}
    for entry in inventory.entries:
        if entry.inspection != "available":
            continue
        label = None if entry.loader is None else entry.loader.type_name
        groups.setdefault(label, []).append(entry)
    claimed_loaders = {
        event.loader_type_name
        for event in document.events
        if isinstance(event, FindSpecCall) and event.found and event.loader_type_name is not None
    }
    custom = sorted(label for label in groups if label is not None and label in claimed_loaders)
    disagreements = [
        entry for entry in inventory.entries if entry.inspection == "available" and entry.loader_agreement is False
    ]
    relevant_names = set(document.modules_since_install or ())
    relevant_names.update(
        event.fullname for event in document.events if isinstance(event, FindSpecCall) and event.found
    )
    unavailable = sorted(
        (entry for entry in inventory.entries if entry.inspection != "available" and entry.name in relevant_names),
        key=lambda entry: entry.name,
    )
    if not custom and not disagreements and not unavailable and not inventory.non_string_keys:
        return []
    lines = [f"-- relevant post-hoc loader inventory ({len(inventory.entries)} total entries in JSON) --"]
    lines.append("Text shows claimed custom loaders and metadata problems; attribution remains report-time.")
    for label in custom:
        entries = groups[label]
        lines.append(f"{label}: {len(entries)} module(s)")
        lines.extend(_inventory_entry_lines(entries, context))
    if disagreements:
        lines.append(f"loader metadata disagreements: {len(disagreements)}")
        lines.extend(_inventory_entry_lines(disagreements, context))
    if unavailable:
        lines.append(f"metadata unavailable for {len(unavailable)} module-cache entries:")
        lines.extend(
            f"    {entry.name}: {entry.reason or 'unavailable'}" for entry in unavailable[:_MAX_LISTED_MODULES]
        )
        if len(unavailable) > _MAX_LISTED_MODULES:
            lines.append(f"    ... and {len(unavailable) - _MAX_LISTED_MODULES} more")
    if inventory.non_string_keys:
        lines.append(f"non-string sys.modules keys omitted: {inventory.non_string_keys}")
    return lines


def _inventory_entry_lines(entries: "list[ModuleMetadata]", context: _RenderContext) -> list[str]:
    """List inventory modules with their origins, bounded per group."""
    ordered = sorted(entries, key=lambda entry: entry.name)
    lines: list[str] = []
    for entry in ordered[:_MAX_LISTED_MODULES]:
        summary = entry.spec_summary
        origin = None if summary is None else summary.origin
        disagreement = " [loader metadata disagreement]" if entry.loader_agreement is False else ""
        lines.append(f"    {entry.name}: origin {_metadata_value(origin, context)}{disagreement}")
    if len(ordered) > _MAX_LISTED_MODULES:
        lines.append(f"    ... and {len(ordered) - _MAX_LISTED_MODULES} more")
    return lines


def _metadata_value(value: "str | ImportObjectRef | None", context: _RenderContext) -> str:
    """Format already-reduced metadata without consulting live objects."""
    if isinstance(value, ImportObjectRef):
        return f"<{_ref_label(value)} id 0x{value.object_id:x}>"
    if value is None:
        return "None"
    return context.quoted_path(value)


def _mutation_lines(mutation: MetaPathMutation, context: _RenderContext) -> list[str]:
    """Format one mutation record: op, delta, resulting contents, and user stack."""
    delta_parts: list[str] = []
    if mutation.added:
        delta_parts.append("+" + _names_line(mutation.added))
    if mutation.removed:
        delta_parts.append("-" + _names_line(mutation.removed))
    delta = " ".join(delta_parts) if delta_parts else "(order change)"
    lines = [f"#{mutation.seq} {mutation.op} {delta}{context.thread_suffix(mutation.thread_name)}"]
    lines.append(f"    meta_path after: {_names_line(mutation.contents_after)}")
    lines.extend(_stack_lines(mutation.stack, context))
    return lines


def _reassignment_lines(reassignment: MetaPathReassignment, context: _RenderContext) -> list[str]:
    """Format one reassignment record with before/after contents and detection stack."""
    lines = [
        f"#{reassignment.seq} sys.meta_path REASSIGNED, detected during import of "
        f"'{reassignment.during_import}'{context.thread_suffix(reassignment.thread_name)}"
    ]
    lines.append(f"    before: {_names_line(reassignment.old_contents)}")
    lines.append(f"    after:  {_names_line(reassignment.new_contents)}")
    lines.append("    instrumentation reinstalled; stack shows the triggering import, not the reassignment itself:")
    lines.extend(_stack_lines(reassignment.stack, context))
    return lines


def _path_hooks_mutation_lines(mutation: PathHooksMutation, context: _RenderContext) -> list[str]:
    """Format one path-hook mutation from captured plain references."""
    delta_parts: list[str] = []
    if mutation.added:
        delta_parts.append("+" + context.hook_refs(mutation.added))
    if mutation.removed:
        delta_parts.append("-" + context.hook_refs(mutation.removed))
    delta = " ".join(delta_parts) if delta_parts else "(order change)"
    lines = [f"#{mutation.seq} {mutation.op} {delta}{context.thread_suffix(mutation.thread_name)}"]
    lines.append(f"    path_hooks after: {context.hook_refs(mutation.contents_after)}")
    lines.extend(_stack_lines(mutation.stack, context))
    return lines


def _path_hooks_reassignment_lines(reassignment: PathHooksReassignment, context: _RenderContext) -> list[str]:
    """Format one path-hooks reassignment detected at an import boundary."""
    lines = [
        f"#{reassignment.seq} sys.path_hooks REASSIGNED, detected during import of "
        f"'{reassignment.during_import}'{context.thread_suffix(reassignment.thread_name)}"
    ]
    lines.append(f"    before: {context.hook_refs(reassignment.old_contents)}")
    lines.append(f"    after:  {context.hook_refs(reassignment.new_contents)}")
    lines.append("    instrumentation reinstalled; stack shows the triggering import, not the reassignment itself:")
    lines.extend(_stack_lines(reassignment.stack, context))
    return lines


def _cache_entry_line(entry: ImporterCacheEntry, context: _RenderContext) -> str:
    """Format one captured cache value without inspecting the live finder."""
    finder = _cache_finder_name(entry.finder)
    return f"{context.quoted_path(entry.path)} -> {finder}"


def _cache_finder_name(finder: ImportObjectRef | None, force_id: bool = False) -> str:
    """Format a captured cache finder or its negative marker.

    Cache finders are keyed by path, so their ids are usually noise;
    ``force_id`` is used only when a replacement's before/after labels would
    otherwise read identically.
    """
    if finder is None:
        return "negative (None)"
    label = _ref_label(finder)
    return f"{label} id 0x{finder.object_id:x}" if force_id else label


def _importer_cache_diff_lines(diff: ImporterCacheDiff, context: _RenderContext) -> list[str]:
    """Format one bounded human projection of an exhaustive cache diff."""
    lines = [f"#{diff.seq} at {diff.observation}{context.thread_suffix(diff.thread_name)}"]
    changes: list[str] = []
    changes.extend(f"    + {_cache_entry_line(entry, context)}" for entry in diff.added)
    changes.extend(f"    - {_cache_entry_line(entry, context)}" for entry in diff.removed)
    for replacement in diff.replaced:
        same_label = _cache_finder_name(replacement.before) == _cache_finder_name(replacement.after)
        changes.append(
            f"    ~ {context.quoted_path(replacement.path)}: "
            f"{_cache_finder_name(replacement.before, force_id=same_label)} -> "
            f"{_cache_finder_name(replacement.after, force_id=same_label)}"
        )
    lines.extend(changes[:_MAX_CACHE_CHANGES_PER_DIFF])
    if len(changes) > _MAX_CACHE_CHANGES_PER_DIFF:
        lines.append(f"    ... and {len(changes) - _MAX_CACHE_CHANGES_PER_DIFF} more changes")
    if diff.non_string_keys_before != diff.non_string_keys_after:
        lines.append(f"    non-string keys omitted: {diff.non_string_keys_before} -> {diff.non_string_keys_after}")
    return lines


def _attribution_lines(calls: list[FindSpecCall], context: _RenderContext) -> list[str]:
    """Summarize finder traffic, capping claimed-module display per finder."""
    probes: dict[tuple[str, int], int] = {}
    wins: dict[tuple[str, int], list[str]] = {}
    for call in calls:
        key = (call.finder_type_name, call.finder_id)
        probes[key] = probes.get(key, 0) + 1
        if call.found:
            wins.setdefault(key, []).append(call.fullname)
    if not probes:
        return ["(no find_spec activity recorded on instrumented finders)"]
    lines: list[str] = []
    for (name, finder_id), count in sorted(probes.items()):
        claimed = wins.get((name, finder_id), [])
        lines.append(f"{context.finder_label(name, finder_id)}: {count} probes, {len(claimed)} claimed")
        lines.extend(f"    {module}" for module in claimed[:_MAX_LISTED_MODULES])
        if len(claimed) > _MAX_LISTED_MODULES:
            lines.append(f"    ... and {len(claimed) - _MAX_LISTED_MODULES} more")
    return lines


def _finding_lines(finding: Finding, context: _RenderContext) -> list[str]:
    """Render one structured finding as a headline plus labeled evidence lines."""
    if finding.kind == "legacy_finder_contract" and finding.finder_contract is not None:
        contract = finding.finder_contract
        return [
            f"[legacy-finder-contract] {contract.finder_type_name}: find_module is callable but find_spec is not",
            "    CPython 3.12+ removed the legacy fallback; direct consumers may require find_spec earlier",
        ]
    if finding.kind == "path_hook_shadow" and finding.deep_call is not None:
        call = finding.deep_call
        return [
            f"[path-hook-shadow] {context.quoted_path(finding.module)}: distinct hooks accepted this path",
            f"    captured acceptances: event #{finding.supporting_event_seqs[0]} and event #{call.seq}",
        ]
    if finding.kind == "failed_after_mutation":
        mutation, started, failed = finding.supporting_event_seqs
        return [
            f"[failed-after-mutation] '{finding.module}': exact import failure followed a recorded mutation",
            f"    mutation event #{mutation}; import boundary events #{started} -> #{failed}; temporal correlation is not causation",
        ]
    if finding.kind == "module_replacement" and finding.deep_call is not None:
        call = finding.deep_call
        transition = _module_transition(call.module_state_before, call.module_state_after)
        return [
            f"[module-replacement] '{finding.module}': object identity changed across deep {call.boundary}",
            f"    captured boundary: {transition}; internal steps and temporary objects are unknown",
        ]
    if finding.kind == "no_spec":
        return [
            f"[no-spec] '{finding.module}' is in sys.modules with no __spec__ and no recorded finder claim "
            "(manually created or exec_module-style load; invisible to all import hooks)."
        ]
    claim = finding.claim
    if finding.kind == "finder_side_effect" and claim is not None:
        finder = context.finder_label(claim.finder_type_name, claim.finder_id)
        outcome = f"raising {claim.exception_type_name}" if claim.exception_type_name is not None else "returning None"
        transition = _module_transition(claim.module_state_before, claim.module_state_after)
        return [
            f"[finder-side-effect] '{finding.module}': {finder} changed sys.modules while {outcome}",
            f"    captured boundary: {transition}; nested activity was not observed",
        ]
    replay = finding.replay
    tag = finding.kind.replace("_", "-")
    if claim is None or replay is None:
        return [f"[{tag}] '{finding.module}'"]
    if finding.kind == "loader_displacement":
        return [
            f"[loader-displacement] '{finding.module}': captured claim and live PathFinder replay chose different loader types",
            f"    claimed: loader {claim.loader_type_name}, origin {_origin_display(claim.origin, context)}",
            f"    PathFinder replay: loader {replay.loader_type_name}, origin {_origin_display(replay.origin, context)}",
            f"    {_spec_comparison_line(finding)}",
            f"    {_structural_comparison_line(finding)}",
        ]
    if finding.kind == "frozen_source_conflict":
        return [
            f"[frozen-source-conflict] '{finding.module}': source claim displaced a frozen or archive loader in replay",
            f"    {_spec_comparison_line(finding)}",
            f"    {_structural_comparison_line(finding)}",
        ]
    finder = context.finder_label(claim.finder_type_name, claim.finder_id)
    claimed_line = f"    claimed: loader {claim.loader_type_name}, origin {_origin_display(claim.origin, context)}"
    if finding.kind == "unfindable":
        return [
            f"[unfindable] '{finding.module}': claimed by {finder}, but a live PathFinder replay finds nothing; "
            "sys.path_hooks-based tools never see this module",
            claimed_line,
            f"    {_structural_comparison_line(finding)}",
        ]
    if finding.kind == "namespace_truncation":
        comparison = finding.spec_comparison
        if comparison is None:
            omitted = "unavailable"
        else:
            omitted = ", ".join(context.quoted_path(path) for path in comparison.omitted_locations)
        return [
            f"[namespace-truncation] '{finding.module}': claimed as a namespace package by {finder}",
            f"    locations omitted from the claim (present in the PathFinder replay): {omitted}",
            f"    {_spec_comparison_line(finding)}",
            f"    {_structural_comparison_line(finding)}",
        ]
    same_origin = (
        claim.origin is not None
        and replay.origin is not None
        and os.path.normcase(claim.origin) == os.path.normcase(replay.origin)
    )
    replay_origin = "same origin" if same_origin else f"origin {_origin_display(replay.origin, context)}"
    return [
        f"[{tag}] '{finding.module}': custom claim by {finder} differs from PathFinder replay",
        claimed_line,
        f"    PathFinder replay: loader {replay.loader_type_name}, {replay_origin}",
        f"    {_spec_comparison_line(finding)}",
        f"    {_structural_comparison_line(finding)}",
    ]


def _module_transition_suffix(before: ModuleCacheState | None, after: ModuleCacheState | None) -> str:
    """Render only informative cache evidence in the bounded timeline."""
    if before is None or after is None:
        return ""
    if before.state == "unavailable" or after.state == "unavailable":
        return "; module identity unavailable"
    if before.state == after.state and before.object_id == after.object_id and before.type_name == after.type_name:
        return ""
    return f"; sys.modules {_module_transition(before, after)}"


def _module_transition(before: ModuleCacheState | None, after: ModuleCacheState | None) -> str:
    """Render one plain-data module-cache transition."""
    return f"{_module_state(before)} -> {_module_state(after)}"


def _module_state(state: ModuleCacheState | None) -> str:
    """Render one captured state without consulting a live module object."""
    if state is None:
        return "not captured"
    if state.state != "object":
        return state.state
    return f"{state.type_name} at 0x{state.object_id:x}" if state.object_id is not None else "object"


def _origin_display(origin: str | None, context: _RenderContext) -> str:
    """Quote a spec origin path, keeping a bare None for absent origins."""
    return "None" if origin is None else context.quoted_path(origin)


def _spec_comparison_line(finding: Finding) -> str:
    comparison = finding.spec_comparison
    if comparison is None:
        return "differences: comparison unavailable"
    differences: list[str] = []
    if comparison.loader_type_changed:
        differences.append("loader type")
    if comparison.origin_changed:
        differences.append("origin")
    if comparison.cached_changed:
        differences.append("cached path")
    if comparison.package_status_changed:
        differences.append("package/module status")
    if comparison.omitted_locations:
        count = len(comparison.omitted_locations)
        differences.append(f"{count} standard search location{'' if count == 1 else 's'} omitted")
    if comparison.additional_locations:
        count = len(comparison.additional_locations)
        differences.append(f"{count} additional claimed search location{'' if count == 1 else 's'}")
    if comparison.locations_reordered:
        differences.append("search locations reordered")
    detail = "; ".join(differences) if differences else "no comparable semantic differences"
    phase = "post-hoc claim" if comparison.observed_locations_state == "post_hoc" else "import-time claim"
    qualifier = "" if comparison.complete else "partial; "
    return f"differences ({qualifier}{phase} vs live replay): {detail}"


def _structural_comparison_line(finding: Finding) -> str:
    """Label identity-only evidence separately from the report-time replay."""
    comparison = finding.structural_comparison
    if comparison is None:
        return "structural evidence: unavailable"
    if comparison.path_hooks_changed is None:
        path_hooks = "sys.path_hooks comparison unavailable"
    elif comparison.path_hooks_changed:
        path_hooks = "sys.path_hooks changed since install"
    else:
        path_hooks = "sys.path_hooks unchanged since install"
    if comparison.importer_cache_changed is None:
        importer_cache = "importer cache comparison unavailable"
    elif comparison.importer_cache_changed:
        count = len(comparison.importer_cache_changed_paths)
        noun = "path" if count == 1 else "paths"
        importer_cache = f"importer cache changed for {count} captured search {noun}"
    else:
        importer_cache = "importer cache unchanged for the captured search path"
    return f"structural evidence: {path_hooks}; {importer_cache}"


def _stack_lines(stack: "StackSummary", context: _RenderContext) -> list[str]:
    """Format interesting captured frames, innermost first, with noise removed."""
    frames = [frame for frame in stack if not _is_noise_frame(frame.filename)]
    shown = frames[:_STACK_DISPLAY_FRAMES]  # walk_stack order: innermost first.
    if not shown:
        return ["    (no frames outside the import machinery)"]
    return [f"    at {context.display_path(frame.filename)}:{frame.lineno} in {frame.name}" for frame in shown]


def _is_noise_frame(filename: str) -> bool:
    """Return True for frames from import machinery or metapathology itself."""
    if filename.startswith("<frozen importlib"):
        return True
    return os.path.normcase(os.path.abspath(filename)).startswith(_PACKAGE_DIR)
