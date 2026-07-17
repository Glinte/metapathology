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
from metapathology._report_data import (
    CausalExplanation,
    Finding,
    ReportDocument,
    ResolutionRoute,
    RouteComparison,
    StandardResolution,
    StructuralComparison,
    unresolved_attempts,
)

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

    __slots__ = (
        "_base",
        "_base_prefix",
        "comparisons_by_id",
        "finder_ambiguous",
        "hook_ambiguous",
        "only_thread",
        "relevant_cache_paths",
        "routes_by_id",
    )

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
        self.routes_by_id = {route.route_id: route for route in document.resolution_routes}
        self.comparisons_by_id = {comparison.comparison_id: comparison for comparison in document.route_comparisons}

        relevant_cache_paths: set[str] = set()
        for finding in document.findings:
            if finding.structural_comparison is not None:
                relevant_cache_paths.update(
                    os.path.normcase(path) for path in finding.structural_comparison.importer_cache_changed_paths
                )
        for route in document.resolution_routes:
            relevant_cache_paths.update(os.path.normcase(path) for path in route.search_path)
        self.relevant_cache_paths = relevant_cache_paths

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

    lines.append("")
    counts = {severity: 0 for severity in ("actionable", "warning", "informational")}
    for finding in document.findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    summary = ", ".join(f"{count} {severity}" for severity, count in counts.items() if count)
    consumed_comparisons = {
        finding.route_comparison_id for finding in document.findings if finding.route_comparison_id is not None
    }
    divergent = tuple(
        comparison
        for comparison in document.route_comparisons
        if _routes_differ(comparison) and comparison.comparison_id not in consumed_comparisons
    )
    lines.append(f"-- findings ({len(document.findings)}{': ' + summary if summary else ''}) --")
    if not document.findings:
        module_count = 0 if document.modules_since_install is None else len(document.modules_since_install)
        if divergent or document.explanations:
            lines.append("No findings were synthesized; the route divergences below are neutral evidence.")
        else:
            lines.append(f"No import-hook interference detected across {module_count} monitored imports.")
    lines.extend(_narrative_lines(document, context))

    if divergent:
        lines.append("")
        lines.append(f"-- resolution route divergences ({len(divergent)}) --")
        for comparison in divergent:
            lines.extend(_route_comparison_lines(comparison, context, mutations))

    unresolved_lines = _unresolved_import_lines(document, context)
    if unresolved_lines:
        lines.append("")
        lines.extend(unresolved_lines)

    empty_sections: list[str] = []

    if calls:
        lines.append("")
        lines.append("-- finder attribution (instrumented finders only) --")
        lines.extend(_attribution_lines(calls, context))
    else:
        empty_sections.append("find_spec activity on instrumented finders")

    contract_lines = _finder_contract_lines(document.finder_contracts)
    if contract_lines:
        lines.append("")
        lines.extend(contract_lines)

    if document.standard_resolutions:
        lines.append("")
        lines.extend(_standard_resolution_lines(document.standard_resolutions, context))
    else:
        empty_sections.append("standard resolution outcomes")

    if document.events:
        lines.append("")
        lines.extend(_timeline_lines(document, context))
    else:
        empty_sections.append("timeline events")

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
        lines.append(f"Nothing was recorded for: {', '.join(empty_sections)}.")
    lines.append("")
    return lines


# One static consequence sentence per finding kind, stating only what the
# kind's documented semantics already imply.
_WHY_IT_MATTERS = {
    "namespace_truncation": (
        "submodules that exist only under the omitted locations cannot be imported "
        "while the narrower namespace stays cached"
    ),
    "module_replacement": (
        "references obtained before the replacement point at a different object than later imports receive"
    ),
    "finder_side_effect": (
        "the finder reported no result yet altered the module cache; later imports see the altered cache"
    ),
    "path_hook_shadow": (
        "only the first accepting hook builds the path-entry finder; the other hook never handles this path"
    ),
}

_SEVERITY_ORDER = {"actionable": 0, "warning": 1, "informational": 2}


