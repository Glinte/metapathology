"""Rendering of the diagnostic report.

All stringification of foreign data (specs, loaders, stack frames) happens
here, at report time, never while an import is in flight. The report is
typically written from an atexit callback, so nothing here may raise.
"""

import os
import sys
from importlib.machinery import PathFinder
from typing import TYPE_CHECKING, TextIO

from metapathology._records import (
    FindSpecCall,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    type_name,
)

if TYPE_CHECKING:
    from importlib.machinery import ModuleSpec
    from traceback import StackSummary

    from metapathology._monitor import Monitor

# This package's own directory, normcased for comparison: used to drop our
# frames from displayed stacks.
_PACKAGE_DIR = os.path.normcase(os.path.dirname(os.path.abspath(__file__)))
# Only claims with these origin suffixes get the bypass check; extension
# modules, builtins, and synthetic origins have no PathFinder baseline.
_SOURCE_SUFFIXES = (".py", ".pyc")
# Max non-noise frames shown per stack in the report.
_STACK_DISPLAY_FRAMES = 5
# Max claimed modules listed per finder in the attribution section.
_MAX_LISTED_MODULES = 25


def write_report(monitor: "Monitor", file: TextIO | None = None) -> None:
    """Render the report for ``monitor`` and write it to ``file`` (default ``sys.stderr``)."""
    out = sys.stderr if file is None else file
    out.write(render_report(monitor))


def render_report(monitor: "Monitor") -> str:
    """Render the full diagnostic report as text.

    Never raises; on internal failure the returned text says so instead.
    """
    try:
        return "\n".join(_render_lines(monitor)) + "\n"
    except Exception as exc:  # The report must never break the host program.
        return f"== metapathology report ==\nreport generation failed: {type(exc).__name__}: {exc}\n"


def _render_lines(monitor: "Monitor") -> list[str]:
    """Build the report body as a list of lines; the caller joins and appends the trailing newline."""
    events = monitor.events()
    mutations = [e for e in events if isinstance(e, MetaPathMutation)]
    reassignments = [e for e in events if isinstance(e, MetaPathReassignment)]
    calls = [e for e in events if isinstance(e, FindSpecCall)]
    errors = [e for e in events if isinstance(e, InternalError)]

    lines = ["== metapathology report =="]
    lines.append(f"monitor enabled: {monitor.enabled}")
    lines.append(f"initial sys.meta_path: {_names_line(monitor.initial_meta_path)}")
    lines.append(f"current sys.meta_path: {_names_line(_current_meta_path_names())}")
    skipped = monitor.skipped_finders()
    if skipped:
        lines.append("finders observed but not instrumented (attribution by elimination):")
        lines.extend(f"    {name}: {reason}" for name, reason in skipped)
    new_modules = _modules_since_install(monitor)
    lines.append(f"modules imported since install: {len(new_modules)}")

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
    lines.append("-- finder attribution (instrumented finders only) --")
    lines.extend(_attribution_lines(calls))

    findings = _suspicious_findings(monitor, calls)
    lines.append("")
    lines.append(f"-- suspicious findings ({len(findings)}) --")
    if not findings:
        lines.append("(none)")
    lines.extend(findings)

    lines.append("")
    lines.append(f"-- internal errors ({len(errors)}) --")
    if not errors:
        lines.append("(none)")
    lines.extend(f"#{e.seq} in {e.where}: {e.exception_type_name}: {e.message}" for e in errors)

    lines.append("")
    return lines


def _current_meta_path_names() -> tuple[str, ...]:
    """Name the current ``sys.meta_path`` entries, tolerating a broken interpreter state at shutdown."""
    try:
        return tuple(type_name(f) for f in list(sys.meta_path))
    except Exception:
        return ("<unavailable>",)


def _modules_since_install(monitor: "Monitor") -> list[str]:
    """List names added to ``sys.modules`` after the monitor was installed."""
    baseline = monitor.baseline_modules
    try:
        return [name for name in list(sys.modules) if name not in baseline]
    except Exception:
        return []


def _names_line(names: tuple[str, ...]) -> str:
    """Format finder names as a bracketed, comma-separated list."""
    return "[" + ", ".join(names) + "]"


def _mutation_lines(mutation: MetaPathMutation) -> list[str]:
    """Format one mutation record: op, added/removed delta, resulting contents, and user-code stack."""
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
    """Format one reassignment record with before/after contents and the detection stack."""
    lines = [
        f"#{reassignment.seq} sys.meta_path REASSIGNED, detected during import of "
        f"'{reassignment.during_import}' [thread {reassignment.thread_name}]"
    ]
    lines.append(f"    before: {_names_line(reassignment.old_contents)}")
    lines.append(f"    after:  {_names_line(reassignment.new_contents)}")
    lines.append("    instrumentation reinstalled; stack shows the triggering import, not the reassignment itself:")
    lines.extend(_stack_lines(reassignment.stack))
    return lines


