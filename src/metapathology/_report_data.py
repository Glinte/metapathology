"""Cutoff-based report evidence and its machine-readable projection.

The monitor records plain event data in import hot paths. This module copies
that data and the relevant live interpreter state exactly once, then performs
report-time analysis. Renderers consume the resulting document and never
inspect the interpreter independently.
"""

import os
import sys
import time
import types
from importlib.machinery import PathFinder

from metapathology import __version__
from metapathology._module_metadata import ModuleMetadata, inspect_module
from metapathology._records import (
    DeepDiagnosticCall,
    DeepImportEvent,
    FinderContract,
    FindSpecCall,
    ImportAuditStart,
    ImporterCacheDiff,
    ImporterCacheEntry,
    ImporterCacheReplacement,
    ImportObjectRef,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    ModuleCacheState,
    MonitorEvent,
    PathHooksMutation,
    PathHooksReassignment,
    SpecSummary,
    StandardFinderCall,
    _ReadOnlyField,
    _Record,
    type_name,
)
from metapathology._spec import summarize_spec

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
_SCHEMA_MINOR = 16
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


class LoaderInventory(_Record):
    """Post-hoc metadata copied from one ``sys.modules`` snapshot."""

    __slots__ = ("_available", "_entries", "_non_string_keys")
    _fields = ("available", "entries", "non_string_keys")
    available = _ReadOnlyField[bool]("_available")
    entries = _ReadOnlyField[tuple[ModuleMetadata, ...]]("_entries")
    non_string_keys = _ReadOnlyField[int]("_non_string_keys")

    def __init__(self, available: bool, entries: tuple[ModuleMetadata, ...], non_string_keys: int) -> None:
        self._available = available
        self._entries = entries
        self._non_string_keys = non_string_keys


class ImportAttempt(_Record):
    """Conservative report-time correlation of one import audit start."""

    __slots__ = (
        "_attempt_id",
        "_event_seqs",
        "_fullname",
        "_presence",
        "_progress",
        "_start_event_seq",
        "_thread_id",
        "_thread_name",
    )
    _fields = (
        "attempt_id",
        "fullname",
        "start_event_seq",
        "event_seqs",
        "thread_id",
        "thread_name",
        "progress",
        "presence",
    )
    attempt_id = _ReadOnlyField[int]("_attempt_id")
    fullname = _ReadOnlyField[str]("_fullname")
    start_event_seq = _ReadOnlyField[int]("_start_event_seq")
    event_seqs = _ReadOnlyField[tuple[int, ...]]("_event_seqs")
    thread_id = _ReadOnlyField[int]("_thread_id")
    thread_name = _ReadOnlyField[str]("_thread_name")
    progress = _ReadOnlyField[str]("_progress")
    presence = _ReadOnlyField[str]("_presence")

    def __init__(
        self,
        attempt_id: int,
        fullname: str,
        start_event_seq: int,
        event_seqs: tuple[int, ...],
        thread_id: int,
        thread_name: str,
        progress: str,
        presence: str,
    ) -> None:
        self._attempt_id = attempt_id
        self._fullname = fullname
        self._start_event_seq = start_event_seq
        self._event_seqs = event_seqs
        self._thread_id = thread_id
        self._thread_name = thread_name
        self._progress = progress
        self._presence = presence


class StandardResolution(_Record):
    """Captured or conservatively inferred standard resolution evidence."""

    __slots__ = (
        "_attempt_id",
        "_category",
        "_component_event_seqs",
        "_event_seq",
        "_evidence_level",
        "_finder_type_name",
        "_fullname",
        "_later_finders",
        "_loader_type_name",
        "_origin",
        "_state_phase",
    )
    _fields = (
        "attempt_id",
        "fullname",
        "finder_type_name",
        "category",
        "loader_type_name",
        "origin",
        "evidence_level",
        "state_phase",
        "event_seq",
        "component_event_seqs",
        "later_finders",
    )
    attempt_id = _ReadOnlyField[int]("_attempt_id")
    fullname = _ReadOnlyField[str]("_fullname")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    category = _ReadOnlyField[str]("_category")
    loader_type_name = _ReadOnlyField[str | None]("_loader_type_name")
    origin = _ReadOnlyField[str | None]("_origin")
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    state_phase = _ReadOnlyField[str]("_state_phase")
    event_seq = _ReadOnlyField[int | None]("_event_seq")
    component_event_seqs = _ReadOnlyField[tuple[int, ...]]("_component_event_seqs")
    later_finders = _ReadOnlyField[tuple[str, ...]]("_later_finders")

    def __init__(
        self,
        *,
        attempt_id: int,
        fullname: str,
        finder_type_name: str,
        category: str,
        loader_type_name: str | None,
        origin: str | None,
        evidence_level: str,
        state_phase: str,
        event_seq: int | None,
        component_event_seqs: tuple[int, ...],
        later_finders: tuple[str, ...],
    ) -> None:
        self._attempt_id = attempt_id
        self._fullname = fullname
        self._finder_type_name = finder_type_name
        self._category = category
        self._loader_type_name = loader_type_name
        self._origin = origin
        self._evidence_level = evidence_level
        self._state_phase = state_phase
        self._event_seq = event_seq
        self._component_event_seqs = component_event_seqs
        self._later_finders = later_finders


