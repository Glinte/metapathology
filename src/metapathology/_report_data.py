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
    DeepDiagnosticCall,
    FindSpecCall,
    ImportAuditStart,
    ImporterCacheDiff,
    ImporterCacheEntry,
    ImporterCacheReplacement,
    ImportObjectRef,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
    PathHooksMutation,
    PathHooksReassignment,
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
# roadmap T2--T7 have supplied their real event and evidence shapes.
_SCHEMA_NAME = "metapathology.report"
_SCHEMA_MAJOR = 0
_SCHEMA_MINOR = 7
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

    __slots__ = (
        "_evidence_level",
        "_exception_type_name",
        "_loader_type_name",
        "_origin",
        "_state_phase",
        "_status",
    )
    _fields = ("evidence_level", "state_phase", "status", "loader_type_name", "origin", "exception_type_name")
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    state_phase = _ReadOnlyField[str]("_state_phase")
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
        self._evidence_level = "live_replay"
        self._state_phase = "report"
        self._status = status
        self._loader_type_name = loader_type_name
        self._origin = origin
        self._exception_type_name = exception_type_name


class StructuralComparison(_Record):
    """Identity-only comparison of install and report-time import structure."""

    __slots__ = (
        "_evidence_level",
        "_importer_cache_changed",
        "_importer_cache_changed_paths",
        "_importer_cache_event_seqs",
        "_path_hooks_changed",
    )
    _fields = (
        "evidence_level",
        "path_hooks_changed",
        "importer_cache_changed",
        "importer_cache_changed_paths",
        "importer_cache_event_seqs",
    )
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    path_hooks_changed = _ReadOnlyField[bool | None]("_path_hooks_changed")
    importer_cache_changed = _ReadOnlyField[bool | None]("_importer_cache_changed")
    importer_cache_changed_paths = _ReadOnlyField[tuple[str, ...]]("_importer_cache_changed_paths")
    importer_cache_event_seqs = _ReadOnlyField[tuple[int, ...]]("_importer_cache_event_seqs")

    def __init__(
        self,
        path_hooks_changed: bool | None,
        importer_cache_changed: bool | None,
        importer_cache_changed_paths: tuple[str, ...],
        importer_cache_event_seqs: tuple[int, ...],
    ) -> None:
        self._evidence_level = "structural_comparison"
        self._path_hooks_changed = path_hooks_changed
        self._importer_cache_changed = importer_cache_changed
        self._importer_cache_changed_paths = importer_cache_changed_paths
        self._importer_cache_event_seqs = importer_cache_event_seqs


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


class Finding(_Record):
    """Structured evidence for one human or machine-readable finding."""

    __slots__ = ("_claim", "_finding_id", "_kind", "_module", "_replay", "_structural_comparison")
    _fields = ("finding_id", "kind", "module", "claim", "replay", "structural_comparison")
    finding_id = _ReadOnlyField[str]("_finding_id")
    kind = _ReadOnlyField[str]("_kind")
    module = _ReadOnlyField[str]("_module")
    claim = _ReadOnlyField[FindSpecCall | None]("_claim")
    replay = _ReadOnlyField[ReplayResult | None]("_replay")
    structural_comparison = _ReadOnlyField[StructuralComparison | None]("_structural_comparison")

    def __init__(
        self,
        finding_id: str,
        kind: str,
        module: str,
        claim: FindSpecCall | None = None,
        replay: ReplayResult | None = None,
        structural_comparison: StructuralComparison | None = None,
    ) -> None:
        self._finding_id = finding_id
        self._kind = kind
        self._module = module
        self._claim = claim
        self._replay = replay
        self._structural_comparison = structural_comparison


class EarlySiteBootstrap(_Record):
    """Report-safe provenance for generated early site activation."""

    __slots__ = ("_activation_source", "_earlier_pth_files", "_path", "_site_packages")
    _fields = ("path", "site_packages", "activation_source", "earlier_pth_files")
    path = _ReadOnlyField[str]("_path")
    site_packages = _ReadOnlyField[str]("_site_packages")
    activation_source = _ReadOnlyField[str]("_activation_source")
    earlier_pth_files = _ReadOnlyField[tuple[str, ...]]("_earlier_pth_files")

    def __init__(
        self,
        path: str,
        site_packages: str,
        activation_source: str,
        earlier_pth_files: tuple[str, ...],
    ) -> None:
        self._path = path
        self._site_packages = site_packages
        self._activation_source = activation_source
        self._earlier_pth_files = earlier_pth_files


