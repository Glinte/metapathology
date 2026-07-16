"""Human-readable projection of a cutoff-based report document."""

import os

from metapathology._records import (
    DeepDiagnosticCall,
    FindSpecCall,
    ImportAuditStart,
    ImporterCacheDiff,
    ImporterCacheEntry,
    ImportObjectRef,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
    PathHooksMutation,
    PathHooksReassignment,
)
from metapathology._report_data import Finding, ReportDocument

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Iterable
    from traceback import StackSummary
    from typing import TypeVar

    _EventT = TypeVar("_EventT")


# This package's own directory, normcased for comparison: used to drop our
# frames from displayed stacks.
_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
# Max non-noise frames shown per stack in the report.
_STACK_DISPLAY_FRAMES = 5
# Max claimed modules listed per finder in the attribution section.
_MAX_LISTED_MODULES = 25
_MAX_CACHE_CHANGES_PER_DIFF = 25


def render_lines(document: ReportDocument) -> list[str]:
    """Build the report body as a list of lines; the caller adds the trailing newline."""
    mutations = _events_of_type(document.events, MetaPathMutation)
    reassignments = _events_of_type(document.events, MetaPathReassignment)
    path_hook_mutations = _events_of_type(document.events, PathHooksMutation)
    path_hook_reassignments = _events_of_type(document.events, PathHooksReassignment)
    importer_cache_diffs = _events_of_type(document.events, ImporterCacheDiff)
    calls = _events_of_type(document.events, FindSpecCall)
    errors = _events_of_type(document.events, InternalError)

    lines = ["== metapathology report =="]
    lines.append("report guide: https://glinte.github.io/metapathology/report/")
    lines.append(f"monitor enabled: {document.monitor_enabled}")
    if document.deep_diagnostics:
        lines.append("WARNING: opt-in deep diagnostics replace foreign callables and may perturb identity checks")
        lines.append(f"deep mechanisms enabled: {', '.join(document.deep_diagnostics)}")
    else:
        lines.append("deep diagnostics: disabled")
    bootstrap = document.early_site_bootstrap
    if bootstrap is None:
        lines.append("early site bootstrap: inactive")
    else:
        lines.append(f"early site bootstrap: {bootstrap.path}")
        lines.append(f"bootstrap site-packages: {bootstrap.site_packages}")
        lines.append(f"bootstrap activation: {bootstrap.activation_source}")
        earlier = _names_line(bootstrap.earlier_pth_files) if bootstrap.earlier_pth_files else "(none)"
        lines.append(f"earlier .pth files outside capture: {earlier}")
    lines.append(f"initial sys.meta_path: {_names_line(document.initial_meta_path)}")
    current_meta_path = ("<unavailable>",) if document.current_meta_path is None else document.current_meta_path
    lines.append(f"current sys.meta_path: {_names_line(current_meta_path)}")
    lines.append(f"sys.path_hooks monitoring enabled: {document.path_hooks_enabled}")
    lines.append(f"initial sys.path_hooks: {_refs_line(document.initial_path_hooks)}")
    current_path_hooks = () if document.current_path_hooks is None else document.current_path_hooks
    lines.append(f"current sys.path_hooks: {_refs_line(current_path_hooks)}")
    lines.append(f"sys.path_importer_cache monitoring enabled: {document.importer_cache_enabled}")
    lines.append(
        "initial sys.path_importer_cache: "
        f"{len(document.initial_importer_cache)} string keys, "
        f"{document.initial_importer_cache_non_string_keys} non-string keys omitted"
    )
    current_cache_count = 0 if document.current_importer_cache is None else len(document.current_importer_cache)
    lines.append(
        "current sys.path_importer_cache: "
        f"{current_cache_count} string keys, "
        f"{document.current_importer_cache_non_string_keys or 0} non-string keys omitted"
    )
    standard_skipped = [item for item in document.skipped_finders if item.expected]
    other_skipped = [item for item in document.skipped_finders if not item.expected]
    if standard_skipped:
        lines.append(
            "standard CPython finders left unwrapped (expected): "
            + _names_line(tuple(item.finder_type_name for item in standard_skipped))
        )
        lines.append("    BuiltinImporter handles built-in modules; FrozenImporter handles frozen modules.")
        lines.append(
            "    PathFinder handles sys.path and package paths; suspicious custom claims are compared with it later."
        )
        lines.append("    These entries are classes shared by the interpreter, so metapathology does not modify them.")
    if other_skipped:
        lines.append("other finders observed but not instrumented (direct attribution unavailable):")
        lines.extend(f"    {item.finder_type_name}: {item.reason}" for item in other_skipped)
    module_count = 0 if document.modules_since_install is None else len(document.modules_since_install)
    lines.append(f"modules imported since install: {module_count}")

    lines.append("")
    lines.append(f"-- chronological evidence timeline ({len(document.events)}) --")
    lines.append("Sequence numbers are capture order; concurrent events are not a global wall-clock order.")
    if not document.events:
        lines.append("(none)")
    lines.extend(_timeline_line(event) for event in document.events)

    lines.append("")
    lines.append(f"-- sys.meta_path mutations ({len(mutations)}) --")
    if not mutations:
        lines.append("(none)")
    for mutation in mutations:
        lines.extend(_mutation_lines(mutation))

    lines.append("")
    lines.append(f"-- sys.meta_path reassignments ({len(reassignments)}) --")
    if not reassignments:
        lines.append("(none)")
    for reassignment in reassignments:
        lines.extend(_reassignment_lines(reassignment))

    lines.append("")
    lines.append(f"-- sys.path_hooks mutations ({len(path_hook_mutations)}) --")
    if not path_hook_mutations:
        lines.append("(none)")
    for mutation in path_hook_mutations:
        lines.extend(_path_hooks_mutation_lines(mutation))

    lines.append("")
    lines.append(f"-- sys.path_hooks reassignments ({len(path_hook_reassignments)}) --")
    if not path_hook_reassignments:
        lines.append("(none)")
    for reassignment in path_hook_reassignments:
        lines.extend(_path_hooks_reassignment_lines(reassignment))

    lines.append("")
    lines.append(f"-- sys.path_importer_cache changes ({len(importer_cache_diffs)}) --")
    if not importer_cache_diffs:
        lines.append("(none)")
    for diff in importer_cache_diffs:
        lines.extend(_importer_cache_diff_lines(diff))

    lines.append("")
    lines.append("-- finder attribution (instrumented finders only) --")
    lines.extend(_attribution_lines(calls))

    lines.append("")
    lines.append(f"-- suspicious findings ({len(document.findings)}) --")
    if not document.findings:
        lines.append("(none)")
    lines.extend(_finding_line(finding) for finding in document.findings)

    error_count = len(errors) + len(document.report_errors)
    lines.append("")
    lines.append(f"-- internal errors ({error_count}) --")
    if not error_count:
        lines.append("(none)")
    lines.extend(_internal_error_line(error) for error in errors)
    lines.extend(f"during report in {error.where}: {error.exception_type_name}" for error in document.report_errors)
    lines.append("")
    return lines