class ReplayResult(_Record):
    """PathFinder outcome that does not conflate failure with absence."""

    __slots__ = (
        "_evidence_level",
        "_exception_type_name",
        "_loader_type_name",
        "_origin",
        "_spec_summary",
        "_state_phase",
        "_status",
    )
    _fields = (
        "evidence_level",
        "state_phase",
        "status",
        "loader_type_name",
        "origin",
        "spec_summary",
        "exception_type_name",
    )
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    state_phase = _ReadOnlyField[str]("_state_phase")
    status = _ReadOnlyField[str]("_status")
    loader_type_name = _ReadOnlyField[str | None]("_loader_type_name")
    origin = _ReadOnlyField[str | None]("_origin")
    spec_summary = _ReadOnlyField[SpecSummary | None]("_spec_summary")
    exception_type_name = _ReadOnlyField[str | None]("_exception_type_name")

    def __init__(
        self,
        status: str,
        loader_type_name: str | None = None,
        origin: str | None = None,
        spec_summary: SpecSummary | None = None,
        exception_type_name: str | None = None,
    ) -> None:
        self._evidence_level = "live_replay"
        self._state_phase = "report"
        self._status = status
        self._loader_type_name = loader_type_name
        self._origin = origin
        self._spec_summary = spec_summary
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