class ReportDocument(_Record):
    """One sequence-cutoff snapshot shared by all report renderers."""

    __slots__ = (
        "_argv",
        "_baseline_module_count",
        "_current_importer_cache",
        "_current_importer_cache_non_string_keys",
        "_current_meta_path",
        "_current_path_hooks",
        "_cutoff_seq",
        "_cwd",
        "_deep_diagnostics",
        "_early_site_bootstrap",
        "_events",
        "_findings",
        "_generated_at",
        "_importer_cache_coalesced",
        "_importer_cache_enabled",
        "_importer_cache_observations",
        "_initial_importer_cache",
        "_initial_importer_cache_non_string_keys",
        "_initial_meta_path",
        "_initial_path_hooks",
        "_modules_since_install",
        "_monitor_enabled",
        "_path_hooks_enabled",
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
        "path_hooks_enabled",
        "initial_path_hooks",
        "current_path_hooks",
        "importer_cache_enabled",
        "initial_importer_cache",
        "initial_importer_cache_non_string_keys",
        "current_importer_cache",
        "current_importer_cache_non_string_keys",
        "importer_cache_observations",
        "importer_cache_coalesced",
        "modules_since_install",
        "events",
        "early_site_bootstrap",
        "deep_diagnostics",
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
    path_hooks_enabled = _ReadOnlyField[bool]("_path_hooks_enabled")
    initial_path_hooks = _ReadOnlyField[tuple[ImportObjectRef, ...]]("_initial_path_hooks")
    current_path_hooks = _ReadOnlyField[tuple[ImportObjectRef, ...] | None]("_current_path_hooks")
    importer_cache_enabled = _ReadOnlyField[bool]("_importer_cache_enabled")
    initial_importer_cache = _ReadOnlyField[tuple[ImporterCacheEntry, ...]]("_initial_importer_cache")
    initial_importer_cache_non_string_keys = _ReadOnlyField[int]("_initial_importer_cache_non_string_keys")
    current_importer_cache = _ReadOnlyField[tuple[ImporterCacheEntry, ...] | None]("_current_importer_cache")
    current_importer_cache_non_string_keys = _ReadOnlyField[int | None]("_current_importer_cache_non_string_keys")
    importer_cache_observations = _ReadOnlyField[int]("_importer_cache_observations")
    importer_cache_coalesced = _ReadOnlyField[int]("_importer_cache_coalesced")
    modules_since_install = _ReadOnlyField[tuple[str, ...] | None]("_modules_since_install")
    events = _ReadOnlyField[tuple[MonitorEvent, ...]]("_events")
    early_site_bootstrap = _ReadOnlyField[EarlySiteBootstrap | None]("_early_site_bootstrap")
    deep_diagnostics = _ReadOnlyField[tuple[str, ...]]("_deep_diagnostics")
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
        path_hooks_enabled: bool,
        initial_path_hooks: tuple[ImportObjectRef, ...],
        current_path_hooks: tuple[ImportObjectRef, ...] | None,
        importer_cache_enabled: bool,
        initial_importer_cache: tuple[ImporterCacheEntry, ...],
        initial_importer_cache_non_string_keys: int,
        current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
        current_importer_cache_non_string_keys: int | None,
        importer_cache_observations: int,
        importer_cache_coalesced: int,
        modules_since_install: tuple[str, ...] | None,
        events: tuple[MonitorEvent, ...],
        early_site_bootstrap: EarlySiteBootstrap | None,
        deep_diagnostics: tuple[str, ...],
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
        self._path_hooks_enabled = path_hooks_enabled
        self._initial_path_hooks = initial_path_hooks
        self._current_path_hooks = current_path_hooks
        self._importer_cache_enabled = importer_cache_enabled
        self._initial_importer_cache = initial_importer_cache
        self._initial_importer_cache_non_string_keys = initial_importer_cache_non_string_keys
        self._current_importer_cache = current_importer_cache
        self._current_importer_cache_non_string_keys = current_importer_cache_non_string_keys
        self._importer_cache_observations = importer_cache_observations
        self._importer_cache_coalesced = importer_cache_coalesced
        self._modules_since_install = modules_since_install
        self._events = events
        self._early_site_bootstrap = early_site_bootstrap
        self._deep_diagnostics = deep_diagnostics
        self._skipped_finders = skipped_finders
        self._findings = findings
        self._report_errors = report_errors
        self._cwd = cwd
        self._argv = argv


def capture_document(monitor: "Monitor") -> ReportDocument:
    """Copy evidence at one sequence cutoff before deriving any findings."""
    cutoff_seq, events, skipped_raw, importer_cache, early_site_bootstrap_raw = monitor._report_state()
    report_errors: list[ReportError] = []
    current_meta_path = _current_meta_path_names(report_errors)
    current_path_hooks = _current_path_hooks(monitor, report_errors)
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
        findings = _suspicious_findings(
            monitor,
            events,
            monitor.initial_path_hooks,
            current_path_hooks,
            importer_cache.initial_entries,
            importer_cache.latest_entries,
            report_errors,
        )
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
    early_site_bootstrap = (
        None
        if early_site_bootstrap_raw is None
        else EarlySiteBootstrap(
            early_site_bootstrap_raw.path,
            early_site_bootstrap_raw.site_packages,
            early_site_bootstrap_raw.activation_source,
            early_site_bootstrap_raw.earlier_pth_files,
        )
    )
    return ReportDocument(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        cutoff_seq=cutoff_seq,
        monitor_enabled=monitor.enabled,
        baseline_module_count=len(monitor.baseline_modules),
        initial_meta_path=monitor.initial_meta_path,
        current_meta_path=current_meta_path,
        path_hooks_enabled=monitor.path_hooks_enabled,
        initial_path_hooks=monitor.initial_path_hooks,
        current_path_hooks=current_path_hooks,
        importer_cache_enabled=importer_cache.enabled,
        initial_importer_cache=importer_cache.initial_entries,
        initial_importer_cache_non_string_keys=importer_cache.initial_non_string_keys,
        current_importer_cache=importer_cache.latest_entries,
        current_importer_cache_non_string_keys=importer_cache.latest_non_string_keys,
        importer_cache_observations=importer_cache.observations,
        importer_cache_coalesced=importer_cache.coalesced,
        modules_since_install=modules_since_install,
        events=tuple(events),
        early_site_bootstrap=early_site_bootstrap,
        deep_diagnostics=monitor.deep_diagnostics,
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


def _current_path_hooks(monitor: "Monitor", report_errors: list[ReportError]) -> tuple[ImportObjectRef, ...] | None:
    """Copy current path-hook identities when T1 is active."""
    if not monitor.path_hooks_enabled:
        return None
    try:
        return monitor._current_path_hook_refs()
    except Exception as exc:
        report_errors.append(ReportError("snapshot.path_hooks", type_name(exc)))
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
    initial_path_hooks: tuple[ImportObjectRef, ...],
    current_path_hooks: tuple[ImportObjectRef, ...] | None,
    initial_importer_cache: tuple[ImporterCacheEntry, ...],
    current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
    report_errors: list[ReportError],
) -> tuple[Finding, ...]:
    """Cross-reference copied calls against a best-effort module snapshot."""
    winners = {event.fullname: event for event in events if isinstance(event, FindSpecCall) and event.found}
    structural_context = _structural_context(
        events,
        initial_path_hooks,
        current_path_hooks,
        initial_importer_cache,
        current_importer_cache,
    )
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
            finding = _bypass_finding(name, winner, len(findings) + 1, structural_context, report_errors)
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
    structural_context: _StructuralContext,
    report_errors: list[ReportError],
) -> Finding | None:
    """Compare one custom source claim with a report-time PathFinder replay."""
    origin = winner.origin
    if origin is None or not origin.endswith(_SOURCE_SUFFIXES) or winner.loader_type_name is None:
        return None
    structural_comparison = _structural_comparison(winner.search_path, structural_context)
    replay = _replay_path_finder(name, winner.search_path)
    if replay.status == "failed":
        report_errors.append(ReportError("path_finder_replay", replay.exception_type_name or "Exception"))
        return None
    if replay.status == "not_found":
        return Finding(f"finding:{finding_number}", "unfindable", name, winner, replay, structural_comparison)
    if replay.loader_type_name != winner.loader_type_name or not _same_path(replay.origin, origin):
        return Finding(f"finding:{finding_number}", "bypass", name, winner, replay, structural_comparison)
    return None


