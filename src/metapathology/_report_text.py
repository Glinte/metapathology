"""Human-readable projection of a cutoff-based report document."""

import os
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder

from metapathology._records import (
    DeepDiagnosticCall,
    DeepImportEvent,
    FinderContract,
    FindSpecCall,
    ImportAuditStart,
    ImportCall,
    ImporterCacheDiff,
    ImporterCacheEntry,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    ModuleCacheState,
    MonitorEvent,
    ObjectRef,
    PathHooksMutation,
    PathHooksReassignment,
    StandardFinderCall,
    SysPathMutation,
    SysPathReassignment,
)
from metapathology._report_analysis import unresolved_attempts
from metapathology._report_model import (
    CausalExplanation,
    ContractEvidence,
    DeepCallEvidence,
    Finding,
    ReportDocument,
    ResolutionRoute,
    RouteComparison,
    StandardResolution,
    StructuralComparison,
    finding_claim,
    finding_deep_call,
    finding_route_comparison_id,
    finding_structural_comparison,
)
from metapathology._report_text_context import (
    _ANSI_BOLD_CYAN,
    _ANSI_BOLD_RED,
    _ANSI_RESET,
    _ref_label,
    _RenderContext,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from traceback import StackSummary
    from typing import TypeVar

    from metapathology._module_metadata import ModuleMetadata

    _EventT = TypeVar("_EventT", bound=MonitorEvent)


# This package's own directory, normcased for comparison: used to drop our
# frames from displayed stacks.
_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
# Max non-noise frames shown per stack in the report.
_STACK_DISPLAY_FRAMES = 5
_REPORT_GUIDE_URL = "https://glinte.github.io/metapathology/report/"
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


def _iter_event_section(
    empty_sections: list[str],
    items: "list[_EventT]",
    label: str,
    render_item: "Callable[[_EventT, _RenderContext], list[str]]",
    context: _RenderContext,
    *,
    record_empty: bool = True,
) -> "Iterator[str]":
    """Yield one monitored-list section, or record its label as empty.

    A present section is a ``-- label (N) --`` heading (preceded by a blank
    line) over ``render_item`` output for each entry. An absent section adds
    ``label`` to ``empty_sections`` only when ``record_empty`` is set.
    """
    if items:
        yield ""
        yield context.heading(f"-- {label} ({len(items)}) --")
        for item in items:
            yield from render_item(item, context)
    elif record_empty:
        empty_sections.append(label)


class _TextEventBuckets:
    """Classify the exhaustive event log once for all text report sections."""

    def __init__(self, events: tuple[MonitorEvent, ...]) -> None:
        self.meta_path_mutations: list[MetaPathMutation] = []
        self.meta_path_reassignments: list[MetaPathReassignment] = []
        self.path_hook_mutations: list[PathHooksMutation] = []
        self.path_hook_reassignments: list[PathHooksReassignment] = []
        self.sys_path_mutations: list[SysPathMutation] = []
        self.sys_path_reassignments: list[SysPathReassignment] = []
        self.importer_cache_diffs: list[ImporterCacheDiff] = []
        self.finder_calls: list[FindSpecCall] = []
        self.internal_errors: list[InternalError] = []
        for event in events:
            self._add(event)

    def _add(self, event: MonitorEvent) -> None:
        if isinstance(event, MetaPathMutation):
            self.meta_path_mutations.append(event)
        elif isinstance(event, MetaPathReassignment):
            self.meta_path_reassignments.append(event)
        elif isinstance(event, PathHooksMutation):
            self.path_hook_mutations.append(event)
        elif isinstance(event, PathHooksReassignment):
            self.path_hook_reassignments.append(event)
        elif isinstance(event, SysPathMutation):
            self.sys_path_mutations.append(event)
        elif isinstance(event, SysPathReassignment):
            self.sys_path_reassignments.append(event)
        elif isinstance(event, ImporterCacheDiff):
            self.importer_cache_diffs.append(event)
        elif isinstance(event, FindSpecCall):
            self.finder_calls.append(event)
        elif isinstance(event, InternalError):
            self.internal_errors.append(event)


class _TextReportRenderer:
    """Own one text rendering pass and its pre-indexed display state."""

    def __init__(self, document: ReportDocument, *, color: bool) -> None:
        self.document = document
        self.context = _RenderContext(document, color=color)
        self._empty: list[str] = []
        buckets = _TextEventBuckets(document.analysis.events)
        self.mutations = buckets.meta_path_mutations
        self.calls = buckets.finder_calls
        self.errors = buckets.internal_errors
        self.buckets = buckets

    def iter_lines(self) -> "Iterator[str]":
        yield from self._render_header()
        yield ""
        divergent = self._divergent_comparisons()
        yield from self._render_findings()
        yield from self._render_comparisons(divergent)
        yield from self._render_evidence()
        yield from self._render_observation_sections()
        yield from self._render_errors()
        yield from self._render_inventory()
        if self._empty:
            yield ""
            yield f"Nothing was recorded for: {', '.join(self._empty)}."
        yield ""

    def _render_header(self) -> "Iterator[str]":
        yield self.context.heading("== metapathology report ==")
        yield from _verdict_lines(self.document, self.context)
        yield f"report guide: {_REPORT_GUIDE_URL}"
        yield _monitoring_line(self.document)
        yield from _mechanism_header_lines(self.document, self.context)
        yield from _snapshot_header_lines(self.document, self.context)
        yield from _inventory_header_lines(self.document)
        modules = self.document.capture.modules_since_install
        yield f"modules imported since install: {0 if modules is None else len(modules)}"
        if self.document.process.cwd:
            yield (
                f"paths shown relative to: {self.document.process.cwd} (that directory itself displays as <project>)"
            )

    def _divergent_comparisons(self) -> tuple[RouteComparison, ...]:
        """Route comparisons that differ and are not already consumed by a finding."""
        findings = self.document.analysis.findings
        consumed = {
            route_comparison_id
            for item in findings
            if (route_comparison_id := finding_route_comparison_id(item)) is not None
        }
        return tuple(
            item
            for item in self.document.analysis.route_comparisons
            if _routes_differ(item) and item.comparison_id not in consumed
        )

    def _render_findings(self) -> "Iterator[str]":
        findings = self.document.analysis.findings
        counts = {severity: 0 for severity in ("actionable", "warning", "informational")}
        for finding in findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        summary = ", ".join(
            f"{count} {self.context.severity(severity, severity)}" for severity, count in counts.items() if count
        )
        title = f"-- findings ({len(findings)}"
        yield (
            f"{self.context.heading(title + ':')} {summary}{self.context.heading(') --')}"
            if summary
            else self.context.heading(title + ") --")
        )
        if not findings:
            yield from self._render_empty_finding_message(self._divergent_comparisons())
        yield from _narrative_lines(self.document, self.context)

    def _render_empty_finding_message(self, divergent: tuple[RouteComparison, ...]) -> "Iterator[str]":
        modules = self.document.capture.modules_since_install
        if divergent or self.document.analysis.explanations:
            yield "No problems were found. The comparisons below are informational."
        else:
            yield (
                f"No import-hook interference detected across {0 if modules is None else len(modules)} monitored imports."
            )

    def _render_comparisons(self, divergent: tuple[RouteComparison, ...]) -> "Iterator[str]":
        if not divergent:
            return
        yield ""
        yield self.context.heading(f"-- modules found by a custom finder ({len(divergent)}) --")
        yield (
            "For each module, the recorded result is compared with a fresh PathFinder search over the same"
            " path, run at report time. The fresh search reflects current state; it does not show which finder"
            " would have won during the run."
        )
        for comparison in divergent:
            yield from _route_comparison_lines(comparison, self.context, self.mutations)

    def _render_evidence(self) -> "Iterator[str]":
        unresolved = _unresolved_import_lines(self.document, self.context)
        if unresolved:
            yield ""
            yield from unresolved

    def _render_observation_sections(self) -> "Iterator[str]":
        if self.calls:
            yield ""
            yield self.context.heading("-- finder calls --")
            yield "Standard CPython finders are not wrapped, so their calls do not appear here."
            yield from _attribution_lines(self.calls, self.context)
        else:
            self._empty.append("finder calls")
        yield from self._render_contracts_and_timeline()
        yield from self._render_event_sections()
        yield from self._render_replays()

    def _render_event_sections(self) -> "Iterator[str]":
        """Render independently typed exhaustive-event sections."""
        yield from _iter_event_section(
            self._empty, self.mutations, "sys.meta_path mutations", _mutation_lines, self.context
        )
        yield from _iter_event_section(
            self._empty,
            self.buckets.meta_path_reassignments,
            "sys.meta_path reassignments",
            _reassignment_lines,
            self.context,
        )
        yield from _iter_event_section(
            self._empty,
            self.buckets.path_hook_mutations,
            "sys.path_hooks mutations",
            _path_hooks_mutation_lines,
            self.context,
        )
        yield from _iter_event_section(
            self._empty,
            self.buckets.path_hook_reassignments,
            "sys.path_hooks reassignments",
            _path_hooks_reassignment_lines,
            self.context,
        )
        yield from _iter_event_section(
            self._empty,
            self.buckets.sys_path_mutations,
            "sys.path mutations",
            _sys_path_mutation_lines,
            self.context,
            record_empty=self.document.sys_path_enabled,
        )
        yield from _iter_event_section(
            self._empty,
            self.buckets.sys_path_reassignments,
            "sys.path reassignments",
            _sys_path_reassignment_lines,
            self.context,
            record_empty=self.document.sys_path_enabled,
        )
        yield from _iter_event_section(
            self._empty,
            self.buckets.importer_cache_diffs,
            "sys.path_importer_cache changes",
            _importer_cache_diff_lines,
            self.context,
        )

    def _render_contracts_and_timeline(self) -> "Iterator[str]":
        contracts = _finder_contract_lines(self.document.analysis.finder_contracts, self.context)
        if contracts:
            yield ""
            yield from contracts
        if self.document.analysis.standard_resolutions:
            yield ""
            yield from _standard_resolution_lines(self.document.analysis.standard_resolutions, self.context)
        else:
            self._empty.append("imports attributed to standard finders")
        if self.document.analysis.events:
            yield ""
            yield from _timeline_lines(self.document, self.context)
        else:
            self._empty.append("timeline events")

    def _render_replays(self) -> "Iterator[str]":
        if not self.document.analysis.speculative_replay_enabled:
            return
        replay_lines = _speculative_replay_lines(self.document, self.context)
        if replay_lines:
            yield ""
            yield from replay_lines
        else:
            self._empty.append("speculative replays")

    def _render_errors(self) -> "Iterator[str]":
        error_count = len(self.errors) + len(self.document.analysis.report_errors)
        if not error_count:
            self._empty.append("internal errors")
            return
        yield ""
        yield self.context.negative(f"-- internal errors ({error_count}) --")
        for error in self.errors:
            yield _internal_error_line(error)
        for error in self.document.analysis.report_errors:
            yield f"during report in {error.where}: {error.exception_type_name}"

    def _render_inventory(self) -> "Iterator[str]":
        inventory = _loader_inventory_lines(self.document, self.context)
        if inventory:
            yield ""
            yield from inventory


def render_lines(document: ReportDocument, *, color: bool = False) -> list[str]:
    """Build the report body as a list of lines; the caller adds the trailing newline."""
    return list(iter_report_lines(document, color=color))


def iter_report_lines(document: ReportDocument, *, color: bool = False) -> "Iterator[str]":
    """Yield the report body one line at a time; the caller adds the trailing newline."""
    return _TextReportRenderer(document, color=color).iter_lines()


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
    shown_explanations = _primary_explanations(document.analysis.explanations) if document.analysis.explanations else ()
    explanations_by_cause: dict[str, list[CausalExplanation]] = {}
    for explanation in shown_explanations:
        if explanation.cause_finding_id is not None:
            explanations_by_cause.setdefault(explanation.cause_finding_id, []).append(explanation)
    ordered = sorted(
        document.analysis.findings, key=lambda item: (_SEVERITY_ORDER.get(item.severity, 1), item.finding_id)
    )
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
        block[0] = (
            f"{context.severity(f'[{number}]', finding.severity)} {_style_leading_tag(block[0], finding.severity, context)}"
        )
        if finding.signals:
            block.extend(f"    note: {_signal_description(signal)}" for signal in finding.signals)
        consequence = _WHY_IT_MATTERS.get(finding.kind)
        if consequence is not None:
            block.append(f"    why it matters: {consequence}")
        block.append(f"    {_finding_confidence_line(finding, context)}")
        block.append(f"    guide: {_REPORT_GUIDE_URL}#{finding.kind.replace('_', '-')}")
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
            explanation,
            context,
            explanations_by_id={item.explanation_id: item for item in document.analysis.explanations},
        )
        block[0] = f"[{number}] {block[0]}"
        block[0] = _style_leading_tag(block[0], "warning", context, count=2)
        lines.extend(block)

    if informational:
        lines.append(f"{context.severity('informational', 'informational')} ({len(informational)}):")
        lines.extend(
            f"    {_style_leading_tag(_finding_lines(finding, context)[0], 'informational', context)}"
            for finding in informational
        )
    return lines