def _events_of_type(events: "Iterable[object]", event_type: "type[_EventT]") -> "list[_EventT]":
    """Return only events of ``event_type``, preserving that concrete type for callers."""
    return [event for event in events if isinstance(event, event_type)]


def _internal_error_line(error: InternalError) -> str:
    """Format an internal error without requiring captured foreign exception text."""
    line = f"#{error.seq} in {error.where}: {error.exception_type_name}"
    return line if error.message is None else f"{line}: {error.message}"


def _timeline_line(event: MonitorEvent) -> str:
    """Render one compact line using only data captured in the event record."""
    if isinstance(event, DeepDiagnosticCall):
        subject = event.fullname if event.fullname is not None else event.path
        return f"#{event.seq} deep {event.boundary} {subject or '<unknown>'}: {event.outcome}"
    if isinstance(event, ImportAuditStart):
        path_hooks = "disabled" if event.path_hooks_id is None else f"0x{event.path_hooks_id:x}"
        if event.importer_cache_id is None:
            importer_cache = "disabled"
        else:
            importer_cache = f"0x{event.importer_cache_id:x} size {event.importer_cache_size}"
        return (
            f"#{event.seq} import audit: resolution started for {event.fullname!r}; outcome unknown "
            f"[thread {event.thread_name}; meta_path 0x{event.meta_path_id:x} "
            f"{_names_line(event.meta_path_type_names)}; path_hooks {path_hooks}; "
            f"importer_cache {importer_cache}]"
        )
    if isinstance(event, FindSpecCall):
        if event.exception_type_name is not None:
            outcome = f"raised {event.exception_type_name}"
        elif event.found:
            outcome = f"claimed with loader {event.loader_type_name}, origin {event.origin!r}"
        else:
            outcome = "passed"
        return (
            f"#{event.seq} finder {event.finder_type_name} id 0x{event.finder_id:x} probed "
            f"{event.fullname!r}: {outcome} [thread {event.thread_name}]"
        )
    if isinstance(event, ImporterCacheDiff):
        return (
            f"#{event.seq} importer cache diff at {event.observation}: +{len(event.added)} "
            f"-{len(event.removed)} ~{len(event.replaced)} [thread {event.thread_name}]"
        )
    if isinstance(event, MetaPathMutation):
        return f"#{event.seq} sys.meta_path {event.op} {_timeline_delta(event.added, event.removed)} [thread {event.thread_name}]"
    if isinstance(event, MetaPathReassignment):
        return (
            f"#{event.seq} sys.meta_path reassignment detected during {event.during_import!r} "
            f"[thread {event.thread_name}]"
        )
    if isinstance(event, PathHooksMutation):
        return (
            f"#{event.seq} sys.path_hooks {event.op}: +{len(event.added)} -{len(event.removed)} "
            f"[thread {event.thread_name}]"
        )
    if isinstance(event, PathHooksReassignment):
        return (
            f"#{event.seq} sys.path_hooks reassignment detected during {event.during_import!r} "
            f"[thread {event.thread_name}]"
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


def _ref_name(reference: ImportObjectRef) -> str:
    """Format captured identity metadata without inspecting the original object."""
    label = reference.type_name if reference.name is None else f"{reference.name} ({reference.type_name})"
    return f"{label} id 0x{reference.object_id:x}"


def _refs_line(references: tuple[ImportObjectRef, ...]) -> str:
    """Format a path-hook snapshot."""
    return "[" + ", ".join(_ref_name(reference) for reference in references) + "]"


def _mutation_lines(mutation: MetaPathMutation) -> list[str]:
    """Format one mutation record: op, delta, resulting contents, and user stack."""
    delta_parts: list[str] = []
    if mutation.added:
        delta_parts.append("+" + _names_line(mutation.added))
    if mutation.removed:
        delta_parts.append("-" + _names_line(mutation.removed))
    delta = " ".join(delta_parts) if delta_parts else "(order change)"
    lines = [f"#{mutation.seq} {mutation.op} {delta} [thread {mutation.thread_name}]"]
    lines.append(f"    meta_path after: {_names_line(mutation.contents_after)}")
    lines.extend(_stack_lines(mutation.stack))
    return lines


def _reassignment_lines(reassignment: MetaPathReassignment) -> list[str]:
    """Format one reassignment record with before/after contents and detection stack."""
    lines = [
        f"#{reassignment.seq} sys.meta_path REASSIGNED, detected during import of "
        f"'{reassignment.during_import}' [thread {reassignment.thread_name}]"
    ]
    lines.append(f"    before: {_names_line(reassignment.old_contents)}")
    lines.append(f"    after:  {_names_line(reassignment.new_contents)}")
    lines.append("    instrumentation reinstalled; stack shows the triggering import, not the reassignment itself:")
    lines.extend(_stack_lines(reassignment.stack))
    return lines


def _path_hooks_mutation_lines(mutation: PathHooksMutation) -> list[str]:
    """Format one path-hook mutation from captured plain references."""
    delta_parts: list[str] = []
    if mutation.added:
        delta_parts.append("+" + _refs_line(mutation.added))
    if mutation.removed:
        delta_parts.append("-" + _refs_line(mutation.removed))
    delta = " ".join(delta_parts) if delta_parts else "(order change)"
    lines = [f"#{mutation.seq} {mutation.op} {delta} [thread {mutation.thread_name}]"]
    lines.append(f"    path_hooks after: {_refs_line(mutation.contents_after)}")
    lines.extend(_stack_lines(mutation.stack))
    return lines


def _path_hooks_reassignment_lines(reassignment: PathHooksReassignment) -> list[str]:
    """Format one path-hooks reassignment detected at an import boundary."""
    lines = [
        f"#{reassignment.seq} sys.path_hooks REASSIGNED, detected during import of "
        f"'{reassignment.during_import}' [thread {reassignment.thread_name}]"
    ]
    lines.append(f"    before: {_refs_line(reassignment.old_contents)}")
    lines.append(f"    after:  {_refs_line(reassignment.new_contents)}")
    lines.append("    instrumentation reinstalled; stack shows the triggering import, not the reassignment itself:")
    lines.extend(_stack_lines(reassignment.stack))
    return lines


def _cache_entry_line(entry: ImporterCacheEntry) -> str:
    """Format one captured cache value without inspecting the live finder."""
    finder = _cache_finder_name(entry.finder)
    return f"{entry.path!r} -> {finder}"


def _cache_finder_name(finder: ImportObjectRef | None) -> str:
    """Format a captured cache finder or its negative marker."""
    return "negative (None)" if finder is None else _ref_name(finder)


def _importer_cache_diff_lines(diff: ImporterCacheDiff) -> list[str]:
    """Format one bounded human projection of an exhaustive cache diff."""
    lines = [f"#{diff.seq} {diff.observation} [thread {diff.thread_name}]"]
    changes: list[str] = []
    changes.extend(f"    + {_cache_entry_line(entry)}" for entry in diff.added)
    changes.extend(f"    - {_cache_entry_line(entry)}" for entry in diff.removed)
    changes.extend(
        f"    ~ {replacement.path!r}: "
        f"{_cache_finder_name(replacement.before)} -> {_cache_finder_name(replacement.after)}"
        for replacement in diff.replaced
    )
    lines.extend(changes[:_MAX_CACHE_CHANGES_PER_DIFF])
    if len(changes) > _MAX_CACHE_CHANGES_PER_DIFF:
        lines.append(f"    ... and {len(changes) - _MAX_CACHE_CHANGES_PER_DIFF} more changes")
    if diff.non_string_keys_before != diff.non_string_keys_after:
        lines.append(f"    non-string keys omitted: {diff.non_string_keys_before} -> {diff.non_string_keys_after}")
    return lines


def _attribution_lines(calls: list[FindSpecCall]) -> list[str]:
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
        lines.append(f"{name} (id 0x{finder_id:x}): {count} find_spec calls, {len(claimed)} claimed")
        lines.extend(f"    {module}" for module in claimed[:_MAX_LISTED_MODULES])
        if len(claimed) > _MAX_LISTED_MODULES:
            lines.append(f"    ... and {len(claimed) - _MAX_LISTED_MODULES} more")
    return lines


def _finding_line(finding: Finding) -> str:
    """Render one structured finding using the established human vocabulary."""
    if finding.kind == "no_spec":
        return (
            f"[no-spec] '{finding.module}' is in sys.modules with no __spec__ and no recorded finder claim "
            "(manually created or exec_module-style load; invisible to all import hooks)."
        )
    claim = finding.claim
    replay = finding.replay
    if claim is None or replay is None:
        return f"[{finding.kind}] '{finding.module}'"
    if finding.kind == "unfindable":
        return (
            f"[unfindable] '{finding.module}' (origin {claim.origin}) was claimed by {claim.finder_type_name}, but "
            "the current live PathFinder replay cannot find it: sys.path_hooks-based tools never see this module."
            f"\n    {_structural_comparison_line(finding)}"
        )
    if finding.kind == "namespace_truncation":
        comparison = finding.spec_comparison
        omitted = "an unavailable location"
        if comparison is not None:
            omitted = ", ".join(repr(path) for path in comparison.omitted_locations)
        return (
            f"[namespace-truncation] '{finding.module}' was claimed by {claim.finder_type_name}; the captured "
            f"namespace search locations omit {omitted}, which the current live PathFinder replay includes."
            f"\n    {_spec_comparison_line(finding)}"
            f"\n    {_structural_comparison_line(finding)}"
        )
    if finding.kind == "package_displacement":
        label = "package-displacement"
    elif finding.kind == "origin_displacement":
        label = "origin-displacement"
    elif finding.kind == "spec_difference":
        label = "spec-difference"
    else:
        label = "bypass"
    return (
        f"[{label}] '{finding.module}' was claimed by {claim.finder_type_name} "
        f"(loader {claim.loader_type_name}, origin {claim.origin}); the current live PathFinder replay would use "
        f"loader {replay.loader_type_name} (origin {replay.origin}). sys.path_hooks-based tools were bypassed."
        f"\n    {_spec_comparison_line(finding)}"
        f"\n    {_structural_comparison_line(finding)}"
    )


def _spec_comparison_line(finding: Finding) -> str:
    comparison = finding.spec_comparison
    if comparison is None:
        return "spec comparison unavailable."
    differences: list[str] = []
    if comparison.loader_type_changed:
        differences.append("loader type differs")
    if comparison.origin_changed:
        differences.append("origin differs")
    if comparison.cached_changed:
        differences.append("cached path differs")
    if comparison.package_status_changed:
        differences.append("package/module status differs")
    if comparison.omitted_locations:
        differences.append(f"{len(comparison.omitted_locations)} standard search location(s) omitted")
    if comparison.additional_locations:
        differences.append(f"{len(comparison.additional_locations)} additional claimed search location(s)")
    if comparison.locations_reordered:
        differences.append("search locations reordered")
    detail = "; ".join(differences) if differences else "no comparable semantic differences"
    completeness = "complete" if comparison.complete else "partial"
    observed_phase = (
        "post-hoc claim locations" if comparison.observed_locations_state == "post_hoc" else "import-time claim capture"
    )
    return f"spec comparison ({completeness}, {observed_phase} vs current live replay): {detail}."


def _structural_comparison_line(finding: Finding) -> str:
    """Label identity-only evidence separately from the report-time replay."""
    comparison = finding.structural_comparison
    if comparison is None:
        return "historical structural evidence unavailable."
    if comparison.path_hooks_changed is None:
        path_hooks = "sys.path_hooks comparison unavailable"
    elif comparison.path_hooks_changed:
        path_hooks = "sys.path_hooks changed between install and report"
    else:
        path_hooks = "sys.path_hooks unchanged between install and report"
    if comparison.importer_cache_changed is None:
        importer_cache = "sys.path_importer_cache comparison unavailable"
    elif comparison.importer_cache_changed:
        count = len(comparison.importer_cache_changed_paths)
        noun = "path" if count == 1 else "paths"
        importer_cache = f"sys.path_importer_cache changed for {count} captured search {noun}"
    else:
        importer_cache = "sys.path_importer_cache unchanged for the captured search path"
    return f"historical structural evidence: {path_hooks}; {importer_cache}."


def _stack_lines(stack: "StackSummary") -> list[str]:
    """Format interesting captured frames, innermost first, with noise removed."""
    frames = [frame for frame in stack if not _is_noise_frame(frame.filename)]
    shown = frames[:_STACK_DISPLAY_FRAMES]  # walk_stack order: innermost first.
    if not shown:
        return ["    (no frames outside the import machinery)"]
    return [f"    at {frame.filename}:{frame.lineno} in {frame.name}" for frame in shown]


def _is_noise_frame(filename: str) -> bool:
    """Return True for frames from import machinery or metapathology itself."""
    if filename.startswith("<frozen importlib"):
        return True
    return os.path.normcase(os.path.abspath(filename)).startswith(_PACKAGE_DIR)