class SpecComparison(_Record):
    """Field-level differences between a captured claim and live replay."""

    __slots__ = (
        "_additional_locations",
        "_cached_changed",
        "_complete",
        "_loader_type_changed",
        "_locations_reordered",
        "_observed_locations_state",
        "_omitted_locations",
        "_origin_changed",
        "_package_status_changed",
        "_replayed_locations_state",
    )
    _fields = (
        "complete",
        "loader_type_changed",
        "origin_changed",
        "cached_changed",
        "package_status_changed",
        "omitted_locations",
        "additional_locations",
        "locations_reordered",
        "observed_locations_state",
        "replayed_locations_state",
    )
    complete = _ReadOnlyField[bool]("_complete")
    loader_type_changed = _ReadOnlyField[bool | None]("_loader_type_changed")
    origin_changed = _ReadOnlyField[bool | None]("_origin_changed")
    cached_changed = _ReadOnlyField[bool | None]("_cached_changed")
    package_status_changed = _ReadOnlyField[bool | None]("_package_status_changed")
    omitted_locations = _ReadOnlyField[tuple[str, ...]]("_omitted_locations")
    additional_locations = _ReadOnlyField[tuple[str, ...]]("_additional_locations")
    locations_reordered = _ReadOnlyField[bool | None]("_locations_reordered")
    observed_locations_state = _ReadOnlyField[str]("_observed_locations_state")
    replayed_locations_state = _ReadOnlyField[str]("_replayed_locations_state")

    def __init__(
        self,
        complete: bool,
        loader_type_changed: bool | None,
        origin_changed: bool | None,
        cached_changed: bool | None,
        package_status_changed: bool | None,
        omitted_locations: tuple[str, ...],
        additional_locations: tuple[str, ...],
        locations_reordered: bool | None,
        observed_locations_state: str,
        replayed_locations_state: str,
    ) -> None:
        self._complete = complete
        self._loader_type_changed = loader_type_changed
        self._origin_changed = origin_changed
        self._cached_changed = cached_changed
        self._package_status_changed = package_status_changed
        self._omitted_locations = omitted_locations
        self._additional_locations = additional_locations
        self._locations_reordered = locations_reordered
        self._observed_locations_state = observed_locations_state
        self._replayed_locations_state = replayed_locations_state


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

    __slots__ = (
        "_claim",
        "_deep_call",
        "_evidence_level",
        "_finding_id",
        "_kind",
        "_limitations",
        "_module",
        "_replay",
        "_spec_comparison",
        "_structural_comparison",
    )
    _fields = (
        "finding_id",
        "kind",
        "module",
        "claim",
        "deep_call",
        "evidence_level",
        "limitations",
        "replay",
        "spec_comparison",
        "structural_comparison",
    )
    finding_id = _ReadOnlyField[str]("_finding_id")
    kind = _ReadOnlyField[str]("_kind")
    module = _ReadOnlyField[str]("_module")
    claim = _ReadOnlyField[FindSpecCall | None]("_claim")
    deep_call = _ReadOnlyField[DeepDiagnosticCall | None]("_deep_call")
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    limitations = _ReadOnlyField[tuple[str, ...]]("_limitations")
    replay = _ReadOnlyField[ReplayResult | None]("_replay")
    spec_comparison = _ReadOnlyField[SpecComparison | None]("_spec_comparison")
    structural_comparison = _ReadOnlyField[StructuralComparison | None]("_structural_comparison")

    def __init__(
        self,
        finding_id: str,
        kind: str,
        module: str,
        claim: FindSpecCall | None = None,
        replay: ReplayResult | None = None,
        spec_comparison: SpecComparison | None = None,
        structural_comparison: StructuralComparison | None = None,
        deep_call: DeepDiagnosticCall | None = None,
        evidence_level: str = "post_hoc",
        limitations: tuple[str, ...] = (),
    ) -> None:
        self._finding_id = finding_id
        self._kind = kind
        self._module = module
        self._claim = claim
        self._deep_call = deep_call
        self._evidence_level = evidence_level
        self._limitations = limitations
        self._replay = replay
        self._spec_comparison = spec_comparison
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
        "_attempts",
        "_baseline_module_count",
        "_current_importer_cache",
        "_current_importer_cache_non_string_keys",
        "_current_meta_path",
        "_current_path_hooks",
        "_cutoff_seq",
        "_cwd",
        "_deep_diagnostics",
        "_deep_import_outcomes_status",
        "_early_site_bootstrap",
        "_events",
        "_finder_contracts",
        "_findings",
        "_generated_at",
        "_importer_cache_coalesced",
        "_importer_cache_enabled",
        "_importer_cache_observations",
        "_initial_importer_cache",
        "_initial_importer_cache_non_string_keys",
        "_initial_meta_path",
        "_initial_path_hooks",
        "_loader_inventory",
        "_modules_since_install",
        "_monitor_enabled",
        "_path_hooks_enabled",
        "_report_errors",
        "_skipped_finders",
        "_standard_finder_status",
        "_standard_resolutions",
    )
    _fields = (
        "generated_at",
        "attempts",
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
        "loader_inventory",
        "modules_since_install",
        "events",
        "early_site_bootstrap",
        "deep_diagnostics",
        "deep_import_outcomes_status",
        "skipped_finders",
        "standard_resolutions",
        "standard_finder_status",
        "findings",
        "finder_contracts",
        "report_errors",
        "cwd",
        "argv",
    )
    generated_at = _ReadOnlyField[str]("_generated_at")
    attempts = _ReadOnlyField[tuple[ImportAttempt, ...]]("_attempts")
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
    loader_inventory = _ReadOnlyField[LoaderInventory]("_loader_inventory")
    modules_since_install = _ReadOnlyField[tuple[str, ...] | None]("_modules_since_install")
    events = _ReadOnlyField[tuple[MonitorEvent, ...]]("_events")
    early_site_bootstrap = _ReadOnlyField[EarlySiteBootstrap | None]("_early_site_bootstrap")
    deep_diagnostics = _ReadOnlyField[tuple[str, ...]]("_deep_diagnostics")
    deep_import_outcomes_status = _ReadOnlyField[str]("_deep_import_outcomes_status")
    skipped_finders = _ReadOnlyField[tuple[SkippedFinder, ...]]("_skipped_finders")
    standard_resolutions = _ReadOnlyField[tuple[StandardResolution, ...]]("_standard_resolutions")
    standard_finder_status = _ReadOnlyField[str]("_standard_finder_status")
    findings = _ReadOnlyField[tuple[Finding, ...]]("_findings")
    finder_contracts = _ReadOnlyField[tuple[FinderContract, ...]]("_finder_contracts")
    report_errors = _ReadOnlyField[tuple[ReportError, ...]]("_report_errors")
    cwd = _ReadOnlyField[str | None]("_cwd")
    argv = _ReadOnlyField[tuple[str, ...]]("_argv")

    def __init__(
        self,
        *,
        generated_at: str,
        attempts: tuple[ImportAttempt, ...],
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
        loader_inventory: LoaderInventory,
        modules_since_install: tuple[str, ...] | None,
        events: tuple[MonitorEvent, ...],
        early_site_bootstrap: EarlySiteBootstrap | None,
        deep_diagnostics: tuple[str, ...],
        deep_import_outcomes_status: str,
        skipped_finders: tuple[SkippedFinder, ...],
        standard_resolutions: tuple[StandardResolution, ...],
        standard_finder_status: str,
        findings: tuple[Finding, ...],
        finder_contracts: tuple[FinderContract, ...],
        report_errors: tuple[ReportError, ...],
        cwd: str | None,
        argv: tuple[str, ...],
    ) -> None:
        self._generated_at = generated_at
        self._attempts = attempts
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
        self._loader_inventory = loader_inventory
        self._modules_since_install = modules_since_install
        self._events = events
        self._early_site_bootstrap = early_site_bootstrap
        self._deep_diagnostics = deep_diagnostics
        self._deep_import_outcomes_status = deep_import_outcomes_status
        self._skipped_finders = skipped_finders
        self._standard_resolutions = standard_resolutions
        self._standard_finder_status = standard_finder_status
        self._findings = findings
        self._finder_contracts = finder_contracts
        self._report_errors = report_errors
        self._cwd = cwd
        self._argv = argv