def render_failure_lines(error_name: str, *, color: bool = False) -> list[str]:
    """Render the minimal fallback report after document generation fails."""
    if not color:
        return ["== metapathology report ==", f"report generation failed: {error_name}"]
    return [
        f"{_ANSI_BOLD_CYAN}== metapathology report =={_ANSI_RESET}",
        f"{_ANSI_BOLD_RED}report generation failed:{_ANSI_RESET} {error_name}",
    ]


def _style_leading_tag(line: str, severity: str, context: _RenderContext, count: int = 1) -> str:
    """Style up to ``count`` leading bracketed markers without touching captured values."""
    offset = 0
    styled = line
    for _ in range(count):
        start = styled.find("[", offset)
        if start < 0 or styled[offset:start].strip():
            break
        end = styled.find("]", start + 1)
        if end < 0:
            break
        token = styled[start : end + 1]
        replacement = context.severity(token, severity)
        styled = styled[:start] + replacement + styled[end + 1 :]
        offset = start + len(replacement)
    return styled


_MAX_UNRESOLVED_ATTEMPTS = 25


def _unresolved_import_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """List imports that started but left no module, joining them to legacy-only finders."""
    unresolved = unresolved_attempts(document.analysis.attempts)
    if not unresolved:
        return []
    missing = None if document.analysis.target_outcome is None else document.analysis.target_outcome.missing_module
    ordered = sorted(unresolved, key=lambda attempt: (attempt.fullname != missing, attempt.start_event_seq))
    lines = [context.heading(f"-- imports that started but produced no module ({len(unresolved)}) --")]
    lines.append("Failed optional imports are normal; an entry matters when your program needed the module.")
    for attempt in ordered[:_MAX_UNRESOLVED_ATTEMPTS]:
        marker = " (the failed module)" if attempt.fullname == missing else ""
        lines.append(
            f"'{attempt.fullname}': import started at event #{attempt.start_event_seq}, "
            f"{_progress_description(attempt.progress)}{marker}{context.thread_suffix(attempt.thread_name)}"
        )
    if len(ordered) > _MAX_UNRESOLVED_ATTEMPTS:
        lines.append(f"... and {len(ordered) - _MAX_UNRESOLVED_ATTEMPTS} more; full list in the JSON report")
    numbers = _headline_finding_numbers(document)
    legacy = [finding for finding in document.analysis.findings if finding.kind == "legacy_finder_contract"]
    for finding in legacy:
        reference = f" — see [{numbers[finding.finding_id]}]" if finding.finding_id in numbers else ""
        lines.append(
            f"note: {finding.module} was on sys.meta_path but accepts only the legacy find_module protocol; "
            f"CPython 3.12+ never calls it{reference}"
        )
    return lines