def _narrative_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Render numbered problem blocks: explanations headline their cause finding's evidence."""
    shown_explanations = _primary_explanations(document.explanations) if document.explanations else ()
    explanations_by_cause: dict[str, list[CausalExplanation]] = {}
    for explanation in shown_explanations:
        if explanation.cause_finding_id is not None:
            explanations_by_cause.setdefault(explanation.cause_finding_id, []).append(explanation)
    ordered = sorted(document.findings, key=lambda item: (_SEVERITY_ORDER.get(item.severity, 1), item.finding_id))
    headline_findings = [finding for finding in ordered if finding.severity != "informational"]
    informational = [finding for finding in ordered if finding.severity == "informational"]
    numbers = _headline_finding_numbers(document)

    lines: list[str] = []
    number = 0
    for finding in headline_findings:
        number = numbers[finding.finding_id]
        explanations = explanations_by_cause.pop(finding.finding_id, [])
        block: list[str] = []
        if explanations:
            block.extend(_explanation_lines(explanations[0], context, nested=True))
            for extra in explanations[1:]:
                block.extend(_explanation_lines(extra, context, nested=True))
            block.extend("    " + line for line in _finding_lines(finding, context))
        else:
            block.extend(_finding_lines(finding, context))
        block[0] = f"[{number}] {block[0]}"
        if finding.signals:
            block.append(f"    corroborating signals: {', '.join(item.replace('_', ' ') for item in finding.signals)}")
        consequence = _WHY_IT_MATTERS.get(finding.kind)
        if consequence is not None:
            block.append(f"    why it matters: {consequence}")
        block.append(f"    {_finding_confidence_line(finding)}")
        lines.extend(block)

    orphan_explanations = [
        explanation
        for explanation in shown_explanations
        if explanation.cause_finding_id is None
        or not any(finding.finding_id == explanation.cause_finding_id for finding in headline_findings)
    ]
    for explanation in orphan_explanations:
        number += 1
        block = _explanation_lines(
            explanation, context, explanations_by_id={item.explanation_id: item for item in document.explanations}
        )
        block[0] = f"[{number}] {block[0]}"
        lines.extend(block)

    if informational:
        lines.append(f"informational ({len(informational)}):")
        lines.extend(f"    {_finding_lines(finding, context)[0]}" for finding in informational)
    return lines


_MAX_UNRESOLVED_ATTEMPTS = 25


def _unresolved_import_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """List imports that started but left no module, joining them to legacy-only finders."""
    unresolved = unresolved_attempts(document.attempts)
    if not unresolved:
        return []
    missing = None if document.target_outcome is None else document.target_outcome.missing_module
    ordered = sorted(unresolved, key=lambda attempt: (attempt.fullname != missing, attempt.start_event_seq))
    lines = [f"-- imports that started but produced no module ({len(unresolved)}) --"]
    lines.append("Failed or abandoned optional imports are normal; entries matter when the target needed the module.")
    for attempt in ordered[:_MAX_UNRESOLVED_ATTEMPTS]:
        marker = " (the target's failed module)" if attempt.fullname == missing else ""
        lines.append(
            f"'{attempt.fullname}': progress {_humanize(attempt.progress)}, began at event #{attempt.start_event_seq}"
            f"{marker}{context.thread_suffix(attempt.thread_name)}"
        )
    if len(ordered) > _MAX_UNRESOLVED_ATTEMPTS:
        lines.append(f"... and {len(ordered) - _MAX_UNRESOLVED_ATTEMPTS} more; details in JSON")
    numbers = _headline_finding_numbers(document)
    legacy = [finding for finding in document.findings if finding.kind == "legacy_finder_contract"]
    for finding in legacy:
        reference = f" — see [{numbers[finding.finding_id]}]" if finding.finding_id in numbers else ""
        lines.append(
            f"note: {finding.module} was on sys.meta_path but accepts only the legacy find_module protocol; "
            f"CPython 3.12+ never calls it{reference}"
        )
    return lines


def _headline_finding_numbers(document: ReportDocument) -> dict[str, int]:
    """Visible block numbers assigned to non-informational findings in display order."""
    ordered = sorted(document.findings, key=lambda item: (_SEVERITY_ORDER.get(item.severity, 1), item.finding_id))
    return {
        finding.finding_id: number
        for number, finding in enumerate(
            (finding for finding in ordered if finding.severity != "informational"), start=1
        )
    }