def _attribution_lines(calls: list[FindSpecCall]) -> list[str]:
    """Summarize ``find_spec`` traffic per finder: probe counts and claimed modules, capped per finder."""
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


def _suspicious_findings(monitor: "Monitor", calls: list[FindSpecCall]) -> list[str]:
    """Cross-reference ``sys.modules`` against recorded claims and return finding lines.

    Modules claimed by an instrumented finder get the bypass check; modules
    with no recorded claim and no ``__spec__`` are flagged as manual loads.
    Modules that predate the monitor are ignored.
    """
    winners: dict[str, FindSpecCall] = {}
    for call in calls:
        if call.found:
            winners[call.fullname] = call
    findings: list[str] = []
    baseline = monitor.baseline_modules
    for name, module in list(sys.modules.items()):
        if name in baseline or name == "__main__":
            continue
        winner = winners.get(name)
        if winner is not None:
            findings.extend(_bypass_findings(name, winner))
            continue
        try:
            spec = getattr(module, "__spec__", None)
        except Exception:  # sys.modules values can be arbitrarily weird objects.
            spec = None
        if spec is None:
            findings.append(
                f"[no-spec] '{name}' is in sys.modules with no __spec__ and no recorded finder claim "
                "(manually created or exec_module-style load; invisible to all import hooks)."
            )
    return findings


def _bypass_findings(name: str, winner: FindSpecCall) -> list[str]:
    """Check one claimed source module against a fresh ``PathFinder`` replay.

    Args:
        name: The claimed module's fullname.
        winner: The recorded ``find_spec`` call that claimed it.

    Returns:
        At most one finding line: ``[bypass]`` when the standard machinery
        would produce a different loader or origin, ``[unfindable]`` when it
        finds nothing at all, empty when the claim looks compliant or has no
        filesystem source origin.
    """
    origin = winner.origin
    if origin is None or not origin.endswith(_SOURCE_SUFFIXES) or winner.loader_type_name is None:
        return []
    replay = _replay_path_finder(name)
    if replay is None:
        return [
            f"[unfindable] '{name}' (origin {origin}) was claimed by {winner.finder_type_name}, but the "
            "standard sys.path machinery cannot find it: sys.path_hooks-based tools never see this module."
        ]
    replay_loader = None if replay.loader is None else type_name(replay.loader)
    replay_origin = replay.origin if isinstance(replay.origin, str) else None
    if replay_loader != winner.loader_type_name or not _same_path(replay_origin, origin):
        return [
            f"[bypass] '{name}' was claimed by {winner.finder_type_name} "
            f"(loader {winner.loader_type_name}, origin {origin}); the standard sys.path machinery would use "
            f"loader {replay_loader} (origin {replay_origin}). sys.path_hooks-based tools were bypassed."
        ]
    return []


def _replay_path_finder(name: str) -> "ModuleSpec | None":
    """Ask the standard ``PathFinder`` what it would do for ``name``, without importing anything.

    Returns:
        The spec the standard path machinery would produce now, or None when
        it finds nothing or the replay is impossible (e.g. the parent package
        is gone or has no ``__path__``).
    """
    parent_name, _, _ = name.rpartition(".")
    path = None
    if parent_name:
        parent = sys.modules.get(parent_name)
        path = getattr(parent, "__path__", None)
        if path is None:
            return None
    try:
        return PathFinder.find_spec(name, path)
    except Exception:  # A broken finder chain must not break the report.
        return None


def _same_path(a: str | None, b: str | None) -> bool:
    """Compare two filesystem paths after absolutization and case normalization."""
    if a is None or b is None:
        return a == b
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _stack_lines(stack: "StackSummary") -> list[str]:
    """Format the interesting frames of a captured stack, innermost first, noise filtered out."""
    frames = [f for f in stack if not _is_noise_frame(f.filename)]
    shown = frames[:_STACK_DISPLAY_FRAMES]  # walk_stack order: innermost first.
    if not shown:
        return ["    (no frames outside the import machinery)"]
    return [f"    at {f.filename}:{f.lineno} in {f.name}" for f in shown]


def _is_noise_frame(filename: str) -> bool:
    """Return True for frames from the import machinery or from metapathology itself."""
    if filename.startswith("<frozen importlib"):
        return True
    return os.path.normcase(os.path.abspath(filename)).startswith(_PACKAGE_DIR)