def _headline_finding_numbers(document: ReportDocument) -> dict[str, int]:
    """Visible block numbers assigned to non-informational findings in display order."""
    ordered = sorted(
        document.analysis.findings, key=lambda item: (_SEVERITY_ORDER.get(item.severity, 1), item.finding_id)
    )
    return {
        finding.finding_id: number
        for number, finding in enumerate(
            (finding for finding in ordered if finding.severity != "informational"), start=1
        )
    }


def _finding_confidence_line(finding: Finding, context: _RenderContext) -> str:
    """State severity, evidence level, and limitations as one prose sentence."""
    limitations = ", ".join(item.replace("_", " ") for item in finding.limitations)
    suffix = f"; limitations: {limitations}" if limitations else ""
    article = "an" if finding.severity == "actionable" else "a"
    severity = context.severity(finding.severity, finding.severity)
    return f"this is {article} {severity} finding based on {_humanize(finding.evidence_level)} evidence{suffix}"


def _explanation_lines(
    explanation: CausalExplanation,
    context: _RenderContext,
    nested: bool = False,
    explanations_by_id: "dict[str, CausalExplanation] | None" = None,
) -> list[str]:
    """Render one deterministic evidence join before its atomic findings."""
    renderer = _EXPLANATION_RENDERERS.get(explanation.kind)
    if renderer is not None:
        return renderer(explanation, context, nested, explanations_by_id)
    return [f"[{explanation.confidence}] {explanation.kind}: '{explanation.subject}'"]


def _render_namespace_truncation_explanation(
    explanation: CausalExplanation, context: _RenderContext, nested: bool, _by_id: object
) -> list[str]:
    status = "failed" if explanation.effect_status == "failed" else "was attempted with outcome unknown"
    lines = [
        f"[{explanation.confidence}] '{explanation.subject}' {status} after {explanation.finder_type_name} truncated its parent namespace",
        f"    omitted location {context.quoted_path(explanation.omitted_location)} contains {context.quoted_path(explanation.candidate_path)}",
        ("    supporting events: " if nested else f"    cause: {explanation.cause_finding_id}; supporting events: ")
        + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
    ]
    if explanation.next_observation == "enable_deep_import_outcomes":
        lines.append("    next step: rerun with --deep-import-outcomes to confirm whether the submodule import failed")
    return lines


def _render_standard_precedence_explanation(
    explanation: CausalExplanation, context: _RenderContext, _nested: bool, _by_id: object
) -> list[str]:
    qualifier = "produced" if explanation.confidence == "captured" else "likely produced"
    lines = [
        f"[{explanation.confidence}] {explanation.finder_type_name} {qualifier} {explanation.effect_status} for '{explanation.subject}' before later finders",
        f"    later unreachable finders: {', '.join(explanation.later_finders)}; origin: {_origin_display(explanation.origin, context)}",
    ]
    if explanation.standard_attempt_id is not None:
        lines.append(f"    attempt: attempt:{explanation.standard_attempt_id}")
    if explanation.next_observation == "enable_deep_import_outcomes":
        lines.append("    next step: rerun with --deep-import-outcomes to record the actual PathFinder result")
    return lines


def _render_namespace_candidate_explanation(
    explanation: CausalExplanation, context: _RenderContext, _nested: bool, _by_id: object
) -> list[str]:
    lines = [
        f"[{explanation.confidence}] '{explanation.subject}': {explanation.finder_type_name} found a namespace candidate from {context.quoted_path(explanation.candidate_path)}",
        f"    it continued searching and selected the regular module at {_origin_display(explanation.origin, context)}",
        "    supporting events: " + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
    ]
    if explanation.effect_status == "descendant_failed":
        lines.insert(2, "    the selected module was not a package, and the descendant import then failed")
    return lines


def _render_repeated_load_explanation(
    explanation: CausalExplanation, context: _RenderContext, _nested: bool, _by_id: object
) -> list[str]:
    return [
        f"[correlated] the same origin was selected again for '{explanation.subject}' by {explanation.finder_type_name}",
        f"    origin: {_origin_display(explanation.origin, context)}",
        "    the earlier import loaded, but the later import failed; the exception message was not captured",
        "    supporting events: " + ", ".join(f"#{seq}" for seq in explanation.event_seqs),
    ]


def _render_finder_side_effect_explanation(
    explanation: CausalExplanation, _context: _RenderContext, _nested: bool, _by_id: object
) -> list[str]:
    outcome = "raised" if explanation.effect_status == "finder_raised" else "returned None"
    return [
        f"[captured] {explanation.finder_type_name} {outcome} after changing sys.modules['{explanation.subject}']",
        f"    during {_bare_boundary_label(explanation.boundary)}: {_module_transition(explanation.state_before, explanation.state_after)}",
        "    nested activity was not observed; the boundary delta does not identify the internal cause",
    ]


def _render_module_identity_explanation(
    explanation: CausalExplanation, _context: _RenderContext, _nested: bool, _by_id: object
) -> list[str]:
    if explanation.kind == "repeated_loader_execution":
        return [
            f"[captured] {explanation.finder_type_name} executed '{explanation.subject}' again with a different module object",
            f"    during {_bare_boundary_label(explanation.boundary)}: {_module_transition(explanation.state_before, explanation.state_after)}",
            "    this is not an ordinary reload, which normally reuses the existing module object",
        ]
    action = (
        "executed a separate module object for"
        if explanation.effect_status == "separate_module_executed"
        else "replaced the module identity for"
    )
    return [
        f"[captured] {explanation.finder_type_name} {action} '{explanation.subject}'",
        f"    during {_bare_boundary_label(explanation.boundary)}: {_module_transition(explanation.state_before, explanation.state_after)}",
        "    both endpoint identities are exact; intermediate steps remain unknown",
    ]


def _render_ambiguous_explanation(
    explanation: CausalExplanation,
    _context: _RenderContext,
    _nested: bool,
    explanations_by_id: "dict[str, CausalExplanation] | None",
) -> list[str]:
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


_EXPLANATION_RENDERERS: "dict[str, Callable[..., list[str]]]" = {
    "namespace_truncation_failure": _render_namespace_truncation_explanation,
    "standard_winner_precedence": _render_standard_precedence_explanation,
    "namespace_candidate_displaced": _render_namespace_candidate_explanation,
    "repeated_load_failure": _render_repeated_load_explanation,
    "finder_side_effect": _render_finder_side_effect_explanation,
    "module_replacement": _render_module_identity_explanation,
    "repeated_loader_execution": _render_module_identity_explanation,
    "ambiguous_contention": _render_ambiguous_explanation,
}


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


