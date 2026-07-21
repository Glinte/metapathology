"""Immutable state contracts shared by monitoring and reporting."""

from metapathology._record import _Record
from metapathology._records import FinderContract, ImporterCacheEntry, MonitorEvent, ObjectRef

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    TargetOutcomeKind = Literal["completed", "raised"]
    StandardFinderStatus = Literal[
        "disabled",
        "unavailable_existing_profiler",
        "active_path_finder_aggregate",
        "unsupported_path_finder_boundary",
        "inactive_after_uninstall",
    ]
    DeepImportOutcomesStatus = Literal[
        "disabled",
        "refused_existing_profiler",
        "unsupported_boundary",
        "active_current_and_future_threading_threads_cache_hits_not_observed",
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


class _TargetOutcomeState(_Record):
    """Plain reduction of how the monitored target finished."""

    kind: "TargetOutcomeKind"
    exception_type_name: str | None
    missing_module: str | None
    exit_code: int | None


class MonitorSnapshot(_Record):
    """Immutable monitor-owned state copied at one event-sequence cutoff."""

    cutoff_seq: int
    events: tuple[MonitorEvent, ...]
    skipped_finders: tuple[tuple[object, str], ...]
    finder_contracts: tuple[FinderContract, ...]
    importer_cache: _ImporterCacheReportState
    early_site_bootstrap: _EarlySiteBootstrapState | None
    frozen_bootstrap: _FrozenBootstrapState | None
    enabled: bool
    baseline_modules: frozenset[str]
    initial_meta_path: tuple[str, ...]
    path_hooks_enabled: bool
    initial_path_hooks: tuple[ObjectRef, ...]
    sys_path_enabled: bool
    deep_diagnostics: tuple[str, ...]
    deep_import_outcomes_status: "DeepImportOutcomesStatus"
    standard_finder_status: "StandardFinderStatus"
    target_outcome: _TargetOutcomeState | None
