"""Immutable state contracts shared by monitoring and reporting."""

from metapathology._record import _Record
from metapathology._records import FinderAPIObservation, ImporterCacheEntry, MonitorEvent, ObjectIdentity

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    ProgramOutcomeKind = Literal["completed", "raised"]
    PathFinderCaptureStatus = Literal[
        "disabled",
        "unavailable_existing_profiler",
        "active_path_finder_aggregate",
        "unsupported_path_finder_boundary",
        "inactive_after_uninstall",
    ]
    ImportResultsCaptureStatus = Literal[
        "disabled",
        "refused_existing_profiler",
        "unsupported_boundary",
        "active_current_and_future_threading_threads_cache_hits_not_observed",
        "inactive_after_uninstall",
    ]
    ImportCallsCaptureStatus = Literal[
        "disabled",
        "active_all_threads_including_cache_hits",
        "inactive_after_uninstall",
    ]


_MISSING = object()


class _OwnedValue(_Record):
    """A process-global value replaced by one monitor installation."""

    previous: object
    installed: object


class _OwnedAttribute(_Record):
    """An instance-dict shadow owned by one monitor installation."""

    target: object
    name: str
    previous: object
    installed: object


class _ImporterCacheReportState(_Record):
    """Cache evidence copied with the monitor's report cutoff."""

    enabled: bool
    initial_entries: tuple[ImporterCacheEntry, ...]
    initial_non_string_keys: int
    latest_entries: tuple[ImporterCacheEntry, ...] | None
    latest_non_string_keys: int | None
    observations: int
    coalesced: int


class _EarlySiteBootstrapState(_Record):
    """Provenance for an active early site bootstrap."""

    path: str
    site_packages: str
    activation_source: str
    earlier_pth_files: tuple[str, ...]


class _FrozenBootstrapState(_Record):
    """Provenance for activation inside a frozen application."""

    integration: str
    path: str
    boundary: str


class _ProgramOutcomeState(_Record):
    """Plain reduction of how the monitored target finished."""

    kind: "ProgramOutcomeKind"
    exception_type_name: str | None
    missing_module: str | None
    exit_code: int | None


class MonitorSnapshot(_Record):
    """Immutable monitor-owned state copied at one event-sequence cutoff."""

    cutoff_seq: int
    events: tuple[MonitorEvent, ...]
    skipped_finders: tuple[tuple[object, str], ...]
    finder_apis: tuple[FinderAPIObservation, ...]
    importer_cache: _ImporterCacheReportState
    retained_cache_finders: tuple[tuple[int, object], ...]
    early_site_bootstrap: _EarlySiteBootstrapState | None
    frozen_bootstrap: _FrozenBootstrapState | None
    enabled: bool
    baseline_modules: frozenset[str]
    initial_meta_path: tuple[str, ...]
    import_audit_enabled: bool
    meta_path_enabled: bool
    finder_attribution_enabled: bool
    path_hooks_enabled: bool
    initial_path_hooks: tuple[ObjectIdentity, ...]
    sys_path_enabled: bool
    detailed_capture: tuple[str, ...]
    import_results_capture_status: "ImportResultsCaptureStatus"
    import_calls_capture_status: "ImportCallsCaptureStatus"
    path_finder_capture_status: "PathFinderCaptureStatus"
    program_outcome: _ProgramOutcomeState | None