def _mechanism_header_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    lines: list[str] = []
    if document.capture.deep_diagnostics:
        warning = context.warning("WARNING:")
        lines.append(
            f"{warning} opt-in deep diagnostics are enabled; they wrap import-machinery callables "
            "and may affect identity checks"
        )
        lines.append(f"deep diagnostics enabled: {', '.join(document.capture.deep_diagnostics)}")
    if document.capture.deep_import_outcomes_status != "disabled":
        lines.append(f"import outcome observation: {_humanize(document.capture.deep_import_outcomes_status)}")
    if document.capture.deep_import_calls_status != "disabled":
        lines.append(f"import call observation: {_humanize(document.capture.deep_import_calls_status)}")
    if document.capture.standard_finder_status != "disabled":
        lines.append(f"PathFinder result capture: {_humanize(document.capture.standard_finder_status)}")
    bootstrap = document.capture.early_site_bootstrap
    if bootstrap is not None:
        lines.append(f"early site bootstrap: {context.display_path(bootstrap.path)}")
        lines.append(f"bootstrap site-packages: {context.display_path(bootstrap.site_packages)}")
        lines.append(f"bootstrap activation: {bootstrap.activation_source}")
        earlier = _names_line(bootstrap.earlier_pth_files) if bootstrap.earlier_pth_files else "(none)"
        lines.append(f"earlier .pth files outside capture: {earlier}")
    frozen_bootstrap = document.capture.frozen_bootstrap
    if frozen_bootstrap is not None:
        lines.append(f"frozen integration: {frozen_bootstrap.integration}")
        lines.append(f"frozen bootstrap: {context.display_path(frozen_bootstrap.path)}")
        lines.append(f"frozen observation boundary: {frozen_bootstrap.boundary}")
    return lines


def _snapshot_header_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    lines: list[str] = []
    if document.meta_path.current == document.meta_path.initial:
        lines.append(f"sys.meta_path (unchanged since install): {_names_line(document.meta_path.initial)}")
        displayed_finders: tuple[str, ...] = document.meta_path.initial
    else:
        current_meta_path = ("<unavailable>",) if document.meta_path.current is None else document.meta_path.current
        lines.append(f"sys.meta_path at install: {_names_line(document.meta_path.initial)}")
        lines.append(f"sys.meta_path now: {_names_line(current_meta_path)}")
        displayed_finders = document.meta_path.initial + current_meta_path
    for name, annotation in _KNOWN_SHIM_ANNOTATIONS.items():
        if name in displayed_finders:
            lines.append(f"    {name} is {annotation}")

    if document.path_hooks.enabled:
        current_hooks = document.path_hooks.current
        if current_hooks is not None and _ref_signatures(current_hooks) == _ref_signatures(document.path_hooks.initial):
            lines.append(f"sys.path_hooks (unchanged since install): {context.hook_refs(document.path_hooks.initial)}")
        else:
            now = "<unavailable>" if current_hooks is None else context.hook_refs(current_hooks)
            lines.append(f"sys.path_hooks at install: {context.hook_refs(document.path_hooks.initial)}")
            lines.append(f"sys.path_hooks now: {now}")

    if document.importer_cache.enabled:
        initial_count = len(document.importer_cache.initial)
        cache = document.importer_cache.current
        current_count = "<unavailable>" if cache is None else str(len(cache))
        line = f"sys.path_importer_cache: {initial_count} -> {current_count} entries"
        initial_non_string = document.importer_cache.initial_non_string_keys
        current_non_string = document.importer_cache.current_non_string_keys or 0
        if initial_non_string or current_non_string:
            if initial_non_string == current_non_string:
                line += f" ({initial_non_string} non-string keys omitted)"
            else:
                line += f" ({initial_non_string} -> {current_non_string} non-string keys omitted)"
        lines.append(line)
    return lines


def _inventory_header_lines(document: ReportDocument) -> list[str]:
    lines: list[str] = []
    standard_skipped = [item for item in document.analysis.skipped_finders if item.expected]
    other_skipped = [item for item in document.analysis.skipped_finders if not item.expected]
    if standard_skipped:
        lines.append(
            "standard CPython finders left unwrapped (expected): "
            + _names_line(tuple(item.finder_type_name for item in standard_skipped))
        )
    if other_skipped:
        lines.append("other finders observed but not instrumented (direct attribution unavailable):")
        lines.extend(f"    {item.finder_type_name}: {item.reason}" for item in other_skipped)
    return lines


def _verdict_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Lead with the target's outcome and a one-sentence reading of the evidence."""
    lines: list[str] = []
    outcome = document.analysis.target_outcome
    unresolved = unresolved_attempts(document.analysis.attempts)
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
        failed = outcome.kind == "raised" or (outcome.exit_code is not None and outcome.exit_code != 0)
        prefix = context.negative("target outcome:") if failed else context.positive("target outcome:")
        lines.append(prefix + line[len("target outcome:") :])

    summary = document.analysis.summary
    counts = [
        f"{count} {context.severity(severity, severity)}"
        for severity, count in (
            ("actionable", summary.actionable),
            ("warning", summary.warning),
            ("informational", summary.informational),
        )
        if count
    ]
    numbers = _headline_finding_numbers(document)
    total = len(document.analysis.findings)
    breakdown = f"{total} finding{'' if total == 1 else 's'}"
    if len(counts) > 1:
        breakdown += f" ({', '.join(counts)})"
    elif counts:
        breakdown = f"{counts[0]} finding{'' if total == 1 else 's'}"
    top = next(
        (finding for finding in document.analysis.findings if finding.finding_id == summary.top_finding_id), None
    )
    if top is not None:
        tag = top.kind.replace("_", "-")
        prefix = context.severity("verdict:", top.severity)
        marker = context.severity(f"[{tag}]", top.severity)
        number = context.severity(f"[{numbers[top.finding_id]}]", top.severity)
        lines.append(f"{prefix} {breakdown}; most severe is {marker} '{top.module}' — see {number}")
    elif counts:
        severity = "warning" if summary.warning else "informational"
        lines.append(f"{context.severity('verdict:', severity)} {breakdown}")
    elif any(_routes_differ(comparison) for comparison in document.analysis.route_comparisons):
        lines.append(
            f"{context.severity('verdict:', 'informational')} no problems found; some modules were found by a "
            "custom finder instead of the standard path search — listed below for review"
        )
    else:
        module_count = (
            0 if document.capture.modules_since_install is None else len(document.capture.modules_since_install)
        )
        lines.append(
            f"{context.positive('verdict:')} no import-hook interference detected across {module_count} monitored imports"
        )
    return lines


def _monitoring_line(document: ReportDocument) -> str:
    """One-line mechanism summary replacing the per-mechanism enabled/disabled lines."""
    if not document.capture.monitor_enabled:
        return "monitoring: disabled"
    mechanisms = ["sys.meta_path"]
    notes: list[str] = []
    for name, enabled in (
        ("sys.path_hooks", document.path_hooks.enabled),
        ("sys.path_importer_cache", document.importer_cache.enabled),
    ):
        if enabled:
            mechanisms.append(name)
        else:
            notes.append(f"{name} off")
    if document.sys_path_enabled:
        mechanisms.append("sys.path")
    line = "monitoring: " + ", ".join(mechanisms)
    if notes:
        line += " (" + "; ".join(notes) + ")"
    return line


def _humanize(value: str) -> str:
    """Render one internal snake_case value as plain words."""
    return value.replace("_", " ")


# Plain-language descriptions for machine-readable route/finding signals.
_SIGNAL_DESCRIPTIONS = {
    "meta_path_short_circuit": ("this finder ran before PathFinder, so the standard path search never saw the module"),
}


def _signal_description(signal: str) -> str:
    """Explain a known signal in plain words, humanizing unknown values."""
    return _SIGNAL_DESCRIPTIONS.get(signal, _humanize(signal))


# Plain-language descriptions for unresolved-attempt progress states.
_PROGRESS_DESCRIPTIONS = {
    "started": "no finder result was recorded",
    "finder_claimed": "a finder returned a spec but no module appeared",
    "finder_raised": "a finder raised an exception",
    "failed": "the import failed",
}