def capture_document(monitor: "Monitor") -> ReportDocument:
    """Copy evidence at one sequence cutoff before deriving any findings."""
    (
        cutoff_seq,
        events,
        skipped_raw,
        finder_contracts,
        importer_cache,
        early_site_bootstrap_raw,
    ) = monitor._report_state()
    report_errors: list[ReportError] = []
    current_meta_path = _current_meta_path_names(report_errors)
    current_path_hooks = _current_path_hooks(monitor, report_errors)
    module_items = _module_items(report_errors)
    loader_inventory = _loader_inventory(module_items)
    attempts = _import_attempts(events, module_items)
    standard_resolutions = _standard_resolutions(events, attempts, loader_inventory.entries)
    modules_since_install = _modules_since_install(monitor, module_items)
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
            module_items,
            loader_inventory.entries,
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
        attempts=attempts,
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
        loader_inventory=loader_inventory,
        modules_since_install=modules_since_install,
        events=tuple(events),
        early_site_bootstrap=early_site_bootstrap,
        deep_diagnostics=monitor.deep_diagnostics,
        deep_import_outcomes_status=monitor.deep_import_outcomes_status,
        skipped_finders=skipped_finders,
        standard_resolutions=standard_resolutions,
        standard_finder_status=monitor.standard_finder_status,
        findings=findings,
        finder_contracts=tuple(finder_contracts),
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


def _module_items(report_errors: list[ReportError]) -> list[tuple[object, object]] | None:
    """Copy the module cache once for all report-time consumers."""
    try:
        return list(sys.modules.items())
    except BaseException as exc:
        report_errors.append(ReportError("snapshot.sys_modules", type_name(exc)))
        return None


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
        exact = next(
            (
                event.outcome
                for event in reversed(evidence)
                if isinstance(event, DeepImportEvent) and event.outcome != "started"
            ),
            None,
        )
        claimed = any(isinstance(event, FindSpecCall) and event.found for event in evidence)
        raised = any(isinstance(event, FindSpecCall) and event.exception_type_name is not None for event in evidence)
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
        if audit is None or finder_type_name not in audit.meta_path_type_names:
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
) -> tuple[str, str, str | None, str | None] | None:
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
    categories = {
        "SourceFileLoader": "source",
        "SourcelessFileLoader": "bytecode",
        "ExtensionFileLoader": "extension",
        "zipimporter": "zip",
    }
    category = categories.get(loader_type_name or "")
    if category is None:
        return None
    return "PathFinder", category, loader_type_name, origin


def _loader_inventory(module_items: list[tuple[object, object]] | None) -> LoaderInventory:
    """Build a safe post-hoc loader inventory from the copied cache."""
    if module_items is None:
        return LoaderInventory(False, (), 0)
    entries: list[ModuleMetadata] = []
    non_string_keys = 0
    for name, module in module_items:
        if type(name) is not str:
            non_string_keys += 1
            continue
        entries.append(inspect_module(name, module))
    return LoaderInventory(True, tuple(entries), non_string_keys)


def _modules_since_install(
    monitor: "Monitor", module_items: list[tuple[object, object]] | None
) -> tuple[str, ...] | None:
    """List module names added after monitor installation."""
    if module_items is None:
        return None
    baseline = monitor.baseline_modules
    return tuple(name for name, _module in module_items if type(name) is str and name not in baseline)