def _structural_context(
    events: list[MonitorEvent],
    initial_path_hooks: tuple[ImportObjectRef, ...],
    current_path_hooks: tuple[ImportObjectRef, ...] | None,
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


def _import_object_signatures(references: tuple[ImportObjectRef, ...]) -> tuple[tuple[int, str], ...]:
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


def _cache_finder_signature(finder: ImportObjectRef | None) -> tuple[int | None, str | None]:
    """Describe a cached finder identity while preserving negative entries."""
    if finder is None:
        return None, None
    return finder.object_id, finder.type_name


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
    path_hook_mutations = sum(isinstance(event, PathHooksMutation) for event in document.events)
    path_hook_reassignments = sum(isinstance(event, PathHooksReassignment) for event in document.events)
    importer_cache_diffs = sum(isinstance(event, ImporterCacheDiff) for event in document.events)
    audit_starts = sum(isinstance(event, ImportAuditStart) for event in document.events)
    calls = sum(isinstance(event, FindSpecCall) for event in document.events)
    deep_calls = sum(isinstance(event, DeepDiagnosticCall) for event in document.events)
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
            "early_site_bootstrap": _json_early_site_bootstrap(document.early_site_bootstrap),
            "enabled": document.monitor_enabled,
            "mechanisms": [
                _mechanism("meta_path_mutations", document.monitor_enabled, mutations, "best_effort"),
                _mechanism("meta_path_reassignments", document.monitor_enabled, reassignments, "import_boundaries"),
                _mechanism("import_audit_starts", document.monitor_enabled, audit_starts, "resolution_starts"),
                _mechanism("finder_attribution", document.monitor_enabled, calls, "instrumented_finders"),
                _mechanism("deep_diagnostics", bool(document.deep_diagnostics), deep_calls, "delegated_boundaries"),
                _mechanism("path_hooks_mutations", document.path_hooks_enabled, path_hook_mutations, "best_effort"),
                _mechanism(
                    "path_hooks_reassignments",
                    document.path_hooks_enabled,
                    path_hook_reassignments,
                    "import_boundaries",
                ),
                {
                    "capacity": 2,
                    "coalesced": document.importer_cache_coalesced,
                    "completeness": "passive_boundaries",
                    "dropped": 0,
                    "enabled": document.importer_cache_enabled,
                    "name": "importer_cache_snapshots",
                    "observations": document.importer_cache_observations,
                    "overflow_policy": "replace_latest",
                    "retained": min(document.importer_cache_observations, 2),
                },
                _mechanism(
                    "importer_cache_diffs",
                    document.importer_cache_enabled,
                    importer_cache_diffs,
                    "passive_boundaries",
                ),
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
            {
                "entries": [_json_import_object(reference) for reference in document.initial_path_hooks],
                "id": "snapshot:path-hooks:install",
                "kind": "path_hooks",
                "phase": "install",
            },
            {
                "entries": None
                if document.current_path_hooks is None
                else [_json_import_object(reference) for reference in document.current_path_hooks],
                "id": "snapshot:path-hooks:report",
                "kind": "path_hooks",
                "phase": "report",
            },
            {
                "entries": [_json_importer_cache_entry(entry) for entry in document.initial_importer_cache],
                "id": "snapshot:importer-cache:install",
                "kind": "importer_cache",
                "non_string_keys": document.initial_importer_cache_non_string_keys,
                "phase": "install",
            },
            {
                "entries": None
                if document.current_importer_cache is None
                else [_json_importer_cache_entry(entry) for entry in document.current_importer_cache],
                "id": "snapshot:importer-cache:report",
                "kind": "importer_cache",
                "non_string_keys": document.current_importer_cache_non_string_keys,
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


def _json_early_site_bootstrap(bootstrap: EarlySiteBootstrap | None) -> dict[str, object] | None:
    """Serialize early activation provenance without consulting live state."""
    if bootstrap is None:
        return None
    return {
        "activation_source": bootstrap.activation_source,
        "earlier_pth_files": list(bootstrap.earlier_pth_files),
        "path": bootstrap.path,
        "site_packages": bootstrap.site_packages,
    }


def _json_event(event: MonitorEvent) -> dict[str, object]:
    """Serialize a public event record without inspecting foreign objects."""
    result: dict[str, object] = {"id": f"event:{event.seq}", "seq": event.seq}
    if isinstance(event, ImportAuditStart):
        result.update(
            {
                "evidence": "resolution_started",
                "fullname": event.fullname,
                "importer_cache": None
                if event.importer_cache_id is None
                else {
                    "object_id": f"0x{event.importer_cache_id:x}",
                    "size": event.importer_cache_size,
                },
                "kind": "import_audit_start",
                "meta_path": {
                    "entries": list(event.meta_path_type_names),
                    "object_id": f"0x{event.meta_path_id:x}",
                },
                "path_hooks_id": None if event.path_hooks_id is None else f"0x{event.path_hooks_id:x}",
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, DeepDiagnosticCall):
        result.update(
            {
                "boundary": event.boundary,
                "evidence": "deep_delegation",
                "exception_type_name": event.exception_type_name,
                "fullname": event.fullname,
                "kind": "deep_diagnostic_call",
                "object_id": f"0x{event.object_id:x}",
                "object_type_name": event.object_type_name,
                "outcome": event.outcome,
                "path": event.path,
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, FindSpecCall):
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
    elif isinstance(event, ImporterCacheDiff):
        result.update(
            {
                "added": [_json_importer_cache_entry(entry) for entry in event.added],
                "kind": "importer_cache_diff",
                "non_string_keys_after": event.non_string_keys_after,
                "non_string_keys_before": event.non_string_keys_before,
                "observation": event.observation,
                "removed": [_json_importer_cache_entry(entry) for entry in event.removed],
                "replaced": [_json_importer_cache_replacement(item) for item in event.replaced],
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
    elif isinstance(event, PathHooksMutation):
        result.update(
            {
                "added": [_json_import_object(reference) for reference in event.added],
                "contents_after": [_json_import_object(reference) for reference in event.contents_after],
                "kind": "path_hooks_mutation",
                "op": event.op,
                "removed": [_json_import_object(reference) for reference in event.removed],
                "stack": [_json_frame(frame) for frame in event.stack],
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, PathHooksReassignment):
        result.update(
            {
                "during_import": event.during_import,
                "kind": "path_hooks_reassignment",
                "new_contents": [_json_import_object(reference) for reference in event.new_contents],
                "old_contents": [_json_import_object(reference) for reference in event.old_contents],
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


def _json_import_object(reference: ImportObjectRef) -> dict[str, object]:
    """Serialize safe import-object identity metadata."""
    return {
        "name": reference.name,
        "object_id": f"0x{reference.object_id:x}",
        "type_name": reference.type_name,
    }


def _json_importer_cache_entry(entry: ImporterCacheEntry) -> dict[str, object]:
    """Serialize one path and its cached finder or negative marker."""
    return {
        "finder": None if entry.finder is None else _json_import_object(entry.finder),
        "path": entry.path,
    }


def _json_importer_cache_replacement(replacement: ImporterCacheReplacement) -> dict[str, object]:
    """Serialize one cached-finder replacement."""
    return {
        "after": None if replacement.after is None else _json_import_object(replacement.after),
        "before": None if replacement.before is None else _json_import_object(replacement.before),
        "path": replacement.path,
    }


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
            "evidence_level": finding.replay.evidence_level,
            "exception_type_name": finding.replay.exception_type_name,
            "loader_type_name": finding.replay.loader_type_name,
            "origin": finding.replay.origin,
            "state_phase": finding.replay.state_phase,
            "status": finding.replay.status,
        }
    if finding.structural_comparison is not None:
        comparison = finding.structural_comparison
        result["structural_comparison"] = {
            "evidence_level": comparison.evidence_level,
            "importer_cache": {
                "changed": comparison.importer_cache_changed,
                "changed_paths": list(comparison.importer_cache_changed_paths),
                "change_event_refs": [f"event:{seq}" for seq in comparison.importer_cache_event_seqs],
                "install_snapshot_ref": "snapshot:importer-cache:install",
                "report_snapshot_ref": "snapshot:importer-cache:report",
            },
            "path_hooks": {
                "changed": comparison.path_hooks_changed,
                "install_snapshot_ref": "snapshot:path-hooks:install",
                "report_snapshot_ref": "snapshot:path-hooks:report",
            },
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