def _progress_description(progress: str) -> str:
    """Explain how far an unresolved import got, humanizing unknown values."""
    return _PROGRESS_DESCRIPTIONS.get(progress, _humanize(progress))


# Recorded type names that identify a plain callable rather than a class;
# showing them would read as noise ("path hook function"), so omit them.
_GENERIC_CALLABLE_TYPE_NAMES = frozenset({"function", "builtin_function_or_method", "method", "method-wrapper"})

# Rendered for deep calls that ran nested inside another observed call; the
# timeline preamble explains the phrase once when it appears.
_NESTED_CALL_OUTCOME = "nested; result not recorded"


def _deep_call_outcome(event: DeepDiagnosticCall) -> str:
    """Translate a recorded deep-call outcome into plain words."""
    if event.outcome == "raised":
        name = event.exception_type_name or "an exception"
        # Path hooks signal "this path entry is not mine" by raising ImportError.
        if event.boundary == "path_hook" and (event.exception_type_name or "").endswith("ImportError"):
            return f"raised {name} (declined the path)"
        return f"raised {name}"
    if event.outcome == "found":
        return "found it"
    if event.outcome == "not_found":
        return "found nothing"
    if event.outcome == "unobserved_reentrant":
        return _NESTED_CALL_OUTCOME
    if event.outcome == "returned":
        return "returned a finder" if event.boundary == "path_hook" else "returned"
    return _humanize(event.outcome)


def _deep_call_line(event: DeepDiagnosticCall, context: _RenderContext) -> str:
    """Render one deep-diagnostic call naming who was called and about what."""
    outcome = _deep_call_outcome(event)
    transition = _module_transition_suffix(event.module_state_before, event.module_state_after)
    thread = context.thread_suffix(event.thread_name)
    if event.boundary == "path_hook":
        hook = "" if event.object_type_name in _GENERIC_CALLABLE_TYPE_NAMES else f"{event.object_type_name} "
        subject = "<unknown>" if event.path is None else context.quoted_path(event.path)
        finder = "" if event.returned_finder is None else f" ({event.returned_finder.type_name})"
        return f"#{event.seq} path hook {hook}called with {subject}: {outcome}{finder}{transition}{thread}"
    if event.boundary == "path_entry_finder":
        where = "" if event.path is None else f" for {context.quoted_path(event.path)}"
        return (
            f"#{event.seq} path entry finder {event.object_type_name}{where} "
            f"asked about {event.fullname!r}: {outcome}{transition}{thread}"
        )
    if event.boundary in ("loader_create_module", "loader_exec_module"):
        method = "create_module()" if event.boundary == "loader_create_module" else "exec_module()"
        return f"#{event.seq} loader {event.object_type_name}.{method} for {event.fullname!r}: {outcome}{transition}{thread}"
    subject = event.fullname if event.fullname is not None else event.path
    return f"#{event.seq} {_humanize(event.boundary)} {subject or '<unknown>'}: {outcome}{transition}{thread}"


_BARE_BOUNDARY_LABELS = {
    "find_spec": "the finder's find_spec() call",
    "path_hook": "a path hook call",
    "path_entry_finder": "a path entry finder call",
    "loader_create_module": "the loader's create_module() call",
    "loader_exec_module": "the loader's exec_module() call",
}


def _bare_boundary_label(boundary: str | None) -> str:
    """Name a deep-diagnostic call site as a noun phrase."""
    if boundary is None:
        return "an unrecorded call"
    return _BARE_BOUNDARY_LABELS.get(boundary, _humanize(boundary))


# Plain-language labels for importer-cache observation boundaries.
_OBSERVATION_LABELS = {
    "install": "at install",
    "report": "at report time",
    "before_path_hooks_mutation": "before a sys.path_hooks change",
    "after_path_hooks_mutation": "after a sys.path_hooks change",
}


def _observation_label(observation: str) -> str:
    """Name the boundary where a cache snapshot was taken."""
    return _OBSERVATION_LABELS.get(observation, _humanize(observation))


def _ref_signatures(references: tuple[ObjectRef, ...]) -> tuple[tuple[int, str, str | None], ...]:
    """Comparable identity tuples for detecting an unchanged snapshot."""
    return tuple((reference.object_id, reference.type_name, reference.name) for reference in references)


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


def _finder_contract_lines(contracts: tuple[FinderContract, ...], context: _RenderContext) -> list[str]:
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
    lines = [context.heading(f"-- finder API contracts ({len(contracts)} observed entries) --")]
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
        lines.append(f"... {omitted} more risk/standard entries; full list in the JSON report")
    return lines


def _standard_resolution_lines(resolutions: tuple[StandardResolution, ...], context: _RenderContext) -> list[str]:
    """Render a bounded projection without hiding evidence provenance."""
    lines = [context.heading(f"-- imports attributed to standard finders ({len(resolutions)}) --")]
    if any(resolution.evidence_level == "inferred" for resolution in resolutions):
        lines.append(
            "Entries marked [inferred] combine import order with module metadata read at report time; "
            "the find_spec() call itself was not recorded."
        )
    for resolution in resolutions[:_STANDARD_RESOLUTION_DISPLAY_LIMIT]:
        origin = "None" if resolution.origin is None else context.quoted_path(resolution.origin)
        line = (
            f"[{_humanize(resolution.evidence_level)}] {resolution.fullname!r}: "
            f"{resolution.finder_type_name} produced {resolution.category} "
            f"(loader {resolution.loader_type_name}, origin {origin})"
        )
        lines.append(line)
        if resolution.later_finders:
            lines.append(f"    later meta path entries were never reached: {_names_line(resolution.later_finders)}")
        if resolution.component_event_seqs:
            references = ", ".join(f"#{seq}" for seq in resolution.component_event_seqs)
            lines.append(f"    related path entry finder calls: {references}")
    omitted = len(resolutions) - _STANDARD_RESOLUTION_DISPLAY_LIMIT
    if omitted > 0:
        lines.append(f"... {omitted} more; full list in the JSON report")
    return lines