def _finding_confidence_line(finding: Finding) -> str:
    """State severity, evidence level, and limitations as one prose sentence."""
    limitations = ", ".join(item.replace("_", " ") for item in finding.limitations)
    suffix = f"; limitations: {limitations}" if limitations else ""
    article = "an" if finding.severity == "actionable" else "a"
    return f"this is {article} {finding.severity} finding based on {_humanize(finding.evidence_level)} evidence{suffix}"


def _explanation_lines(
    explanation: CausalExplanation,
    context: _RenderContext,
    nested: bool = False,
    explanations_by_id: "dict[str, CausalExplanation] | None" = None,
) -> list[str]:
    """Render one deterministic evidence join before its atomic findings."""
    if explanation.kind == "namespace_truncation_failure":
        status = "failed" if explanation.effect_status == "failed" else "was attempted with outcome unknown"
        lines = [
            f"[{explanation.confidence}] '{explanation.subject}' {status} after {explanation.finder_type_name} truncated its parent namespace",
            f"    omitted location {context.quoted_path(explanation.omitted_location)} contains {context.quoted_path(explanation.candidate_path)}",
            ("    supporting events: " if nested else f"    cause: {explanation.cause_finding_id}; supporting events: ")
            + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
        ]
        if explanation.next_observation == "enable_deep_import_outcomes":
            lines.append(
                "    next observation: enable deep import outcomes to confirm whether the descendant import failed"
            )
        return lines
    if explanation.kind == "standard_winner_precedence":
        later = ", ".join(explanation.later_finders)
        qualifier = "produced" if explanation.confidence == "captured" else "likely produced"
        lines = [
            f"[{explanation.confidence}] {explanation.finder_type_name} {qualifier} {explanation.effect_status} for '{explanation.subject}' before later finders",
            f"    later unreachable finders: {later}; origin: {_origin_display(explanation.origin, context)}",
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
        action = (
            "executed a separate module object for"
            if explanation.effect_status == "separate_module_executed"
            else "replaced the module identity for"
        )
        return [
            f"[captured] {explanation.finder_type_name} {action} '{explanation.subject}'",
            f"    {explanation.boundary} boundary: {_module_transition(explanation.state_before, explanation.state_after)}",
            "    both endpoint identities are exact; intermediate steps remain unknown",
        ]
    if explanation.kind == "ambiguous_contention":
        alternatives = []
        for alternative_id in explanation.alternatives:
            alternative = None if explanations_by_id is None else explanations_by_id.get(alternative_id)
            alternatives.append(alternative_id if alternative is None else _humanize(alternative.kind))
        return [
            f"[unknown] competing explanations remain for '{explanation.subject}'",
            f"    alternatives: {', '.join(alternatives)}; supporting events: "
            + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
            "    next observation: capture a more specific boundary before selecting a cause",
        ]
    return [f"[{explanation.confidence}] {explanation.kind}: '{explanation.subject}'"]


def _primary_explanations(explanations: tuple[CausalExplanation, ...]) -> tuple[CausalExplanation, ...]:
    """Keep lower-value corroboration in JSON without crowding human diagnoses."""
    ambiguous = tuple(item for item in explanations if item.kind == "ambiguous_contention")
    if ambiguous:
        return ambiguous
    namespace = tuple(item for item in explanations if item.kind == "namespace_truncation_failure")
    if namespace:
        return tuple(sorted(namespace, key=lambda item: item.effect_status != "failed"))
    exact = tuple(item for item in explanations if item.confidence in ("captured", "correlated"))
    return exact or explanations


def _deep_effective_module_state(call: DeepDiagnosticCall) -> ModuleCacheState | None:
    """Prefer an executed module argument when it differs from the cache entry."""
    before = call.module_state_before
    target = call.target_state
    if (
        before is not None
        and before.state == "object"
        and target is not None
        and target.state == "object"
        and before.object_id != target.object_id
    ):
        return target
    return call.module_state_after


def _header_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Summarize mechanism status and interpreter state, collapsing unchanged snapshots."""
    lines = ["== metapathology report =="]
    lines.extend(_verdict_lines(document))
    lines.append("report guide: https://glinte.github.io/metapathology/report/")
    lines.append(_monitoring_line(document))
    if document.deep_diagnostics:
        lines.append("WARNING: opt-in deep diagnostics replace foreign callables and may perturb identity checks")
        lines.append(f"deep mechanisms enabled: {', '.join(document.deep_diagnostics)}")
    if document.deep_import_outcomes_status != "disabled":
        lines.append(f"deep import outcome coverage: {_humanize(document.deep_import_outcomes_status)}")
    if document.standard_finder_status != "disabled":
        lines.append(f"standard finder aggregate coverage: {_humanize(document.standard_finder_status)}")
    bootstrap = document.early_site_bootstrap
    if bootstrap is not None:
        lines.append(f"early site bootstrap: {context.display_path(bootstrap.path)}")
        lines.append(f"bootstrap site-packages: {context.display_path(bootstrap.site_packages)}")
        lines.append(f"bootstrap activation: {bootstrap.activation_source}")
        earlier = _names_line(bootstrap.earlier_pth_files) if bootstrap.earlier_pth_files else "(none)"
        lines.append(f"earlier .pth files outside capture: {earlier}")
    frozen_bootstrap = document.frozen_bootstrap
    if frozen_bootstrap is not None:
        lines.append(f"frozen integration: {frozen_bootstrap.integration}")
        lines.append(f"frozen bootstrap: {context.display_path(frozen_bootstrap.path)}")
        lines.append(f"frozen observation boundary: {frozen_bootstrap.boundary}")

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
            + "; custom claims are compared with an independent standard path probe below"
        )
    if other_skipped:
        lines.append("other finders observed but not instrumented (direct attribution unavailable):")
        lines.extend(f"    {item.finder_type_name}: {item.reason}" for item in other_skipped)

    module_count = 0 if document.modules_since_install is None else len(document.modules_since_install)
    lines.append(f"modules imported since install: {module_count}")
    if document.cwd:
        lines.append(f"paths shown relative to: {document.cwd} (that directory itself displays as <project>)")
    return lines


def _verdict_lines(document: ReportDocument) -> list[str]:
    """Lead with the target's outcome and a one-sentence reading of the evidence."""
    lines: list[str] = []
    outcome = document.target_outcome
    unresolved = unresolved_attempts(document.attempts)
    if outcome is not None:
        if outcome.kind == "raised":
            what = outcome.exception_type_name or "an exception"
            line = f"target outcome: raised {what}"
            if outcome.missing_module is not None:
                line += f" for '{outcome.missing_module}'"
        else:
            line = "target outcome: completed"
        if outcome.exit_code is not None:
            line += f" (exit status {outcome.exit_code})"
        if outcome.missing_module is not None and any(
            attempt.fullname == outcome.missing_module for attempt in unresolved
        ):
            line += "; the failed module appears under unresolved imports below"
        lines.append(line)

    summary = document.summary
    counts = [
        f"{count} {severity}"
        for severity, count in (
            ("actionable", summary.actionable),
            ("warning", summary.warning),
            ("informational", summary.informational),
        )
        if count
    ]
    numbers = _headline_finding_numbers(document)
    total = len(document.findings)
    breakdown = f"{total} finding{'' if total == 1 else 's'}"
    if len(counts) > 1:
        breakdown += f" ({', '.join(counts)})"
    elif counts:
        breakdown = f"{counts[0]} finding{'' if total == 1 else 's'}"
    top = next((finding for finding in document.findings if finding.finding_id == summary.top_finding_id), None)
    if top is not None:
        tag = top.kind.replace("_", "-")
        lines.append(f"verdict: {breakdown}; most severe is [{tag}] '{top.module}' — see [{numbers[top.finding_id]}]")
    elif counts:
        lines.append(f"verdict: {breakdown}")
    elif any(_routes_differ(comparison) for comparison in document.route_comparisons):
        lines.append("verdict: no findings; neutral resolution route divergences were recorded")
    else:
        module_count = 0 if document.modules_since_install is None else len(document.modules_since_install)
        lines.append(f"verdict: no import-hook interference detected across {module_count} monitored imports")
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
    optional_off: list[str] = []
    if not document.deep_diagnostics:
        optional_off.append("deep diagnostics")
    if document.early_site_bootstrap is None:
        optional_off.append("early site bootstrap")
    if optional_off:
        notes.append(f"opt-in {_join_names(optional_off)} available but not used for this run")
    line = "monitoring: " + ", ".join(mechanisms)
    if notes:
        line += " (" + "; ".join(notes) + ")"
    return line


def _humanize(value: str) -> str:
    """Render one internal snake_case value as plain words."""
    return value.replace("_", " ")


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
        lines.append(f"... {omitted} more risk/standard entries; details in JSON")
    return lines


def _standard_resolution_lines(resolutions: tuple[StandardResolution, ...], context: _RenderContext) -> list[str]:
    """Render a bounded projection without hiding evidence provenance."""
    lines = [f"-- standard resolution outcomes ({len(resolutions)}) --"]
    for resolution in resolutions[:_STANDARD_RESOLUTION_DISPLAY_LIMIT]:
        origin = "None" if resolution.origin is None else context.quoted_path(resolution.origin)
        line = (
            f"[{_humanize(resolution.evidence_level)} standard resolution] {resolution.fullname!r}: "
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
        lines.append(f"... {omitted} additional standard outcomes; details in JSON")
    return lines


def _timeline_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Render the timeline with shared context hoisted into a preamble."""
    events = document.events
    lines = [f"-- chronological evidence timeline ({len(events)}) --"]
    lines.append("Events appear in capture order; concurrent events have no global wall-clock order.")
    if context.only_thread is not None:
        lines.append(f"All events on thread {context.only_thread}.")

    audits = _events_of_type(events, ImportAuditStart)
    meta_path_stable = len({(audit.meta_path_id, audit.meta_path_type_names) for audit in audits}) <= 1
    path_hooks_stable = len({audit.path_hooks_id for audit in audits}) <= 1
    importer_cache_stable = len({audit.importer_cache_id for audit in audits}) <= 1
    if audits:
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

    # Read once per render: full disables collapsing and reproduces the
    # exhaustive pre-collapse timeline exactly.
    full = os.environ.get("METAPATHOLOGY_TEXT_TIMELINE") == "full"
    entries = _timeline_entries(events, context, meta_path_stable, path_hooks_stable, importer_cache_stable)
    if full:
        for _event, main_line, continuation, _collapsible in entries:
            lines.append(main_line)
            lines.extend(continuation)
        return lines

    protected = _protected_event_positions(document, events)
    lines.extend(_collapse_timeline_entries(entries, protected))
    return lines


def _timeline_entries(
    events: "tuple[MonitorEvent, ...]",
    context: _RenderContext,
    meta_path_stable: bool,
    path_hooks_stable: bool,
    importer_cache_stable: bool,
) -> "list[tuple[MonitorEvent, str, list[str], bool]]":
    """Render every event once, tagging whether it is eligible for run-collapsing.

    An ``ImportAuditStart`` is collapsible when it has no audit-deviation
    continuation lines; a ``FindSpecCall`` is collapsible when its outcome is
    a plain decline with no cache-state transition to report.
    """
    entries: list[tuple[MonitorEvent, str, list[str], bool]] = []
    previous_audit: ImportAuditStart | None = None
    for event in events:
        main_line = _timeline_line(event, context)
        continuation: list[str] = []
        collapsible = False
        if isinstance(event, ImportAuditStart):
            continuation = _audit_deviation_lines(
                event, previous_audit, meta_path_stable, path_hooks_stable, importer_cache_stable
            )
            previous_audit = event
            collapsible = not continuation
        elif isinstance(event, FindSpecCall):
            declined = event.exception_type_name is None and not event.found
            collapsible = (
                declined and _module_transition_suffix(event.module_state_before, event.module_state_after) == ""
            )
        entries.append((event, main_line, continuation, collapsible))
    return entries


def _protected_event_positions(document: ReportDocument, events: "tuple[MonitorEvent, ...]") -> set[int]:
    """Positions (by index, not seq) that must always render expanded, plus one neighbor each side."""
    referenced_seqs: set[int] = set()
    for finding in document.findings:
        referenced_seqs.update(finding.supporting_event_seqs)
        if finding.claim is not None:
            referenced_seqs.add(finding.claim.seq)
        if finding.deep_call is not None:
            referenced_seqs.add(finding.deep_call.seq)
    for explanation in document.explanations:
        referenced_seqs.update(explanation.event_seqs)
    position_by_seq = {event.seq: index for index, event in enumerate(events)}
    protected: set[int] = set()
    for seq in referenced_seqs:
        position = position_by_seq.get(seq)
        if position is None:
            continue
        protected.add(position)
        if position > 0:
            protected.add(position - 1)
        if position + 1 < len(events):
            protected.add(position + 1)
    return protected


_MIN_COLLAPSE_RUN = 4


def _collapse_timeline_entries(
    entries: "list[tuple[MonitorEvent, str, list[str], bool]]", protected: set[int]
) -> list[str]:
    """Collapse maximal runs of >= 4 unprotected collapsible events into one summary line each."""
    lines: list[str] = []
    index = 0
    total = len(entries)
    while index < total:
        _event, main_line, continuation, collapsible = entries[index]
        if collapsible and index not in protected:
            end = index
            while end < total and entries[end][3] and end not in protected:
                end += 1
            run_length = end - index
            if run_length >= _MIN_COLLAPSE_RUN:
                lines.append(_collapse_summary_line(entries[index:end]))
                index = end
                continue
        lines.append(main_line)
        lines.extend(continuation)
        index += 1
    return lines


def _collapse_summary_line(run: "list[tuple[MonitorEvent, str, list[str], bool]]") -> str:
    """Summarize one collapsed run of timeline events as a single deterministic line."""
    first_seq = run[0][0].seq
    last_seq = run[-1][0].seq
    starts = sum(1 for event, *_ in run if isinstance(event, ImportAuditStart))
    declined = sum(1 for event, *_ in run if isinstance(event, FindSpecCall))
    clauses = []
    if starts:
        clauses.append(f"{starts} uncached import start{'' if starts == 1 else 's'}")
    if declined:
        clauses.append(f"{declined} declined probe{'' if declined == 1 else 's'}")
    detail = " and ".join(clauses)
    return f"#{first_seq}-#{last_seq}: {detail} collapsed; details in JSON timeline"


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
        return f"#{event.seq} uncached import started: {event.fullname!r}{context.thread_suffix(event.thread_name)}"
    if isinstance(event, FindSpecCall):
        if event.exception_type_name is not None:
            outcome = f"raised {event.exception_type_name}"
        elif event.found:
            origin = "None" if event.origin is None else context.quoted_path(event.origin)
            outcome = f"claimed (loader {event.loader_type_name}, origin {origin})"
        else:
            outcome = "declined"
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
            f"#{event.seq} importer cache diff at {_humanize(event.observation)}: {delta}"
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


# Display-only annotations for finder classes that well-known environment
# tooling installs at startup. They affect text labels only, never severity
# or finding logic, and match by class name alone.
_KNOWN_SHIM_ANNOTATIONS = {"_Finder": "virtualenv startup, expected"}


def _names_line(names: tuple[str, ...]) -> str:
    """Format finder names as a bracketed, comma-separated list."""
    return "[" + ", ".join(_annotated_name(name) for name in names) + "]"


def _annotated_name(name: str) -> str:
    """Append the known-environment-shim annotation to a displayed finder name."""
    annotation = _KNOWN_SHIM_ANNOTATIONS.get(name)
    return name if annotation is None else f"{name} ({annotation})"


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
        return f"<{_ref_label(value)}>"
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


_UNFILTERED_CACHE_CHANGES_SHOWN = 10


def _cache_diff_entries(diff: ImporterCacheDiff, context: _RenderContext) -> "list[tuple[str, str, str]]":
    """Build (kind, path, rendered line) triples for every entry in one cache diff."""
    entries: list[tuple[str, str, str]] = []
    entries.extend(("+", entry.path, f"    + {_cache_entry_line(entry, context)}") for entry in diff.added)
    entries.extend(("-", entry.path, f"    - {_cache_entry_line(entry, context)}") for entry in diff.removed)
    for replacement in diff.replaced:
        same_label = _cache_finder_name(replacement.before) == _cache_finder_name(replacement.after)
        entries.append(
            (
                "~",
                replacement.path,
                f"    ~ {context.quoted_path(replacement.path)}: "
                f"{_cache_finder_name(replacement.before, force_id=same_label)} -> "
                f"{_cache_finder_name(replacement.after, force_id=same_label)}",
            )
        )
    return entries


def _cache_change_counts_clause(entries: "list[tuple[str, str, str]]") -> str:
    """Render an ``other changes`` clause's +/-/~ counts, omitting zero counts."""
    counts = {"+": 0, "-": 0, "~": 0}
    for kind, _path, _line in entries:
        counts[kind] += 1
    parts = [f"{sign}{counts[sign]}" for sign in ("+", "-", "~") if counts[sign]]
    return " ".join(parts) + " entries"


def _importer_cache_diff_lines(diff: ImporterCacheDiff, context: _RenderContext) -> list[str]:
    """Format one bounded human projection of an exhaustive cache diff.

    Full entry lines are shown only for paths relevant to a finding's
    structural comparison or a resolution route's captured search path;
    everything else is summarized as one counted line, keeping JSON
    exhaustive.
    """
    lines = [f"#{diff.seq} at {_humanize(diff.observation)}{context.thread_suffix(diff.thread_name)}"]
    entries = _cache_diff_entries(diff, context)
    relevant = context.relevant_cache_paths
    if relevant:
        relevant_entries = [item for item in entries if os.path.normcase(item[1]) in relevant]
        other_entries = [item for item in entries if os.path.normcase(item[1]) not in relevant]
        shown = relevant_entries[:_MAX_CACHE_CHANGES_PER_DIFF]
        lines.extend(line for _kind, _path, line in shown)
        if len(relevant_entries) > _MAX_CACHE_CHANGES_PER_DIFF:
            omitted = len(relevant_entries) - _MAX_CACHE_CHANGES_PER_DIFF
            lines.append(f"    ... and {omitted} more changes; details in JSON")
        if other_entries:
            lines.append(f"    other changes: {_cache_change_counts_clause(other_entries)}; details in JSON")
    else:
        shown = entries[:_UNFILTERED_CACHE_CHANGES_SHOWN]
        lines.extend(line for _kind, _path, line in shown)
        if len(entries) > _UNFILTERED_CACHE_CHANGES_SHOWN:
            omitted = len(entries) - _UNFILTERED_CACHE_CHANGES_SHOWN
            lines.append(f"    ... and {omitted} more changes; details in JSON")
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
    lines: list[str] = []
    for (name, finder_id), count in sorted(probes.items()):
        claimed = wins.get((name, finder_id), [])
        lines.append(
            f"{context.finder_label(name, finder_id)}: {count} probe{'' if count == 1 else 's'}, {len(claimed)} claimed"
        )
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
        transition = _module_transition(
            finding.module_state_baseline or call.module_state_before, _deep_effective_module_state(call)
        )
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
    tag = finding.kind.replace("_", "-")
    if claim is None:
        return [f"[{tag}] '{finding.module}'"]
    finder = context.finder_label(claim.finder_type_name, claim.finder_id)
    if finding.kind == "namespace_truncation":
        comparison = (
            None if finding.route_comparison_id is None else context.comparisons_by_id.get(finding.route_comparison_id)
        )
        if comparison is None:
            standard_only = "unavailable"
        else:
            standard_only = ", ".join(context.quoted_path(path) for path in comparison.only_in_right_route)
        return [
            f"[namespace-truncation] '{finding.module}': descendant failure is correlated with a narrower namespace route from {finder}",
            f"    locations available only through the standard path probe: {standard_only}",
            f"    {_structural_comparison_line(finding)}",
        ]
    return [f"[{tag}] '{finding.module}': captured evidence from {finder}"]


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


def _routes_differ(comparison: RouteComparison) -> bool:
    """Return whether two route outcomes have any comparable difference."""
    return bool(
        comparison.status_differs
        or comparison.loader_type_differs
        or comparison.origin_differs
        or comparison.cached_differs
        or comparison.package_status_differs
        or comparison.only_in_left_route
        or comparison.only_in_right_route
        or comparison.locations_reordered
    )


def _route_comparison_lines(
    comparison: RouteComparison, context: _RenderContext, mutations: "list[MetaPathMutation] | None" = None
) -> list[str]:
    """Render a neutral route comparison outside the findings section."""
    left = context.routes_by_id.get(comparison.left_route_id)
    right = context.routes_by_id.get(comparison.right_route_id)
    if left is None or right is None:
        return [f"{comparison.comparison_id}: referenced route unavailable"]
    lines = [
        f"'{left.module}': captured claim compared with an independent standard path probe",
        f"    captured route: {_route_summary(left, context)}",
        f"    standard path probe: {_route_summary(right, context, same_origin_as=left)}",
        f"    {_route_difference_line(comparison)}",
    ]
    installer = _finder_installation_line(left.finder_type_name, mutations or [], context)
    if installer is not None:
        lines.append(f"    {installer}")
    if left.signals:
        signals = ", ".join(item.replace("_", " ") for item in left.signals)
        lines.append(f"    captured route signals: {signals}")
    lines.extend(
        (
            "    interpretation: the probe does not predict which finder would win if the captured finder were absent",
            "    probe boundary: captured search path; report-time path hooks, importer cache, filesystem, and "
            "finder state; intervening meta-path finders skipped",
            f"    {_structural_evidence_line(comparison.structural_comparison)}",
        )
    )
    return lines


def _finder_installation_line(
    finder_type_name: str, mutations: "list[MetaPathMutation]", context: _RenderContext
) -> str | None:
    """Name where the captured route's finder joined sys.meta_path, when that is unambiguous."""
    matches = [mutation for mutation in mutations if finder_type_name in mutation.added]
    if len(matches) != 1:
        return None
    frames = [frame for frame in matches[0].stack if not _is_noise_frame(frame.filename)]
    if not frames:
        return f"{finder_type_name} was added to sys.meta_path at event #{matches[0].seq}"
    frame = frames[0]
    return (
        f"{finder_type_name} was added to sys.meta_path at event #{matches[0].seq} "
        f"by {context.display_path(frame.filename)}:{frame.lineno} in {frame.name}"
    )


def _route_summary(
    route: ResolutionRoute, context: _RenderContext, same_origin_as: ResolutionRoute | None = None
) -> str:
    """Summarize one route using only its reduced report data."""
    finder = route.finder_type_name
    if route.finder_id is not None and route.kind == "captured_claim":
        finder = context.finder_label(finder, route.finder_id)
    if route.status != "found" or route.spec_summary is None:
        return f"{finder} status {route.status}"
    summary = route.spec_summary
    loader = "None" if summary.loader is None else summary.loader.type_name
    origin = summary.origin if type(summary.origin) is str else None
    if (
        same_origin_as is not None
        and same_origin_as.spec_summary is not None
        and origin is not None
        and origin == same_origin_as.spec_summary.origin
    ):
        return f"{finder}, loader {loader}, same origin"
    return f"{finder}, loader {loader}, origin {_origin_display(origin, context)}"


def _route_difference_line(comparison: RouteComparison) -> str:
    """Describe symmetric semantic differences without choosing a baseline."""
    differences: list[str] = []
    if comparison.status_differs:
        differences.append("resolution status")
    if comparison.loader_type_differs:
        differences.append("loader type")
    if comparison.origin_differs:
        differences.append("origin")
    if comparison.cached_differs:
        differences.append("cached path")
    if comparison.package_status_differs:
        differences.append("package/module status")
    if comparison.only_in_left_route:
        count = len(comparison.only_in_left_route)
        differences.append(f"{count} search location{'' if count == 1 else 's'} only in captured route")
    if comparison.only_in_right_route:
        count = len(comparison.only_in_right_route)
        differences.append(f"{count} search location{'' if count == 1 else 's'} only in standard path route")
    if comparison.locations_reordered:
        differences.append("search locations reordered")
    detail = "; ".join(differences) if differences else "no comparable semantic differences"
    qualifier = "" if comparison.complete else "partial; "
    return f"route differences ({qualifier}captured route vs standard path probe): {detail}"


def _structural_comparison_line(finding: Finding) -> str:
    """Label identity-only evidence separately from a report-time probe."""
    comparison = finding.structural_comparison
    if comparison is None:
        return "structural evidence: unavailable"
    return _structural_evidence_line(comparison)


def _structural_evidence_line(comparison: StructuralComparison) -> str:
    """Render one structural comparison without coupling it to a finding."""
    path_hooks_changed = comparison.path_hooks_changed
    importer_cache_changed = comparison.importer_cache_changed
    importer_cache_changed_paths = comparison.importer_cache_changed_paths
    if path_hooks_changed is None:
        path_hooks = "sys.path_hooks comparison unavailable"
    elif path_hooks_changed:
        path_hooks = "sys.path_hooks changed since install"
    else:
        path_hooks = "sys.path_hooks unchanged since install"
    if importer_cache_changed is None:
        importer_cache = "importer cache comparison unavailable"
    elif importer_cache_changed:
        count = len(importer_cache_changed_paths)
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