def _suspicious_findings(
    monitor: "Monitor",
    events: list[MonitorEvent],
    initial_path_hooks: tuple[ImportObjectRef, ...],
    current_path_hooks: tuple[ImportObjectRef, ...] | None,
    initial_importer_cache: tuple[ImporterCacheEntry, ...],
    current_importer_cache: tuple[ImporterCacheEntry, ...] | None,
    module_items: list[tuple[object, object]] | None,
    module_metadata: tuple[ModuleMetadata, ...],
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
    findings = _finder_side_effect_findings(events)
    findings.extend(_module_replacement_findings(events, len(findings)))
    baseline = monitor.baseline_modules
    if module_items is None:
        return tuple(findings)
    metadata_by_name = {entry.name: entry for entry in module_metadata}
    for name, module in module_items:
        if type(name) is not str or name in baseline or name == "__main__":
            continue
        winner = winners.get(name)
        if winner is not None:
            finding = _bypass_finding(name, module, winner, len(findings) + 1, structural_context, report_errors)
            if finding is not None:
                findings.append(finding)
            continue
        metadata = metadata_by_name.get(name)
        if metadata is not None and metadata.inspection == "available" and metadata.spec_is_none:
            findings.append(
                Finding(
                    f"finding:{len(findings) + 1}",
                    "no_spec",
                    name,
                    evidence_level="post_hoc",
                    limitations=("report_snapshot_cannot_identify_load_mechanism",),
                )
            )
    return tuple(findings)


def _finder_side_effect_findings(events: list[MonitorEvent]) -> list[Finding]:
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
                f"finding:{len(findings) + 1}",
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


def _bypass_finding(
    name: str,
    module: object,
    winner: FindSpecCall,
    finding_number: int,
    structural_context: _StructuralContext,
    report_errors: list[ReportError],
) -> Finding | None:
    """Compare one custom claim with a report-time PathFinder replay."""
    observed = winner.spec_summary
    if observed is None:
        return None
    observed = _post_hoc_spec_summary(module, observed)
    structural_comparison = _structural_comparison(winner.search_path, structural_context)
    replay = _replay_path_finder(name, winner.search_path)
    if replay.status == "failed":
        report_errors.append(ReportError("path_finder_replay", replay.exception_type_name or "Exception"))
        return None
    if replay.status == "not_found":
        origin = winner.origin
        if origin is not None and origin.endswith(_SOURCE_SUFFIXES) and winner.loader_type_name is not None:
            return Finding(
                f"finding:{finding_number}",
                "unfindable",
                name,
                winner,
                replay,
                structural_comparison=structural_comparison,
                evidence_level="live_replay",
                limitations=("replay_uses_report_time_importer_state",),
            )
        return None
    replayed = replay.spec_summary
    if replayed is None:
        return None
    spec_comparison = _compare_specs(observed, replayed)
    kind = _spec_finding_kind(winner, observed, replayed, spec_comparison)
    if kind is not None:
        return Finding(
            f"finding:{finding_number}",
            kind,
            name,
            winner,
            replay,
            spec_comparison,
            structural_comparison,
            evidence_level="live_replay",
            limitations=("replay_uses_report_time_importer_state",),
        )
    return None


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


def _spec_finding_kind(
    winner: FindSpecCall,
    observed: SpecSummary,
    replayed: SpecSummary,
    comparison: SpecComparison,
) -> str | None:
    """Choose the most specific user-facing label supported by the evidence."""
    if observed.is_namespace is True and replayed.is_namespace is True and comparison.omitted_locations:
        return "namespace_truncation"
    if comparison.package_status_changed:
        return "package_displacement"
    if comparison.origin_changed and type(observed.origin) is str and type(replayed.origin) is str:
        return "origin_displacement"
    source_claim = winner.origin is not None and winner.origin.endswith(_SOURCE_SUFFIXES)
    if source_claim and (comparison.loader_type_changed or comparison.origin_changed):
        return "bypass"
    if (
        comparison.loader_type_changed
        or comparison.cached_changed
        or comparison.omitted_locations
        or comparison.additional_locations
        or comparison.locations_reordered
    ):
        return "spec_difference"
    return None


def _compare_specs(observed: SpecSummary, replayed: SpecSummary) -> SpecComparison:
    """Compare safe semantic fields without inspecting either live spec."""
    loader_changed = (
        None
        if "loader:missing" in observed.unavailable_fields or "loader:missing" in replayed.unavailable_fields
        else _loader_type(observed) != _loader_type(replayed)
    )
    origin_changed = _safe_path_value_changed(observed.origin, replayed.origin)
    cached_changed = _safe_path_value_changed(observed.cached, replayed.cached)
    package_changed = (
        None
        if observed.is_package is None or replayed.is_package is None
        else observed.is_package != replayed.is_package
    )
    observed_locations = _string_locations(observed)
    replayed_locations = _string_locations(replayed)
    omitted: tuple[str, ...] = ()
    additional: tuple[str, ...] = ()
    reordered: bool | None = None
    if observed_locations is not None and replayed_locations is not None:
        observed_keys = tuple(_path_key(path) for path in observed_locations)
        replayed_keys = tuple(_path_key(path) for path in replayed_locations)
        omitted, additional = _location_delta(observed_locations, observed_keys, replayed_locations, replayed_keys)
        reordered = not omitted and not additional and observed_keys != replayed_keys
    locations_complete = not (
        observed.is_package is True and observed.locations_state in ("deferred", "failed")
    ) and not (replayed.is_package is True and replayed.locations_state in ("deferred", "failed"))
    complete = not observed.unavailable_fields and not replayed.unavailable_fields and locations_complete
    return SpecComparison(
        complete,
        loader_changed,
        origin_changed,
        cached_changed,
        package_changed,
        omitted,
        additional,
        reordered,
        observed.locations_state,
        replayed.locations_state,
    )


def _module_replacement_findings(events: list[MonitorEvent], offset: int) -> list[Finding]:
    """Return exact object replacements captured across deep loader boundaries."""
    findings: list[Finding] = []
    for event in events:
        if not isinstance(event, DeepDiagnosticCall) or not event.boundary.startswith("loader_"):
            continue
        before = event.module_state_before
        after = event.module_state_after
        if (
            before is None
            or after is None
            or before.state != "object"
            or after.state != "object"
            or before.object_id == after.object_id
            or event.fullname is None
        ):
            continue
        findings.append(
            Finding(
                f"finding:{offset + len(findings) + 1}",
                "module_replacement",
                event.fullname,
                deep_call=event,
                evidence_level="captured",
                limitations=("boundary_delta_does_not_reconstruct_intermediate_states",),
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
    observed: str | ImportObjectRef | None,
    replayed: str | ImportObjectRef | None,
) -> bool | None:
    if type(observed) is str and type(replayed) is str:
        return not _same_path(observed, replayed)
    if observed is None and replayed is None:
        return False
    if isinstance(observed, ImportObjectRef) or isinstance(replayed, ImportObjectRef):
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
        summary, _loader = summarize_spec(spec, iterate_foreign_locations=True)
        loader_type_name = None if summary.loader is None else summary.loader.type_name
        origin = summary.origin if type(summary.origin) is str else None
        return ReplayResult("found", loader_type_name, origin, summary)
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
    deep_import_events = sum(isinstance(event, DeepImportEvent) for event in document.events)
    standard_finder_calls = sum(isinstance(event, StandardFinderCall) for event in document.events)
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
                _mechanism(
                    "finder_contracts",
                    document.monitor_enabled,
                    len(document.finder_contracts),
                    "first_observation_per_identity",
                ),
                _mechanism("deep_diagnostics", bool(document.deep_diagnostics), deep_calls, "delegated_boundaries"),
                _mechanism(
                    "deep_import_outcomes",
                    "import_outcomes" in document.deep_diagnostics,
                    deep_import_events,
                    document.deep_import_outcomes_status,
                ),
                _mechanism(
                    "standard_finder_aggregate",
                    document.standard_finder_status.startswith("active_"),
                    standard_finder_calls,
                    document.standard_finder_status,
                ),
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
        "loader_inventory": _json_loader_inventory(document.loader_inventory),
        "finder_contracts": [_json_finder_contract(contract) for contract in document.finder_contracts],
        "import_attempts": [_json_import_attempt(attempt) for attempt in document.attempts],
        "standard_resolutions": [_json_standard_resolution(resolution) for resolution in document.standard_resolutions],
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


def _finder_contract_category(contract: FinderContract) -> str:
    """Summarize the two independently inspected protocol states."""
    modern = contract.find_spec.availability
    legacy = contract.find_module.availability
    if "indeterminate" in (modern, legacy):
        return "indeterminate"
    if modern == "callable":
        return "modern_and_legacy" if legacy == "callable" else "modern"
    return "legacy_only" if legacy == "callable" else "protocol_less"


def _json_finder_contract(contract: FinderContract) -> dict[str, object]:
    """Project one finder contract without consulting the live finder."""
    return {
        "category": _finder_contract_category(contract),
        "finder_id": f"0x{contract.finder_id:x}",
        "finder_type_name": contract.finder_type_name,
        "find_module": {
            "availability": contract.find_module.availability,
            "defined_by": contract.find_module.defined_by,
            "evidence": contract.find_module.evidence,
        },
        "find_spec": {
            "availability": contract.find_spec.availability,
            "defined_by": contract.find_spec.defined_by,
            "evidence": contract.find_spec.evidence,
        },
        "observation": contract.observation,
        "observation_event_ref": None if contract.observation_seq is None else f"event:{contract.observation_seq}",
        "position": contract.position,
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


def _json_module_state(state: ModuleCacheState | None) -> dict[str, object] | None:
    """Serialize target-module identity evidence."""
    if state is None:
        return None
    return {
        "object_id": None if state.object_id is None else f"0x{state.object_id:x}",
        "state": state.state,
        "type_name": state.type_name,
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
                "attempt_id": event.attempt_id,
                "meta_path": {
                    "entries": list(event.meta_path_type_names),
                    "object_id": f"0x{event.meta_path_id:x}",
                },
                "path_hooks_id": None if event.path_hooks_id is None else f"0x{event.path_hooks_id:x}",
                "thread_name": event.thread_name,
                "thread_id": event.thread_id,
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
                "module_state_after": _json_module_state(event.module_state_after),
                "module_state_before": _json_module_state(event.module_state_before),
                "object_id": f"0x{event.object_id:x}",
                "object_type_name": event.object_type_name,
                "outcome": event.outcome,
                "path": event.path,
                "thread_name": event.thread_name,
                "thread_id": event.thread_id,
            }
        )
    elif isinstance(event, DeepImportEvent):
        result.update(
            {
                "attempt_id": event.attempt_id,
                "evidence": "exact_import_boundary",
                "fullname": event.fullname,
                "kind": "deep_import_event",
                "outcome": event.outcome,
                "thread_id": event.thread_id,
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, StandardFinderCall):
        result.update(
            {
                "attempt_id": event.attempt_id,
                "evidence": "captured_standard_finder_boundary",
                "finder_type_name": event.finder_type_name,
                "fullname": event.fullname,
                "kind": "standard_finder_call",
                "spec": _json_spec_summary(event.spec_summary),
                "thread_id": event.thread_id,
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
                "module_state_after": _json_module_state(event.module_state_after),
                "module_state_before": _json_module_state(event.module_state_before),
                "origin": event.origin,
                "search_path": list(event.search_path),
                "search_path_kind": event.search_path_kind,
                "spec": None if event.spec_summary is None else _json_spec_summary(event.spec_summary),
                "thread_name": event.thread_name,
                "thread_id": event.thread_id,
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


def _json_import_attempt(attempt: ImportAttempt) -> dict[str, object]:
    """Serialize one derived attempt while retaining links to raw evidence."""
    return {
        "id": f"attempt:{attempt.attempt_id}",
        "fullname": attempt.fullname,
        "start_event_ref": f"event:{attempt.start_event_seq}",
        "evidence_event_refs": [f"event:{seq}" for seq in attempt.event_seqs],
        "thread_id": attempt.thread_id,
        "thread_name": attempt.thread_name,
        "progress": attempt.progress,
        "presence": attempt.presence,
    }


def _json_standard_resolution(resolution: StandardResolution) -> dict[str, object]:
    """Serialize provenance without presenting inference as a raw event."""
    return {
        "attempt_ref": f"attempt:{resolution.attempt_id}",
        "category": resolution.category,
        "component_event_refs": [f"event:{seq}" for seq in resolution.component_event_seqs],
        "evidence_level": resolution.evidence_level,
        "event_ref": None if resolution.event_seq is None else f"event:{resolution.event_seq}",
        "finder_type_name": resolution.finder_type_name,
        "fullname": resolution.fullname,
        "later_finders": list(resolution.later_finders),
        "loader_type_name": resolution.loader_type_name,
        "origin": resolution.origin,
        "state_phase": resolution.state_phase,
    }


def _json_import_object(reference: ImportObjectRef) -> dict[str, object]:
    """Serialize safe import-object identity metadata."""
    return {
        "name": reference.name,
        "object_id": f"0x{reference.object_id:x}",
        "type_name": reference.type_name,
    }


def _json_spec_value(value: str | ImportObjectRef | None) -> object:
    if isinstance(value, ImportObjectRef):
        return {"kind": "object", **_json_import_object(value)}
    return value


def _json_spec_summary(summary: SpecSummary) -> dict[str, object]:
    locations = summary.submodule_search_locations
    return {
        "cached": _json_spec_value(summary.cached),
        "is_namespace": summary.is_namespace,
        "is_package": summary.is_package,
        "loader": None if summary.loader is None else _json_import_object(summary.loader),
        "locations_state": summary.locations_state,
        "origin": _json_spec_value(summary.origin),
        "spec": _json_import_object(summary.spec),
        "submodule_search_locations": None
        if locations is None
        else [_json_spec_value(location) for location in locations],
        "unavailable_fields": list(summary.unavailable_fields),
    }


def _loader_inventory_groups(
    inventory: LoaderInventory,
) -> tuple[tuple[ImportObjectRef | None, tuple[ModuleMetadata, ...]], ...]:
    """Group available module records by effective loader identity."""
    groups: dict[int | None, tuple[ImportObjectRef | None, list[ModuleMetadata]]] = {}
    for entry in inventory.entries:
        if entry.inspection != "available":
            continue
        loader = entry.loader
        key = None if loader is None else loader.object_id
        group = groups.get(key)
        if group is None:
            group = (loader, [])
            groups[key] = group
        group[1].append(entry)
    ordered = sorted(
        groups.values(),
        key=lambda group: (
            group[0] is None,
            "" if group[0] is None else group[0].type_name,
            -1 if group[0] is None else group[0].object_id,
        ),
    )
    return tuple((loader, tuple(sorted(entries, key=lambda entry: entry.name))) for loader, entries in ordered)


def _json_module_metadata(entry: ModuleMetadata) -> dict[str, object]:
    """Serialize one safely reduced module-cache entry."""
    return {
        "inspection": entry.inspection,
        "loader_agreement": entry.loader_agreement,
        "loader_source": entry.loader_source,
        "module": _json_import_object(entry.module),
        "module_loader": None if entry.module_loader is None else _json_import_object(entry.module_loader),
        "module_loader_available": entry.module_loader_available,
        "name": entry.name,
        "reason": entry.reason,
        "spec": None if entry.spec_summary is None else _json_spec_summary(entry.spec_summary),
        "spec_present": entry.spec_present,
    }


def _json_loader_inventory(inventory: LoaderInventory) -> dict[str, object]:
    """Serialize the exhaustive post-hoc module grouping."""
    unavailable = [entry for entry in inventory.entries if entry.inspection != "available"]
    return {
        "available": inventory.available,
        "evidence": "post_hoc",
        "groups": [
            {
                "loader": None if loader is None else _json_import_object(loader),
                "modules": [_json_module_metadata(entry) for entry in entries],
            }
            for loader, entries in _loader_inventory_groups(inventory)
        ],
        "non_string_keys_omitted": inventory.non_string_keys,
        "phase": "report",
        "unavailable": [_json_module_metadata(entry) for entry in sorted(unavailable, key=lambda item: item.name)],
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
    event_refs: list[str] = []
    if finding.claim is not None:
        event_refs.append(f"event:{finding.claim.seq}")
    if finding.deep_call is not None:
        event_refs.append(f"event:{finding.deep_call.seq}")
    evidence: dict[str, object] = {
        "event_refs": event_refs,
        "level": finding.evidence_level,
        "limitations": list(finding.limitations),
    }
    result: dict[str, object] = {
        "evidence": evidence,
        "id": finding.finding_id,
        "kind": finding.kind,
        "module": finding.module,
    }
    if finding.claim is not None:
        result["claim"] = {
            "event_ref": f"event:{finding.claim.seq}",
            "finder_id": f"0x{finding.claim.finder_id:x}",
            "finder_type_name": finding.claim.finder_type_name,
            "loader_type_name": finding.claim.loader_type_name,
            "origin": finding.claim.origin,
            "search_path": list(finding.claim.search_path),
            "search_path_kind": finding.claim.search_path_kind,
            "spec": None if finding.claim.spec_summary is None else _json_spec_summary(finding.claim.spec_summary),
        }
        if finding.kind == "finder_side_effect":
            evidence["module_state_after"] = _json_module_state(finding.claim.module_state_after)
            evidence["module_state_before"] = _json_module_state(finding.claim.module_state_before)
            evidence["outcome"] = (
                f"raised:{finding.claim.exception_type_name}"
                if finding.claim.exception_type_name is not None
                else "passed"
            )
    if finding.deep_call is not None:
        result["deep_call"] = {
            "boundary": finding.deep_call.boundary,
            "event_ref": f"event:{finding.deep_call.seq}",
            "module_state_after": _json_module_state(finding.deep_call.module_state_after),
            "module_state_before": _json_module_state(finding.deep_call.module_state_before),
            "outcome": finding.deep_call.outcome,
        }
    if finding.replay is not None:
        result["path_finder_replay"] = {
            "evidence_level": finding.replay.evidence_level,
            "exception_type_name": finding.replay.exception_type_name,
            "loader_type_name": finding.replay.loader_type_name,
            "origin": finding.replay.origin,
            "spec": None if finding.replay.spec_summary is None else _json_spec_summary(finding.replay.spec_summary),
            "state_phase": finding.replay.state_phase,
            "status": finding.replay.status,
        }
    if finding.spec_comparison is not None:
        comparison = finding.spec_comparison
        result["spec_comparison"] = {
            "additional_locations": list(comparison.additional_locations),
            "cached_changed": comparison.cached_changed,
            "complete": comparison.complete,
            "evidence_level": "captured_vs_live_replay",
            "loader_type_changed": comparison.loader_type_changed,
            "locations_reordered": comparison.locations_reordered,
            "omitted_locations": list(comparison.omitted_locations),
            "observed_locations_state": comparison.observed_locations_state,
            "origin_changed": comparison.origin_changed,
            "package_status_changed": comparison.package_status_changed,
            "replayed_locations_state": comparison.replayed_locations_state,
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
        evidence["finder_claim"] = "not_recorded"
        evidence["module_spec"] = "missing"
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
        "loader_inventory": {
            "available": False,
            "evidence": "post_hoc",
            "groups": [],
            "non_string_keys_omitted": 0,
            "phase": "report",
            "unavailable": [],
        },
        "finder_contracts": [],
        "import_attempts": [],
        "timeline": [],
        "findings": [],
        "diagnostics": {
            "internal_error_refs": [],
            "report_errors": [{"exception_type_name": error_name, "where": "report_generation"}],
            "skipped_finders": [],
        },
    }