def _timeline_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Render the timeline with shared context hoisted into a preamble."""
    events = document.analysis.events
    lines = [context.heading(f"-- event timeline ({len(events)}) --")]
    if context.only_thread is not None:
        lines.append(f"Events are numbered in the order they were recorded; all ran on thread {context.only_thread}.")
    else:
        lines.append(
            "Events are numbered in the order they were recorded; concurrent threads have no exact wall-clock order."
        )

    audits = [event for event in events if isinstance(event, ImportAuditStart)]
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

    if any(isinstance(event, DeepDiagnosticCall) and event.outcome == "unobserved_reentrant" for event in events):
        lines.append(
            f"Calls marked '{_NESTED_CALL_OUTCOME}' ran inside another observed call "
            "(for example during a module's exec_module()); they happened normally but their results were not captured."
        )

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
    collapsed = _collapse_timeline_entries(entries, protected)
    if len(collapsed) < len(entries):
        lines.append("Runs of routine events are collapsed; the JSON report always lists every event.")
    lines.extend(collapsed)
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
        elif isinstance(event, DeepDiagnosticCall):
            routine = event.outcome in ("not_found", "unobserved_reentrant")
            collapsible = (
                routine and _module_transition_suffix(event.module_state_before, event.module_state_after) == ""
            )
        entries.append((event, main_line, continuation, collapsible))
    return entries


def _protected_event_positions(document: ReportDocument, events: "tuple[MonitorEvent, ...]") -> set[int]:
    """Positions (by index, not seq) that must always render expanded, plus one neighbor each side."""
    referenced_seqs: set[int] = set()
    for finding in document.analysis.findings:
        referenced_seqs.update(finding.supporting_event_seqs)
        claim = finding_claim(finding)
        if claim is not None:
            referenced_seqs.add(claim.seq)
        deep_call = finding_deep_call(finding)
        if deep_call is not None:
            referenced_seqs.add(deep_call.seq)
    for explanation in document.analysis.explanations:
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
    deep_misses = sum(1 for event, *_ in run if isinstance(event, DeepDiagnosticCall) and event.outcome == "not_found")
    nested = sum(
        1 for event, *_ in run if isinstance(event, DeepDiagnosticCall) and event.outcome == "unobserved_reentrant"
    )
    clauses = []
    if starts:
        clauses.append(f"{starts} import start{'' if starts == 1 else 's'}")
    if declined:
        clauses.append(f"{declined} finder call{'' if declined == 1 else 's'} returning None")
    if deep_misses:
        clauses.append(f"{deep_misses} path entry finder call{'' if deep_misses == 1 else 's'} finding nothing")
    if nested:
        clauses.append(f"{nested} nested call{'' if nested == 1 else 's'}")
    detail = _join_names(clauses)
    return f"#{first_seq}-#{last_seq}: {detail} (collapsed)"


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


def _import_call_line(event: ImportCall, context: _RenderContext) -> str:
    """Render one ``builtins.__import__`` call, flagging cache hits."""
    target = ("." * event.level) + event.name if event.level else event.name
    spec = f"__import__({target!r}"
    if event.fromlist:
        spec += f", fromlist={list(event.fromlist)!r}"
    spec += ")"
    by = "" if event.importing_module is None else f" by {event.importing_module!r}"
    if event.exception_type_name is not None:
        outcome = f"raised {event.exception_type_name}"
    elif event.module_state_before.state == "object":
        # The module was already loaded before the call, so __import__ returned
        # it straight from sys.modules without any finder or audit event.
        outcome = context.warning("cache hit")
    else:
        outcome = "returned"
    return f"#{event.seq} {spec}{by}: {outcome}{context.thread_suffix(event.thread_name)}"


def _timeline_deep_import_event(event: DeepImportEvent, context: _RenderContext) -> str:
    return f"#{event.seq} import of {event.fullname!r}: {event.outcome}{context.thread_suffix(event.thread_name)}"


def _timeline_import_audit_start(event: ImportAuditStart, context: _RenderContext) -> str:
    # Stable snapshot context lives in the timeline preamble; deviations are
    # appended by _timeline_lines as continuation lines.
    return f"#{event.seq} import started: {event.fullname!r}{context.thread_suffix(event.thread_name)}"


def _timeline_find_spec_call(event: FindSpecCall, context: _RenderContext) -> str:
    if event.exception_type_name is not None:
        outcome = f"raised {event.exception_type_name}"
    elif event.found:
        origin = "None" if event.origin is None else context.quoted_path(event.origin)
        outcome = f"found it (loader {event.loader_type_name}, origin {origin})"
    else:
        outcome = "returned None"
    finder = context.finder_label(event.finder_type_name, event.finder_id)
    transition = _module_transition_suffix(event.module_state_before, event.module_state_after)
    return (
        f"#{event.seq} {finder}.find_spec({event.fullname!r}): {outcome}{transition}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_standard_finder_call(event: StandardFinderCall, context: _RenderContext) -> str:
    loader = event.spec_summary.loader
    loader_name = "None" if loader is None else loader.type_name
    return (
        f"#{event.seq} captured {event.finder_type_name} result for {event.fullname!r}: loader {loader_name}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_importer_cache_diff(event: ImporterCacheDiff, context: _RenderContext) -> str:
    deltas: list[str] = []
    if event.added:
        deltas.append(context.positive(f"+{len(event.added)}"))
    if event.removed:
        deltas.append(context.negative(f"-{len(event.removed)}"))
    if event.replaced:
        deltas.append(context.warning(f"~{len(event.replaced)}"))
    delta = " ".join(deltas) if deltas else "(no changes)"
    return (
        f"#{event.seq} sys.path_importer_cache {_observation_label(event.observation)}: {delta}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_meta_path_mutation(event: MetaPathMutation, context: _RenderContext) -> str:
    return (
        f"#{event.seq} sys.meta_path {event.op} {_timeline_delta(event.added, event.removed, context)}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_meta_path_reassignment(event: MetaPathReassignment, context: _RenderContext) -> str:
    return (
        f"#{event.seq} sys.meta_path reassignment detected during {event.during_import!r}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_path_hooks_mutation(event: PathHooksMutation, context: _RenderContext) -> str:
    return (
        f"#{event.seq} sys.path_hooks {event.op}: "
        f"{context.positive(f'+{len(event.added)}')} {context.negative(f'-{len(event.removed)}')}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_path_hooks_reassignment(event: PathHooksReassignment, context: _RenderContext) -> str:
    return (
        f"#{event.seq} sys.path_hooks reassignment detected during {event.during_import!r}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_sys_path_mutation(event: SysPathMutation, context: _RenderContext) -> str:
    return (
        f"#{event.seq} sys.path {event.op} {_timeline_delta(event.added, event.removed, context)}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_sys_path_reassignment(event: SysPathReassignment, context: _RenderContext) -> str:
    return (
        f"#{event.seq} sys.path reassignment detected during {event.during_import!r}"
        f"{context.thread_suffix(event.thread_name)}"
    )


def _timeline_internal_error(event: InternalError, context: _RenderContext) -> str:
    return _internal_error_line(event)


# One line renderer per event type, dispatched by concrete type in _timeline_line.
_TIMELINE_LINE_BUILDERS: "dict[type[MonitorEvent], Callable[..., str]]" = {
    DeepDiagnosticCall: _deep_call_line,
    DeepImportEvent: _timeline_deep_import_event,
    ImportCall: _import_call_line,
    ImportAuditStart: _timeline_import_audit_start,
    FindSpecCall: _timeline_find_spec_call,
    StandardFinderCall: _timeline_standard_finder_call,
    ImporterCacheDiff: _timeline_importer_cache_diff,
    MetaPathMutation: _timeline_meta_path_mutation,
    MetaPathReassignment: _timeline_meta_path_reassignment,
    PathHooksMutation: _timeline_path_hooks_mutation,
    PathHooksReassignment: _timeline_path_hooks_reassignment,
    SysPathMutation: _timeline_sys_path_mutation,
    SysPathReassignment: _timeline_sys_path_reassignment,
    InternalError: _timeline_internal_error,
}


def _timeline_line(event: MonitorEvent, context: _RenderContext) -> str:
    """Render one compact line using only data captured in the event record."""
    return _TIMELINE_LINE_BUILDERS[type(event)](event, context)


def _timeline_delta(added: tuple[str, ...], removed: tuple[str, ...], context: _RenderContext) -> str:
    """Format a compact meta-path delta."""
    parts: list[str] = []
    if added:
        parts.append(context.positive("+" + _names_line(added)))
    if removed:
        parts.append(context.negative("-" + _names_line(removed)))
    return " ".join(parts) if parts else "(order change)"


# Display-only notes for finder classes that well-known environment tooling
# installs at startup. Rendered once as a header note, never inline in list
# snapshots, and never used by severity or finding logic.
_KNOWN_SHIM_ANNOTATIONS = {"_Finder": "installed by virtualenv at startup; its presence is expected"}


def _names_line(names: tuple[str, ...]) -> str:
    """Format finder names as a bracketed, comma-separated list."""
    return "[" + ", ".join(names) + "]"


def _loader_inventory_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Render a bounded view of the exhaustive post-hoc loader inventory.

    Text shows loader groups that participated in captured custom claims and
    any metadata disagreements; JSON retains the exhaustive inventory.
    """
    inventory = document.analysis.loader_inventory
    if not inventory.available:
        return [context.heading("-- loaders of imported modules --"), "(sys.modules snapshot unavailable)"]
    groups: dict[str | None, list[ModuleMetadata]] = {}
    for entry in inventory.entries:
        if entry.inspection != "available":
            continue
        label = None if entry.loader is None else entry.loader.type_name
        groups.setdefault(label, []).append(entry)
    claimed_loaders = {
        event.loader_type_name
        for event in document.analysis.events
        if isinstance(event, FindSpecCall) and event.found and event.loader_type_name is not None
    }
    custom = sorted(label for label in groups if label is not None and label in claimed_loaders)
    disagreements = [
        entry for entry in inventory.entries if entry.inspection == "available" and entry.loader_agreement is False
    ]
    relevant_names = set(document.capture.modules_since_install or ())
    relevant_names.update(
        event.fullname for event in document.analysis.events if isinstance(event, FindSpecCall) and event.found
    )
    unavailable = sorted(
        (entry for entry in inventory.entries if entry.inspection != "available" and entry.name in relevant_names),
        key=lambda entry: entry.name,
    )
    if not custom and not disagreements and not unavailable and not inventory.non_string_keys:
        return []
    lines = [
        context.heading(
            f"-- loaders of imported modules ({len(inventory.entries)} entries; full list in the JSON report) --"
        )
    ]
    lines.append(
        "Only custom loaders and metadata problems are shown. This reflects module metadata at report time,"
        " which may differ from how each module was originally loaded."
    )
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


