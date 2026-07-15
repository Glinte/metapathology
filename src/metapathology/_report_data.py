"""Cutoff-based report evidence and its machine-readable projection.

The monitor records plain event data in import hot paths. This module copies
that data and the relevant live interpreter state exactly once, then performs
report-time analysis. Renderers consume the resulting document and never
inspect the interpreter independently.
"""

import os
import sys
import time
from importlib.machinery import PathFinder

from metapathology import __version__
from metapathology._records import (
    FindSpecCall,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
    _ReadOnlyField,
    _Record,
    type_name,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from traceback import FrameSummary

    from metapathology._monitor import Monitor


# Only claims with these origin suffixes get the bypass check; extension
# modules, builtins, and synthetic origins have no PathFinder baseline.
_SOURCE_SUFFIXES = (".py", ".pyc")
_STANDARD_CLASS_FINDER_REASON_PREFIX = "standard CPython class finder;"
# The JSON contract remains experimental until the observation mechanisms in
# roadmap T1--T7 have supplied their real event and evidence shapes.
_SCHEMA_NAME = "metapathology.report"
_SCHEMA_MAJOR = 0
_SCHEMA_MINOR = 1
# TODO(schema 1.0): Define and export a TypedDict for this document before
# exposing a public mapping-returning API; the 0.x shape is intentionally fluid.


class ReportError(_Record):
    """One failure while copying or analysing report-time state."""

    __slots__ = ("_exception_type_name", "_where")
    _fields = ("where", "exception_type_name")
    where = _ReadOnlyField[str]("_where")
    exception_type_name = _ReadOnlyField[str]("_exception_type_name")

    def __init__(self, where: str, exception_type_name: str) -> None:
        self._where = where
        self._exception_type_name = exception_type_name


class SkippedFinder(_Record):
    """Report-safe description of an uninstrumented finder."""

    __slots__ = ("_expected", "_finder_type_name", "_reason")
    _fields = ("finder_type_name", "reason", "expected")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    reason = _ReadOnlyField[str]("_reason")
    expected = _ReadOnlyField[bool]("_expected")

    def __init__(self, finder_type_name: str, reason: str, expected: bool) -> None:
        self._finder_type_name = finder_type_name
        self._reason = reason
        self._expected = expected


class ReplayResult(_Record):
    """PathFinder outcome that does not conflate failure with absence."""

    __slots__ = ("_exception_type_name", "_loader_type_name", "_origin", "_status")
    _fields = ("status", "loader_type_name", "origin", "exception_type_name")
    status = _ReadOnlyField[str]("_status")
    loader_type_name = _ReadOnlyField[str | None]("_loader_type_name")
    origin = _ReadOnlyField[str | None]("_origin")
    exception_type_name = _ReadOnlyField[str | None]("_exception_type_name")

    def __init__(
        self,
        status: str,
        loader_type_name: str | None = None,
        origin: str | None = None,
        exception_type_name: str | None = None,
    ) -> None:
        self._status = status
        self._loader_type_name = loader_type_name
        self._origin = origin
        self._exception_type_name = exception_type_name


class Finding(_Record):
    """Structured evidence for one human or machine-readable finding."""

    __slots__ = ("_claim", "_finding_id", "_kind", "_module", "_replay")
    _fields = ("finding_id", "kind", "module", "claim", "replay")
    finding_id = _ReadOnlyField[str]("_finding_id")
    kind = _ReadOnlyField[str]("_kind")
    module = _ReadOnlyField[str]("_module")
    claim = _ReadOnlyField[FindSpecCall | None]("_claim")
    replay = _ReadOnlyField[ReplayResult | None]("_replay")

    def __init__(
        self,
        finding_id: str,
        kind: str,
        module: str,
        claim: FindSpecCall | None = None,
        replay: ReplayResult | None = None,
    ) -> None:
        self._finding_id = finding_id
        self._kind = kind
        self._module = module
        self._claim = claim
        self._replay = replay


class ReportDocument(_Record):
    """One sequence-cutoff snapshot shared by all report renderers."""

    __slots__ = (
        "_argv",
        "_baseline_module_count",
        "_current_meta_path",
        "_cutoff_seq",
        "_cwd",
        "_events",
        "_findings",
        "_generated_at",
        "_initial_meta_path",
        "_modules_since_install",
        "_monitor_enabled",
        "_report_errors",
        "_skipped_finders",
    )
    _fields = (
        "generated_at",
        "cutoff_seq",
        "monitor_enabled",
        "baseline_module_count",
        "initial_meta_path",
        "current_meta_path",
        "modules_since_install",
        "events",
        "skipped_finders",
        "findings",
        "report_errors",
        "cwd",
        "argv",
    )
    generated_at = _ReadOnlyField[str]("_generated_at")
    cutoff_seq = _ReadOnlyField[int]("_cutoff_seq")
    monitor_enabled = _ReadOnlyField[bool]("_monitor_enabled")
    baseline_module_count = _ReadOnlyField[int]("_baseline_module_count")
    initial_meta_path = _ReadOnlyField[tuple[str, ...]]("_initial_meta_path")
    current_meta_path = _ReadOnlyField[tuple[str, ...] | None]("_current_meta_path")
    modules_since_install = _ReadOnlyField[tuple[str, ...] | None]("_modules_since_install")
    events = _ReadOnlyField[tuple[MonitorEvent, ...]]("_events")
    skipped_finders = _ReadOnlyField[tuple[SkippedFinder, ...]]("_skipped_finders")
    findings = _ReadOnlyField[tuple[Finding, ...]]("_findings")
    report_errors = _ReadOnlyField[tuple[ReportError, ...]]("_report_errors")
    cwd = _ReadOnlyField[str | None]("_cwd")
    argv = _ReadOnlyField[tuple[str, ...]]("_argv")

    def __init__(
        self,
        *,
        generated_at: str,
        cutoff_seq: int,
        monitor_enabled: bool,
        baseline_module_count: int,
        initial_meta_path: tuple[str, ...],
        current_meta_path: tuple[str, ...] | None,
        modules_since_install: tuple[str, ...] | None,
        events: tuple[MonitorEvent, ...],
        skipped_finders: tuple[SkippedFinder, ...],
        findings: tuple[Finding, ...],
        report_errors: tuple[ReportError, ...],
        cwd: str | None,
        argv: tuple[str, ...],
    ) -> None:
        self._generated_at = generated_at
        self._cutoff_seq = cutoff_seq
        self._monitor_enabled = monitor_enabled
        self._baseline_module_count = baseline_module_count
        self._initial_meta_path = initial_meta_path
        self._current_meta_path = current_meta_path
        self._modules_since_install = modules_since_install
        self._events = events
        self._skipped_finders = skipped_finders
        self._findings = findings
        self._report_errors = report_errors
        self._cwd = cwd
        self._argv = argv


def capture_document(monitor: "Monitor") -> ReportDocument:
    """Copy evidence at one sequence cutoff before deriving any findings."""
    cutoff_seq, events, skipped_raw = monitor._report_state()
    report_errors: list[ReportError] = []
    current_meta_path = _current_meta_path_names(report_errors)
    modules_since_install = _modules_since_install(monitor, report_errors)
    skipped_finders = tuple(
        SkippedFinder(
            type_name(finder),
            reason,
            reason.startswith(_STANDARD_CLASS_FINDER_REASON_PREFIX),
        )
        for finder, reason in skipped_raw
    )
    previously_active = monitor._begin_report_analysis()
    try:
        findings = _suspicious_findings(monitor, events, report_errors)
    finally:
        monitor._end_report_analysis(previously_active)
    try:
        cwd: str | None = os.getcwd()
    except Exception as exc:
        cwd = None
        report_errors.append(ReportError("process.cwd", type_name(exc)))
    argv: list[str] = []
    try:
        argv.extend(value if isinstance(value, str) else f"<{type_name(value)}>" for value in list(sys.argv))
    except Exception as exc:
        report_errors.append(ReportError("process.argv", type_name(exc)))
    return ReportDocument(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        cutoff_seq=cutoff_seq,
        monitor_enabled=monitor.enabled,
        baseline_module_count=len(monitor.baseline_modules),
        initial_meta_path=monitor.initial_meta_path,
        current_meta_path=current_meta_path,
        modules_since_install=modules_since_install,
        events=tuple(events),
        skipped_finders=skipped_finders,
        findings=findings,
        report_errors=tuple(report_errors),
        cwd=cwd,
        argv=tuple(argv),
    )


def _current_meta_path_names(report_errors: list[ReportError]) -> tuple[str, ...] | None:
    """Name current meta-path entries, tolerating broken shutdown state."""
    try:
        return tuple(type_name(finder) for finder in list(sys.meta_path))
    except Exception as exc:
        report_errors.append(ReportError("snapshot.meta_path", type_name(exc)))
        return None


def _modules_since_install(monitor: "Monitor", report_errors: list[ReportError]) -> tuple[str, ...] | None:
    """List module names added after monitor installation."""
    baseline = monitor.baseline_modules
    try:
        return tuple(name for name in list(sys.modules) if isinstance(name, str) and name not in baseline)
    except Exception as exc:
        report_errors.append(ReportError("snapshot.sys_modules", type_name(exc)))
        return None


def _suspicious_findings(
    monitor: "Monitor",
    events: list[MonitorEvent],
    report_errors: list[ReportError],
) -> tuple[Finding, ...]:
    """Cross-reference copied calls against a best-effort module snapshot."""
    winners = {event.fullname: event for event in events if isinstance(event, FindSpecCall) and event.found}
    findings: list[Finding] = []
    baseline = monitor.baseline_modules
    try:
        module_items = list(sys.modules.items())
    except Exception as exc:
        report_errors.append(ReportError("findings.sys_modules", type_name(exc)))
        return ()
    for name, module in module_items:
        if not isinstance(name, str) or name in baseline or name == "__main__":
            continue
        winner = winners.get(name)
        if winner is not None:
            finding = _bypass_finding(name, winner, len(findings) + 1, report_errors)
            if finding is not None:
                findings.append(finding)
            continue
        # Preserve the existing BaseException boundary: ordinary hostile
        # metadata becomes a no-spec lead, while process-control exceptions
        # raised by foreign objects continue to propagate.
        try:
            spec = getattr(module, "__spec__", None)
        except Exception:
            spec = None
        if spec is None:
            findings.append(Finding(f"finding:{len(findings) + 1}", "no_spec", name))
    return tuple(findings)


def _bypass_finding(
    name: str,
    winner: FindSpecCall,
    finding_number: int,
    report_errors: list[ReportError],
) -> Finding | None:
    """Compare one custom source claim with a report-time PathFinder replay."""
    origin = winner.origin
    if origin is None or not origin.endswith(_SOURCE_SUFFIXES) or winner.loader_type_name is None:
        return None
    replay = _replay_path_finder(name, winner.search_path)
    if replay.status == "failed":
        report_errors.append(ReportError("path_finder_replay", replay.exception_type_name or "Exception"))
        return None
    if replay.status == "not_found":
        return Finding(f"finding:{finding_number}", "unfindable", name, winner, replay)
    if replay.loader_type_name != winner.loader_type_name or not _same_path(replay.origin, origin):
        return Finding(f"finding:{finding_number}", "bypass", name, winner, replay)
    return None


def _replay_path_finder(name: str, search_path: tuple[str, ...]) -> ReplayResult:
    """Replay PathFinder without reporting a replay failure as not-found."""
    try:
        spec = PathFinder.find_spec(name, search_path)
        if spec is None:
            return ReplayResult("not_found")
        loader_type_name = None if spec.loader is None else type_name(spec.loader)
        origin = spec.origin if isinstance(spec.origin, str) else None
        return ReplayResult("found", loader_type_name, origin)
    except Exception as exc:  # A broken finder chain must not break the report.
        return ReplayResult("failed", exception_type_name=type_name(exc))


def _same_path(a: str | None, b: str | None) -> bool:
    """Compare filesystem paths after absolutization and case normalization."""
    if a is None or b is None:
        return a == b
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def json_document(document: ReportDocument) -> dict[str, object]:
    """Project one report document onto the experimental JSON schema."""
    mutations = sum(isinstance(event, MetaPathMutation) for event in document.events)
    reassignments = sum(isinstance(event, MetaPathReassignment) for event in document.events)
    calls = sum(isinstance(event, FindSpecCall) for event in document.events)
    version = sys.version_info
    return {
        "schema": {"major": _SCHEMA_MAJOR, "minor": _SCHEMA_MINOR, "name": _SCHEMA_NAME},
        "tool": {"name": "metapathology", "version": __version__},
        "generated_at": document.generated_at,
        "process": {
            "argv": list(document.argv),
            "cwd": document.cwd,
            "executable": sys.executable if isinstance(sys.executable, str) else None,
            "implementation": sys.implementation.name,
            "parent_pid": os.getppid(),
            "pid": os.getpid(),
            "platform": sys.platform,
            "python_version": f"{version.major}.{version.minor}.{version.micro}",
        },
        "capture": {
            "baseline_module_count": document.baseline_module_count,
            "cutoff_seq": document.cutoff_seq,
            "enabled": document.monitor_enabled,
            "mechanisms": [
                _mechanism("meta_path_mutations", document.monitor_enabled, mutations, "best_effort"),
                _mechanism("meta_path_reassignments", document.monitor_enabled, reassignments, "import_boundaries"),
                _mechanism("finder_attribution", document.monitor_enabled, calls, "instrumented_finders"),
            ],
            "modules_since_install": None
            if document.modules_since_install is None
            else list(document.modules_since_install),
        },
        "snapshots": [
            {
                "entries": list(document.initial_meta_path),
                "id": "snapshot:install",
                "kind": "meta_path",
                "phase": "install",
            },
            {
                "entries": None if document.current_meta_path is None else list(document.current_meta_path),
                "id": "snapshot:report",
                "kind": "meta_path",
                "phase": "report",
            },
        ],
        "timeline": [_json_event(event) for event in document.events],
        "findings": [_json_finding(finding) for finding in document.findings],
        "diagnostics": {
            "internal_error_refs": [
                f"event:{event.seq}" for event in document.events if isinstance(event, InternalError)
            ],
            "report_errors": [
                {"exception_type_name": error.exception_type_name, "where": error.where}
                for error in document.report_errors
            ],
            "skipped_finders": [
                {
                    "expected": skipped.expected,
                    "finder_type_name": skipped.finder_type_name,
                    "reason": skipped.reason,
                }
                for skipped in document.skipped_finders
            ],
        },
    }


def _mechanism(name: str, enabled: bool, retained: int, completeness: str) -> dict[str, object]:
    """Describe a lifetime-growing producer without implying perfect observation."""
    return {
        "capacity": None,
        "completeness": completeness,
        "dropped": 0,
        "enabled": enabled,
        "name": name,
        "overflow_policy": "retain_all",
        "retained": retained,
    }


def _json_event(event: MonitorEvent) -> dict[str, object]:
    """Serialize a public event record without inspecting foreign objects."""
    result: dict[str, object] = {"id": f"event:{event.seq}", "seq": event.seq}
    if isinstance(event, FindSpecCall):
        result.update(
            {
                "exception_type_name": event.exception_type_name,
                "finder_id": f"0x{event.finder_id:x}",
                "finder_type_name": event.finder_type_name,
                "found": event.found,
                "fullname": event.fullname,
                "kind": "find_spec_call",
                "loader_type_name": event.loader_type_name,
                "origin": event.origin,
                "search_path": list(event.search_path),
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, MetaPathMutation):
        result.update(
            {
                "added": list(event.added),
                "contents_after": list(event.contents_after),
                "kind": "meta_path_mutation",
                "op": event.op,
                "removed": list(event.removed),
                "stack": [_json_frame(frame) for frame in event.stack],
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, MetaPathReassignment):
        result.update(
            {
                "during_import": event.during_import,
                "kind": "meta_path_reassignment",
                "new_contents": list(event.new_contents),
                "old_contents": list(event.old_contents),
                "stack": [_json_frame(frame) for frame in event.stack],
                "thread_name": event.thread_name,
            }
        )
    else:
        result.update(
            {
                "exception_type_name": event.exception_type_name,
                "kind": "internal_error",
                "message": event.message,
                "where": event.where,
            }
        )
    return result


def _json_frame(frame: "FrameSummary") -> dict[str, object]:
    """Serialize a captured frame without resolving its source line."""
    return {"filename": frame.filename, "function": frame.name, "lineno": frame.lineno}


def _json_finding(finding: Finding) -> dict[str, object]:
    """Serialize mechanics; human wording is deliberately not contractual."""
    result: dict[str, object] = {"id": finding.finding_id, "kind": finding.kind, "module": finding.module}
    if finding.claim is not None:
        result["claim"] = {
            "event_ref": f"event:{finding.claim.seq}",
            "finder_id": f"0x{finding.claim.finder_id:x}",
            "finder_type_name": finding.claim.finder_type_name,
            "loader_type_name": finding.claim.loader_type_name,
            "origin": finding.claim.origin,
            "search_path": list(finding.claim.search_path),
        }
    if finding.replay is not None:
        result["path_finder_replay"] = {
            "exception_type_name": finding.replay.exception_type_name,
            "loader_type_name": finding.replay.loader_type_name,
            "origin": finding.replay.origin,
            "status": finding.replay.status,
        }
    if finding.kind == "no_spec":
        result["evidence"] = {"finder_claim": "not_recorded", "module_spec": "missing"}
    return result


def failed_json_document(error_name: str) -> dict[str, object]:
    """Return valid JSON structure when ordinary report generation fails."""
    return {
        "schema": {"major": _SCHEMA_MAJOR, "minor": _SCHEMA_MINOR, "name": _SCHEMA_NAME},
        "tool": {"name": "metapathology", "version": __version__},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "process": {"pid": os.getpid()},
        "capture": {"cutoff_seq": None, "enabled": False, "mechanisms": []},
        "snapshots": [],
        "timeline": [],
        "findings": [],
        "diagnostics": {
            "internal_error_refs": [],
            "report_errors": [{"exception_type_name": error_name, "where": "report_generation"}],
            "skipped_finders": [],
        },
    }