def _metadata_value(value: "str | ObjectRef | None", context: _RenderContext) -> str:
    """Format already-reduced metadata without consulting live objects."""
    if isinstance(value, ObjectRef):
        return f"<{_ref_label(value)}>"
    if value is None:
        return "None"
    return context.quoted_path(value)


def _mutation_lines_core(
    mutation: "MetaPathMutation | PathHooksMutation | SysPathMutation",
    context: _RenderContext,
    *,
    after_label: str,
    format_values: "Callable[..., str]",
) -> list[str]:
    """Format any monitored-list mutation: op, delta, resulting contents, and user stack.

    ``format_values`` renders one entry snapshot (finder names for meta_path and
    sys.path, path-hook references for sys.path_hooks); ``after_label`` names the
    list in the resulting-contents line.
    """
    delta_parts: list[str] = []
    if mutation.added:
        delta_parts.append("+" + format_values(mutation.added))
    if mutation.removed:
        delta_parts.append("-" + format_values(mutation.removed))
    delta = " ".join(delta_parts) if delta_parts else "(order change)"
    lines = [f"#{mutation.seq} {mutation.op} {delta}{context.thread_suffix(mutation.thread_name)}"]
    lines.append(f"    {after_label}: {format_values(mutation.contents_after)}")
    lines.extend(_stack_lines(mutation.stack, context))
    return lines


def _reassignment_lines_core(
    reassignment: "MetaPathReassignment | PathHooksReassignment | SysPathReassignment",
    context: _RenderContext,
    *,
    mechanism: str,
    format_values: "Callable[..., str]",
) -> list[str]:
    """Format any monitored-list reassignment with before/after contents and the detection stack."""
    lines = [
        f"#{reassignment.seq} {mechanism} REASSIGNED, detected during import of "
        f"'{reassignment.during_import}'{context.thread_suffix(reassignment.thread_name)}"
    ]
    lines.append(f"    before: {format_values(reassignment.old_contents)}")
    lines.append(f"    after:  {format_values(reassignment.new_contents)}")
    lines.append("    instrumentation reinstalled; stack shows the triggering import, not the reassignment itself:")
    lines.extend(_stack_lines(reassignment.stack, context))
    return lines


def _mutation_lines(mutation: MetaPathMutation, context: _RenderContext) -> list[str]:
    return _mutation_lines_core(mutation, context, after_label="meta_path after", format_values=_names_line)


def _reassignment_lines(reassignment: MetaPathReassignment, context: _RenderContext) -> list[str]:
    return _reassignment_lines_core(reassignment, context, mechanism="sys.meta_path", format_values=_names_line)


def _path_hooks_mutation_lines(mutation: PathHooksMutation, context: _RenderContext) -> list[str]:
    return _mutation_lines_core(mutation, context, after_label="path_hooks after", format_values=context.hook_refs)


def _path_hooks_reassignment_lines(reassignment: PathHooksReassignment, context: _RenderContext) -> list[str]:
    return _reassignment_lines_core(reassignment, context, mechanism="sys.path_hooks", format_values=context.hook_refs)


def _cache_entry_line(entry: ImporterCacheEntry, context: _RenderContext) -> str:
    """Format one captured cache value without inspecting the live finder."""
    finder = _cache_finder_name(entry.finder)
    return f"{context.quoted_path(entry.path)} -> {finder}"


def _sys_path_mutation_lines(mutation: SysPathMutation, context: _RenderContext) -> list[str]:
    return _mutation_lines_core(mutation, context, after_label="sys.path after", format_values=_names_line)


def _sys_path_reassignment_lines(reassignment: SysPathReassignment, context: _RenderContext) -> list[str]:
    return _reassignment_lines_core(reassignment, context, mechanism="sys.path", format_values=_names_line)


def _cache_finder_name(finder: ObjectRef | None, force_id: bool = False) -> str:
    """Format a captured cache finder or its negative marker.

    Cache finders are keyed by path, so their ids are usually noise;
    ``force_id`` is used only when a replacement's before/after labels would
    otherwise read identically.
    """
    if finder is None:
        return "None (no importer for this path)"
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


_SPECULATIVE_REPLAY_OUTCOMES = {
    "returned_none": "currently returns no spec",
    "raised": "currently raises",
    "declined_target_unavailable": "not replayed: original lookup had a reload target",
    "finder_unavailable": "not replayed: the displaced finder is no longer retained",
    "unsupported_finder": "not replayed: no callable find_spec",
}


def _speculative_replay_lines(document: ReportDocument, context: _RenderContext) -> list[str]:
    """Format the bounded displaced-finder replay results.

    Every line states what the displaced finder returns *now*; none claim the
    original import would have succeeded. A returned spec proves only current
    finder behavior, not historical resolution or loader success.
    """
    replays = document.analysis.speculative_replays
    if not replays and not document.analysis.speculative_replays_omitted:
        return []
    lines = [context.heading(f"-- speculative replays ({len(replays)}) --")]
    lines.append(
        "Each line replays one importer-cache finder that a later cache change displaced, asking whether it"
        " returns a spec for a module that then failed on the same path. Results reflect current state, not the"
        " outcome during the run."
    )
    for replay in replays:
        finder = replay.displaced_finder.type_name
        where = context.quoted_path(replay.path)
        if replay.outcome == "returned_spec":
            loader = None if replay.spec_summary is None else replay.spec_summary.loader
            loader_clause = "" if loader is None else f" (loader {loader.type_name})"
            outcome = f"currently returns a spec{loader_clause}"
        elif replay.outcome == "raised":
            outcome = f"currently raises {replay.exception_type_name or 'an exception'}"
        else:
            outcome = _SPECULATIVE_REPLAY_OUTCOMES.get(replay.outcome, _humanize(replay.outcome))
        lines.append(
            f"displaced {finder} for {where}, asked about {replay.fullname!r}: {outcome} "
            f"(cache change #{replay.diff_seq}; failed lookup #{replay.attempt_seq})"
        )
    if document.analysis.speculative_replays_omitted:
        lines.append(
            f"    ... and {document.analysis.speculative_replays_omitted} more candidates omitted at the per-report cap"
        )
    return lines


def _importer_cache_diff_lines(diff: ImporterCacheDiff, context: _RenderContext) -> list[str]:
    """Format one bounded human projection of an exhaustive cache diff.

    Full entry lines are shown only for paths relevant to a finding's
    structural comparison or a resolution route's captured search path;
    everything else is summarized as one counted line, keeping JSON
    exhaustive.
    """
    lines = [f"#{diff.seq} {_observation_label(diff.observation)}{context.thread_suffix(diff.thread_name)}"]
    entries = _cache_diff_entries(diff, context)
    relevant = context.relevant_cache_paths
    if relevant:
        relevant_entries = [item for item in entries if os.path.normcase(item[1]) in relevant]
        other_entries = [item for item in entries if os.path.normcase(item[1]) not in relevant]
        shown = relevant_entries[:_MAX_CACHE_CHANGES_PER_DIFF]
        lines.extend(line for _kind, _path, line in shown)
        if len(relevant_entries) > _MAX_CACHE_CHANGES_PER_DIFF:
            omitted = len(relevant_entries) - _MAX_CACHE_CHANGES_PER_DIFF
            lines.append(f"    ... and {omitted} more changes; full list in the JSON report")
        if other_entries:
            lines.append(
                f"    other changes: {_cache_change_counts_clause(other_entries)}; full list in the JSON report"
            )
    else:
        shown = entries[:_UNFILTERED_CACHE_CHANGES_SHOWN]
        lines.extend(line for _kind, _path, line in shown)
        if len(entries) > _UNFILTERED_CACHE_CHANGES_SHOWN:
            omitted = len(entries) - _UNFILTERED_CACHE_CHANGES_SHOWN
            lines.append(f"    ... and {omitted} more changes; full list in the JSON report")
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
            f"{context.finder_label(name, finder_id)}: called {count} time{'' if count == 1 else 's'}, "
            f"found {len(claimed)} module{'' if len(claimed) == 1 else 's'}"
        )
        lines.extend(f"    {module}" for module in claimed[:_MAX_LISTED_MODULES])
        if len(claimed) > _MAX_LISTED_MODULES:
            lines.append(f"    ... and {len(claimed) - _MAX_LISTED_MODULES} more")
    return lines


def _finding_lines(finding: Finding, context: _RenderContext) -> list[str]:
    """Render one structured finding as a headline plus labeled evidence lines."""
    if finding.kind == "legacy_finder_contract" and isinstance(finding.evidence, ContractEvidence):
        contract = finding.evidence.finder_contract
        return [
            f"[legacy-finder-contract] {contract.finder_type_name}: find_module is callable but find_spec is not",
            "    CPython 3.12+ removed the legacy fallback; direct consumers may require find_spec earlier",
        ]
    if finding.kind == "path_hook_shadow" and isinstance(finding.evidence, DeepCallEvidence):
        call = finding.evidence.deep_call
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
    if finding.kind == "regular_module_shadows_namespace":
        return [
            f"[regular-module-shadows-namespace] '{finding.module}': a regular module displaced an earlier namespace candidate",
            "    a later descendant import failed because the selected module was not a package",
        ]
    if finding.kind == "repeated_load_failure":
        return [
            f"[repeated-load-failure] '{finding.module}': the same loader and origin were resolved after an earlier successful load",
            "    the later exact import failed; the captured sequence correlates the repeated load but does not include its exception message",
        ]
    if finding.kind in ("module_replacement", "repeated_loader_execution") and isinstance(
        finding.evidence, DeepCallEvidence
    ):
        call = finding.evidence.deep_call
        transition = _module_transition(
            finding.evidence.module_state_baseline or call.module_state_before, _deep_effective_module_state(call)
        )
        headline = (
            f"[repeated-loader-execution] '{finding.module}': the loader executed a different module object"
            if finding.kind == "repeated_loader_execution"
            else f"[module-replacement] '{finding.module}': the module object changed during {_bare_boundary_label(call.boundary)}"
        )
        return [
            headline,
            f"    captured boundary: {transition}; internal steps and temporary objects are unknown",
        ]
    if finding.kind == "no_spec":
        return [
            f"[no-spec] '{finding.module}' is in sys.modules with no __spec__, and no finder call for it was "
            "recorded (likely created manually or loaded without the normal import machinery)."
        ]
    claim = finding_claim(finding)
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
        route_comparison_id = finding_route_comparison_id(finding)
        comparison = None if route_comparison_id is None else context.comparisons_by_id.get(route_comparison_id)
        if comparison is None:
            standard_only = "unavailable"
        else:
            standard_only = ", ".join(context.quoted_path(path) for path in comparison.only_in_right_route)
        return [
            f"[namespace-truncation] '{finding.module}': the namespace returned by {finder} omits locations "
            "that the standard path search finds, and a submodule import failed",
            f"    locations found only by the standard path search: {standard_only}",
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
    return comparison.has_differences()


def _route_comparison_lines(
    comparison: RouteComparison, context: _RenderContext, mutations: "list[MetaPathMutation] | None" = None
) -> list[str]:
    """Render a neutral route comparison outside the findings section."""
    left = context.routes_by_id.get(comparison.left_route_id)
    right = context.routes_by_id.get(comparison.right_route_id)
    if left is None or right is None:
        return [f"{comparison.comparison_id}: referenced route unavailable"]
    lines = [
        f"'{left.module}':",
        f"    during the run: {_route_summary(left, context)}",
        f"    standard search at report time: {_route_summary(right, context, same_origin_as=left)}",
        f"    {_route_difference_line(comparison)}",
    ]
    installer = _finder_installation_line(left.finder_type_name, mutations or [], context)
    if installer is not None:
        lines.append(f"    {installer}")
    lines.extend(f"    note: {_signal_description(signal)}" for signal in left.signals)
    lines.append(f"    {_structural_evidence_line(comparison.structural_comparison)}")
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
        differences.append(f"{count} search location{'' if count == 1 else 's'} only in the recorded result")
    if comparison.only_in_right_route:
        count = len(comparison.only_in_right_route)
        differences.append(f"{count} search location{'' if count == 1 else 's'} only in the standard search")
    if comparison.locations_reordered:
        differences.append("search locations reordered")
    detail = "; ".join(differences) if differences else "none"
    qualifier = "" if comparison.complete else " (partial comparison)"
    return f"differences{qualifier}: {detail}"


def _structural_comparison_line(finding: Finding) -> str:
    """Label identity-only evidence separately from a report-time probe."""
    comparison = finding_structural_comparison(finding)
    if comparison is None:
        return "since install: comparison unavailable"
    return _structural_evidence_line(comparison)


def _structural_evidence_line(comparison: StructuralComparison) -> str:
    """Render one structural comparison without coupling it to a finding."""
    path_hooks_changed = comparison.path_hooks_changed
    importer_cache_changed = comparison.importer_cache_changed
    importer_cache_changed_paths = comparison.importer_cache_changed_paths
    if path_hooks_changed is None:
        path_hooks = "sys.path_hooks comparison unavailable"
    elif path_hooks_changed:
        path_hooks = "sys.path_hooks changed"
    else:
        path_hooks = "sys.path_hooks unchanged"
    if importer_cache_changed is None:
        importer_cache = "sys.path_importer_cache comparison unavailable"
    elif importer_cache_changed:
        count = len(importer_cache_changed_paths)
        noun = "path" if count == 1 else "paths"
        importer_cache = f"sys.path_importer_cache changed for {count} searched {noun}"
    else:
        importer_cache = "sys.path_importer_cache unchanged for the searched path"
    return f"since install: {path_hooks}; {importer_cache}"


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
