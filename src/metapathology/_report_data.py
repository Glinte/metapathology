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
    SysPathMutation,
    SysPathReassignment,
    _ReadOnlyField,
    _Record,
    type_name,
)
from metapathology._report_schema import (
    EarlySiteBootstrapJSON,
    EventJSON,
    ExplanationJSON,
    FinderContractJSON,
    FindingEvidenceJSON,
    FindingJSON,
    FrameJSON,
    FrozenBootstrapJSON,
    ImportAttemptJSON,
    ImporterCacheEntryJSON,
    ImporterCacheReplacementJSON,
    ImportObjectJSON,
    ImportObjectValueJSON,
    LoaderGroupJSON,
    LoaderInventoryInfo,
    MechanismJSON,
    ModuleMetadataJSON,
    ModuleStateJSON,
    ReportJSON,
    ReportStatus,
    ResolutionRouteJSON,
    RouteComparisonJSON,
    SpecSummaryJSON,
    SpecValueJSON,
    StandardResolutionJSON,
    SummaryInfo,
    TargetOutcomeJSON,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from traceback import FrameSummary

    from metapathology._monitor import Monitor


_MODULE_FILE_SUFFIXES = (".py", ".pyc", ".pyd", ".so")
_STANDARD_CLASS_FINDER_REASON_PREFIX = "standard CPython class finder;"
_SCHEMA_NAME = "metapathology.report"
_SCHEMA_MAJOR = 1
_SCHEMA_MINOR = 0


class ReportError(_Record):
    """One failure while copying or analysing report-time state."""

    __slots__ = ("_exception_type_name", "_where")
    _fields = ("where", "exception_type_name")
    where = _ReadOnlyField[str]("_where")
    exception_type_name = _ReadOnlyField[str]("_exception_type_name")

    def __init__(self, where: str, exception_type_name: str) -> None:
        self._where = where
        self._exception_type_name = exception_type_name


class TargetOutcome(_Record):
    """Plain reduction of how the monitored target finished."""

    __slots__ = ("_exception_type_name", "_exit_code", "_kind", "_missing_module")
    _fields = ("kind", "exception_type_name", "missing_module", "exit_code")
    kind = _ReadOnlyField[str]("_kind")
    exception_type_name = _ReadOnlyField[str | None]("_exception_type_name")
    missing_module = _ReadOnlyField[str | None]("_missing_module")
    exit_code = _ReadOnlyField[int | None]("_exit_code")

    def __init__(
        self,
        kind: str,
        exception_type_name: str | None,
        missing_module: str | None,
        exit_code: int | None,
    ) -> None:
        self._kind = kind
        self._exception_type_name = exception_type_name
        self._missing_module = missing_module
        self._exit_code = exit_code


class ReportSummary(_Record):
    """Counts and top references shared by the text verdict and JSON summary.

    Holds only identifiers and counts; the headline prose itself is composed
    by the text renderer so no human sentences enter the JSON contract.
    """

    __slots__ = (
        "_actionable",
        "_informational",
        "_top_explanation_id",
        "_top_finding_id",
        "_unresolved_import_count",
        "_warning",
    )
    _fields = (
        "actionable",
        "warning",
        "informational",
        "unresolved_import_count",
        "top_finding_id",
        "top_explanation_id",
    )
    actionable = _ReadOnlyField[int]("_actionable")
    warning = _ReadOnlyField[int]("_warning")
    informational = _ReadOnlyField[int]("_informational")
    unresolved_import_count = _ReadOnlyField[int]("_unresolved_import_count")
    top_finding_id = _ReadOnlyField[str | None]("_top_finding_id")
    top_explanation_id = _ReadOnlyField[str | None]("_top_explanation_id")

    def __init__(
        self,
        *,
        actionable: int,
        warning: int,
        informational: int,
        unresolved_import_count: int,
        top_finding_id: str | None,
        top_explanation_id: str | None,
    ) -> None:
        self._actionable = actionable
        self._warning = warning
        self._informational = informational
        self._unresolved_import_count = unresolved_import_count
        self._top_finding_id = top_finding_id
        self._top_explanation_id = top_explanation_id


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


class ResolutionRoute(_Record):
    """One captured or probed route to resolving a module name."""

    __slots__ = (
        "_event_seq",
        "_evidence_level",
        "_exception_type_name",
        "_finder_id",
        "_finder_type_name",
        "_kind",
        "_limitations",
        "_module",
        "_predicts_alternative_winner",
        "_purpose",
        "_route_id",
        "_search_path",
        "_search_path_kind",
        "_search_path_phase",
        "_signals",
        "_spec_summary",
        "_state_phase",
        "_status",
    )
    _fields = (
        "route_id",
        "module",
        "kind",
        "purpose",
        "limitations",
        "evidence_level",
        "state_phase",
        "predicts_alternative_winner",
        "finder_type_name",
        "finder_id",
        "status",
        "spec_summary",
        "exception_type_name",
        "event_seq",
        "search_path",
        "search_path_kind",
        "search_path_phase",
        "signals",
    )
    route_id = _ReadOnlyField[str]("_route_id")
    module = _ReadOnlyField[str]("_module")
    kind = _ReadOnlyField[str]("_kind")
    purpose = _ReadOnlyField[str]("_purpose")
    limitations = _ReadOnlyField[tuple[str, ...]]("_limitations")
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    state_phase = _ReadOnlyField[str]("_state_phase")
    predicts_alternative_winner = _ReadOnlyField[bool]("_predicts_alternative_winner")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    finder_id = _ReadOnlyField[int | None]("_finder_id")
    status = _ReadOnlyField[str]("_status")
    spec_summary = _ReadOnlyField[SpecSummary | None]("_spec_summary")
    exception_type_name = _ReadOnlyField[str | None]("_exception_type_name")
    event_seq = _ReadOnlyField[int | None]("_event_seq")
    search_path = _ReadOnlyField[tuple[str, ...]]("_search_path")
    search_path_kind = _ReadOnlyField[str]("_search_path_kind")
    search_path_phase = _ReadOnlyField[str]("_search_path_phase")
    signals = _ReadOnlyField[tuple[str, ...]]("_signals")

    def __init__(
        self,
        *,
        route_id: str,
        module: str,
        kind: str,
        purpose: str,
        limitations: tuple[str, ...],
        evidence_level: str,
        state_phase: str,
        predicts_alternative_winner: bool,
        finder_type_name: str,
        finder_id: int | None,
        status: str,
        spec_summary: SpecSummary | None,
        exception_type_name: str | None,
        event_seq: int | None,
        search_path: tuple[str, ...],
        search_path_kind: str,
        search_path_phase: str,
        signals: tuple[str, ...] = (),
    ) -> None:
        self._route_id = route_id
        self._module = module
        self._kind = kind
        self._purpose = purpose
        self._limitations = limitations
        self._evidence_level = evidence_level
        self._state_phase = state_phase
        self._predicts_alternative_winner = predicts_alternative_winner
        self._finder_type_name = finder_type_name
        self._finder_id = finder_id
        self._status = status
        self._spec_summary = spec_summary
        self._exception_type_name = exception_type_name
        self._event_seq = event_seq
        self._search_path = search_path
        self._search_path_kind = search_path_kind
        self._search_path_phase = search_path_phase
        self._signals = signals


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


class RouteComparison(_Record):
    """Neutral semantic differences between two resolution routes."""

    __slots__ = (
        "_cached_differs",
        "_comparison_id",
        "_complete",
        "_left_locations_state",
        "_left_route_id",
        "_loader_type_differs",
        "_locations_reordered",
        "_only_in_left_route",
        "_only_in_right_route",
        "_origin_differs",
        "_package_status_differs",
        "_right_locations_state",
        "_right_route_id",
        "_status_differs",
        "_structural_comparison",
    )
    _fields = (
        "comparison_id",
        "left_route_id",
        "right_route_id",
        "complete",
        "status_differs",
        "loader_type_differs",
        "origin_differs",
        "cached_differs",
        "package_status_differs",
        "only_in_left_route",
        "only_in_right_route",
        "locations_reordered",
        "left_locations_state",
        "right_locations_state",
        "structural_comparison",
    )
    comparison_id = _ReadOnlyField[str]("_comparison_id")
    left_route_id = _ReadOnlyField[str]("_left_route_id")
    right_route_id = _ReadOnlyField[str]("_right_route_id")
    complete = _ReadOnlyField[bool]("_complete")
    status_differs = _ReadOnlyField[bool]("_status_differs")
    loader_type_differs = _ReadOnlyField[bool | None]("_loader_type_differs")
    origin_differs = _ReadOnlyField[bool | None]("_origin_differs")
    cached_differs = _ReadOnlyField[bool | None]("_cached_differs")
    package_status_differs = _ReadOnlyField[bool | None]("_package_status_differs")
    only_in_left_route = _ReadOnlyField[tuple[str, ...]]("_only_in_left_route")
    only_in_right_route = _ReadOnlyField[tuple[str, ...]]("_only_in_right_route")
    locations_reordered = _ReadOnlyField[bool | None]("_locations_reordered")
    left_locations_state = _ReadOnlyField[str]("_left_locations_state")
    right_locations_state = _ReadOnlyField[str]("_right_locations_state")
    structural_comparison = _ReadOnlyField[StructuralComparison]("_structural_comparison")

    def __init__(
        self,
        comparison_id: str,
        left_route_id: str,
        right_route_id: str,
        status_differs: bool,
        complete: bool,
        loader_type_differs: bool | None,
        origin_differs: bool | None,
        cached_differs: bool | None,
        package_status_differs: bool | None,
        only_in_left_route: tuple[str, ...],
        only_in_right_route: tuple[str, ...],
        locations_reordered: bool | None,
        left_locations_state: str,
        right_locations_state: str,
        structural_comparison: StructuralComparison,
    ) -> None:
        self._comparison_id = comparison_id
        self._left_route_id = left_route_id
        self._right_route_id = right_route_id
        self._status_differs = status_differs
        self._complete = complete
        self._loader_type_differs = loader_type_differs
        self._origin_differs = origin_differs
        self._cached_differs = cached_differs
        self._package_status_differs = package_status_differs
        self._only_in_left_route = only_in_left_route
        self._only_in_right_route = only_in_right_route
        self._locations_reordered = locations_reordered
        self._left_locations_state = left_locations_state
        self._right_locations_state = right_locations_state
        self._structural_comparison = structural_comparison


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
        "_finder_contract",
        "_finding_id",
        "_kind",
        "_limitations",
        "_module",
        "_module_state_baseline",
        "_route_comparison_id",
        "_route_ids",
        "_severity",
        "_signals",
        "_structural_comparison",
        "_subject_kind",
        "_supporting_event_seqs",
    )
    _fields = (
        "finding_id",
        "kind",
        "module",
        "claim",
        "deep_call",
        "evidence_level",
        "finder_contract",
        "limitations",
        "module_state_baseline",
        "severity",
        "signals",
        "subject_kind",
        "supporting_event_seqs",
        "route_ids",
        "route_comparison_id",
        "structural_comparison",
    )
    finding_id = _ReadOnlyField[str]("_finding_id")
    kind = _ReadOnlyField[str]("_kind")
    module = _ReadOnlyField[str]("_module")
    claim = _ReadOnlyField[FindSpecCall | None]("_claim")
    deep_call = _ReadOnlyField[DeepDiagnosticCall | None]("_deep_call")
    evidence_level = _ReadOnlyField[str]("_evidence_level")
    finder_contract = _ReadOnlyField[FinderContract | None]("_finder_contract")
    limitations = _ReadOnlyField[tuple[str, ...]]("_limitations")
    module_state_baseline = _ReadOnlyField[ModuleCacheState | None]("_module_state_baseline")
    severity = _ReadOnlyField[str]("_severity")
    signals = _ReadOnlyField[tuple[str, ...]]("_signals")
    subject_kind = _ReadOnlyField[str]("_subject_kind")
    supporting_event_seqs = _ReadOnlyField[tuple[int, ...]]("_supporting_event_seqs")
    route_ids = _ReadOnlyField[tuple[str, ...]]("_route_ids")
    route_comparison_id = _ReadOnlyField[str | None]("_route_comparison_id")
    structural_comparison = _ReadOnlyField[StructuralComparison | None]("_structural_comparison")

    def __init__(
        self,
        finding_id: str,
        kind: str,
        module: str,
        claim: FindSpecCall | None = None,
        route_ids: tuple[str, ...] = (),
        route_comparison_id: str | None = None,
        structural_comparison: StructuralComparison | None = None,
        deep_call: DeepDiagnosticCall | None = None,
        evidence_level: str = "post_hoc",
        limitations: tuple[str, ...] = (),
        subject_kind: str = "module",
        finder_contract: FinderContract | None = None,
        supporting_event_seqs: tuple[int, ...] = (),
        severity: str = "warning",
        signals: tuple[str, ...] = (),
        module_state_baseline: ModuleCacheState | None = None,
    ) -> None:
        self._finding_id = finding_id
        self._kind = kind
        self._module = module
        self._claim = claim
        self._deep_call = deep_call
        self._evidence_level = evidence_level
        self._finder_contract = finder_contract
        self._limitations = limitations
        self._module_state_baseline = module_state_baseline
        self._severity = severity
        self._signals = signals
        self._subject_kind = subject_kind
        self._supporting_event_seqs = supporting_event_seqs
        self._route_ids = route_ids
        self._route_comparison_id = route_comparison_id
        self._structural_comparison = structural_comparison


class CausalExplanation(_Record):
    """Deterministic synthesis joining a primary finding to its likely effect."""

    __slots__ = (
        "_alternatives",
        "_boundary",
        "_candidate_path",
        "_cause_finding_id",
        "_confidence",
        "_effect_status",
        "_event_seqs",
        "_explanation_id",
        "_finder_type_name",
        "_kind",
        "_later_finders",
        "_next_observation",
        "_omitted_location",
        "_origin",
        "_standard_attempt_id",
        "_state_after",
        "_state_before",
        "_subject",
    )
    _fields = (
        "alternatives",
        "boundary",
        "explanation_id",
        "kind",
        "confidence",
        "subject",
        "effect_status",
        "cause_finding_id",
        "finder_type_name",
        "omitted_location",
        "candidate_path",
        "later_finders",
        "origin",
        "standard_attempt_id",
        "state_before",
        "state_after",
        "event_seqs",
        "next_observation",
    )
    alternatives = _ReadOnlyField[tuple[str, ...]]("_alternatives")
    boundary = _ReadOnlyField[str | None]("_boundary")
    explanation_id = _ReadOnlyField[str]("_explanation_id")
    kind = _ReadOnlyField[str]("_kind")
    confidence = _ReadOnlyField[str]("_confidence")
    subject = _ReadOnlyField[str]("_subject")
    effect_status = _ReadOnlyField[str]("_effect_status")
    cause_finding_id = _ReadOnlyField[str | None]("_cause_finding_id")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    omitted_location = _ReadOnlyField[str]("_omitted_location")
    candidate_path = _ReadOnlyField[str]("_candidate_path")
    later_finders = _ReadOnlyField[tuple[str, ...]]("_later_finders")
    origin = _ReadOnlyField[str | None]("_origin")
    standard_attempt_id = _ReadOnlyField[int | None]("_standard_attempt_id")
    state_before = _ReadOnlyField[ModuleCacheState | None]("_state_before")
    state_after = _ReadOnlyField[ModuleCacheState | None]("_state_after")
    event_seqs = _ReadOnlyField[tuple[int, ...]]("_event_seqs")
    next_observation = _ReadOnlyField[str | None]("_next_observation")

    def __init__(
        self,
        *,
        explanation_id: str,
        kind: str,
        confidence: str,
        subject: str,
        effect_status: str,
        cause_finding_id: str | None,
        finder_type_name: str,
        omitted_location: str,
        candidate_path: str,
        event_seqs: tuple[int, ...],
        next_observation: str | None,
        origin: str | None = None,
        standard_attempt_id: int | None = None,
        later_finders: tuple[str, ...] = (),
        boundary: str | None = None,
        state_before: ModuleCacheState | None = None,
        state_after: ModuleCacheState | None = None,
        alternatives: tuple[str, ...] = (),
    ) -> None:
        self._alternatives = alternatives
        self._boundary = boundary
        self._explanation_id = explanation_id
        self._kind = kind
        self._later_finders = later_finders
        self._confidence = confidence
        self._subject = subject
        self._effect_status = effect_status
        self._cause_finding_id = cause_finding_id
        self._finder_type_name = finder_type_name
        self._omitted_location = omitted_location
        self._origin = origin
        self._standard_attempt_id = standard_attempt_id
        self._state_before = state_before
        self._state_after = state_after
        self._candidate_path = candidate_path
        self._event_seqs = event_seqs
        self._next_observation = next_observation


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


class FrozenBootstrap(_Record):
    """Report-safe provenance for activation inside a frozen application."""

    __slots__ = ("_boundary", "_integration", "_path")
    _fields = ("integration", "path", "boundary")
    integration = _ReadOnlyField[str]("_integration")
    path = _ReadOnlyField[str]("_path")
    boundary = _ReadOnlyField[str]("_boundary")

    def __init__(self, integration: str, path: str, boundary: str) -> None:
        self._integration = integration
        self._path = path
        self._boundary = boundary


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
        "_explanations",
        "_finder_contracts",
        "_findings",
        "_frozen_bootstrap",
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
        "_resolution_routes",
        "_route_comparisons",
        "_skipped_finders",
        "_standard_finder_status",
        "_standard_resolutions",
        "_summary",
        "_sys_path_enabled",
        "_target_outcome",
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
        "explanations",
        "early_site_bootstrap",
        "frozen_bootstrap",
        "deep_diagnostics",
        "deep_import_outcomes_status",
        "skipped_finders",
        "standard_resolutions",
        "standard_finder_status",
        "findings",
        "resolution_routes",
        "route_comparisons",
        "finder_contracts",
        "report_errors",
        "summary",
        "sys_path_enabled",
        "target_outcome",
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
    explanations = _ReadOnlyField[tuple[CausalExplanation, ...]]("_explanations")
    early_site_bootstrap = _ReadOnlyField[EarlySiteBootstrap | None]("_early_site_bootstrap")
    frozen_bootstrap = _ReadOnlyField[FrozenBootstrap | None]("_frozen_bootstrap")
    deep_diagnostics = _ReadOnlyField[tuple[str, ...]]("_deep_diagnostics")
    deep_import_outcomes_status = _ReadOnlyField[str]("_deep_import_outcomes_status")
    skipped_finders = _ReadOnlyField[tuple[SkippedFinder, ...]]("_skipped_finders")
    standard_resolutions = _ReadOnlyField[tuple[StandardResolution, ...]]("_standard_resolutions")
    standard_finder_status = _ReadOnlyField[str]("_standard_finder_status")
    findings = _ReadOnlyField[tuple[Finding, ...]]("_findings")
    resolution_routes = _ReadOnlyField[tuple[ResolutionRoute, ...]]("_resolution_routes")
    route_comparisons = _ReadOnlyField[tuple[RouteComparison, ...]]("_route_comparisons")
    finder_contracts = _ReadOnlyField[tuple[FinderContract, ...]]("_finder_contracts")
    report_errors = _ReadOnlyField[tuple[ReportError, ...]]("_report_errors")
    summary = _ReadOnlyField[ReportSummary]("_summary")
    sys_path_enabled = _ReadOnlyField[bool]("_sys_path_enabled")
    target_outcome = _ReadOnlyField[TargetOutcome | None]("_target_outcome")
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
        explanations: tuple[CausalExplanation, ...],
        early_site_bootstrap: EarlySiteBootstrap | None,
        frozen_bootstrap: FrozenBootstrap | None,
        deep_diagnostics: tuple[str, ...],
        deep_import_outcomes_status: str,
        skipped_finders: tuple[SkippedFinder, ...],
        standard_resolutions: tuple[StandardResolution, ...],
        standard_finder_status: str,
        findings: tuple[Finding, ...],
        resolution_routes: tuple[ResolutionRoute, ...],
        route_comparisons: tuple[RouteComparison, ...],
        finder_contracts: tuple[FinderContract, ...],
        report_errors: tuple[ReportError, ...],
        summary: ReportSummary,
        sys_path_enabled: bool,
        target_outcome: TargetOutcome | None,
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
        self._explanations = explanations
        self._early_site_bootstrap = early_site_bootstrap
        self._frozen_bootstrap = frozen_bootstrap
        self._deep_diagnostics = deep_diagnostics
        self._deep_import_outcomes_status = deep_import_outcomes_status
        self._skipped_finders = skipped_finders
        self._standard_resolutions = standard_resolutions
        self._standard_finder_status = standard_finder_status
        self._findings = findings
        self._resolution_routes = resolution_routes
        self._route_comparisons = route_comparisons
        self._finder_contracts = finder_contracts
        self._report_errors = report_errors
        self._summary = summary
        self._sys_path_enabled = sys_path_enabled
        self._target_outcome = target_outcome
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
        frozen_bootstrap_raw,
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
        findings, resolution_routes, route_comparisons = _suspicious_findings(
            monitor,
            events,
            monitor.initial_path_hooks,
            current_path_hooks,
            importer_cache.initial_entries,
            importer_cache.latest_entries,
            module_items,
            loader_inventory.entries,
            finder_contracts,
            attempts,
            report_errors,
        )
    finally:
        monitor._end_report_analysis(previously_active)
    explanations = _causal_explanations(findings, attempts, standard_resolutions, route_comparisons, events)
    outcome_state = monitor.target_outcome
    target_outcome = (
        None
        if outcome_state is None
        else TargetOutcome(
            outcome_state.kind,
            outcome_state.exception_type_name,
            outcome_state.missing_module,
            outcome_state.exit_code,
        )
    )
    summary = _report_summary(findings, explanations, attempts)
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
    frozen_bootstrap = (
        None
        if frozen_bootstrap_raw is None
        else FrozenBootstrap(
            frozen_bootstrap_raw.integration,
            frozen_bootstrap_raw.path,
            frozen_bootstrap_raw.boundary,
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
        explanations=explanations,
        early_site_bootstrap=early_site_bootstrap,
        frozen_bootstrap=frozen_bootstrap,
        deep_diagnostics=monitor.deep_diagnostics,
        deep_import_outcomes_status=monitor.deep_import_outcomes_status,
        skipped_finders=skipped_finders,
        standard_resolutions=standard_resolutions,
        standard_finder_status=monitor.standard_finder_status,
        findings=findings,
        resolution_routes=resolution_routes,
        route_comparisons=route_comparisons,
        finder_contracts=tuple(finder_contracts),
        report_errors=tuple(report_errors),
        summary=summary,
        sys_path_enabled=monitor.sys_path_enabled,
        target_outcome=target_outcome,
        cwd=cwd,
        argv=tuple(argv),
    )


_SEVERITY_RANK = {"actionable": 0, "warning": 1, "informational": 2}


def unresolved_attempts(attempts: tuple[ImportAttempt, ...]) -> tuple[ImportAttempt, ...]:
    """Attempts that started, produced no module by report time, and drew no finder claim."""
    return tuple(
        attempt
        for attempt in attempts
        if attempt.presence == "absent_at_report" and attempt.progress in ("started", "failed", "finder_raised")
    )


def _report_summary(
    findings: tuple[Finding, ...],
    explanations: tuple[CausalExplanation, ...],
    attempts: tuple[ImportAttempt, ...],
) -> ReportSummary:
    """Reduce counts and top references; headline prose stays in the text renderer."""
    counts = {"actionable": 0, "warning": 0, "informational": 0}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    top_finding = min(
        (finding for finding in findings if finding.severity != "informational"),
        key=lambda item: (_SEVERITY_RANK.get(item.severity, 1), item.finding_id),
        default=None,
    )
    top_explanation = next(
        (
            explanation
            for explanation in explanations
            if top_finding is not None and explanation.cause_finding_id == top_finding.finding_id
        ),
        None,
    )
    return ReportSummary(
        actionable=counts["actionable"],
        warning=counts["warning"],
        informational=counts["informational"],
        unresolved_import_count=len(unresolved_attempts(attempts)),
        top_finding_id=None if top_finding is None else top_finding.finding_id,
        top_explanation_id=None if top_explanation is None else top_explanation.explanation_id,
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
        if audit is None or finder_type_name not in audit.meta_path_type_names or category != "namespace":
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
    finder_contracts: list[FinderContract],
    attempts: tuple[ImportAttempt, ...],
    report_errors: list[ReportError],
) -> tuple[tuple[Finding, ...], tuple[ResolutionRoute, ...], tuple[RouteComparison, ...]]:
    """Build neutral resolution routes, then promote only corroborated effects."""
    winners = {event.fullname: event for event in events if isinstance(event, FindSpecCall) and event.found}
    structural_context = _structural_context(
        events,
        initial_path_hooks,
        current_path_hooks,
        initial_importer_cache,
        current_importer_cache,
    )
    meta_short_circuit_seqs = _meta_short_circuit_claim_seqs(events)
    findings = _finder_contract_findings(finder_contracts)
    findings.extend(_finder_side_effect_findings(events, len(findings)))
    findings.extend(_module_replacement_findings(events, len(findings)))
    routes: list[ResolutionRoute] = []
    comparisons: list[RouteComparison] = []
    baseline = monitor.baseline_modules
    if module_items is None:
        findings.extend(_path_hook_shadow_findings(events, len(findings)))
        findings.extend(_failed_after_mutation_findings(events, len(findings)))
        return tuple(findings), (), ()
    metadata_by_name = {entry.name: entry for entry in module_metadata}
    for name, module in module_items:
        if type(name) is not str or name in baseline or name == "__main__":
            continue
        winner = winners.get(name)
        if winner is not None:
            captured_route, standard_route, comparison, structural_comparison = _resolution_routes(
                name,
                module,
                winner,
                len(routes) + 1,
                len(comparisons) + 1,
                structural_context,
                winner.seq in meta_short_circuit_seqs,
                report_errors,
            )
            routes.extend((captured_route, standard_route))
            comparisons.append(comparison)
            finding = _corroborated_route_finding(
                name,
                winner,
                captured_route,
                standard_route,
                comparison,
                structural_comparison,
                attempts,
                len(findings) + 1,
                winner.seq in meta_short_circuit_seqs,
            )
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
                    severity="informational",
                )
            )
    findings.extend(_path_hook_shadow_findings(events, len(findings)))
    findings.extend(_failed_after_mutation_findings(events, len(findings)))
    return tuple(findings), tuple(routes), tuple(comparisons)


def _finder_contract_findings(contracts: list[FinderContract]) -> list[Finding]:
    """Expose nonstandard legacy-only contracts as actionable findings."""
    findings: list[Finding] = []
    for contract in contracts:
        if _finder_contract_category(contract) != "legacy_only":
            continue
        findings.append(
            Finding(
                f"finding:{len(findings) + 1}",
                "legacy_finder_contract",
                contract.finder_type_name,
                evidence_level="captured",
                limitations=("protocols_were_inspected_not_invoked",),
                subject_kind="finder",
                finder_contract=contract,
            )
        )
    return findings


def _causal_explanations(
    findings: tuple[Finding, ...],
    attempts: tuple[ImportAttempt, ...],
    standard_resolutions: tuple[StandardResolution, ...],
    route_comparisons: tuple[RouteComparison, ...],
    events: list[MonitorEvent] | None = None,
) -> tuple[CausalExplanation, ...]:
    """Join namespace loss to descendant attempts and report-time path evidence."""
    explanations: list[CausalExplanation] = []
    events_by_seq = {} if events is None else {event.seq: event for event in events}
    comparisons_by_id = {comparison.comparison_id: comparison for comparison in route_comparisons}
    for finding in findings:
        comparison = None if finding.route_comparison_id is None else comparisons_by_id.get(finding.route_comparison_id)
        if finding.kind != "namespace_truncation" or comparison is None or finding.claim is None:
            continue
        prefix = finding.module + "."
        descendant_groups: dict[str, list[ImportAttempt]] = {}
        for attempt in attempts:
            if attempt.fullname.startswith(prefix) and attempt.presence != "present_at_report":
                descendant_groups.setdefault(attempt.fullname, []).append(attempt)
        descendants = [
            next((attempt for attempt in group if attempt.progress == "failed"), group[0])
            for group in descendant_groups.values()
            if not any(attempt.progress == "loaded" for attempt in group)
        ]
        for attempt in descendants:
            relative_parts = attempt.fullname[len(prefix) :].split(".")
            if attempt.progress != "failed" or not any(seq > finding.claim.seq for seq in attempt.event_seqs):
                continue
            match = _omitted_module_candidate(comparison.only_in_right_route, relative_parts)
            if match is None:
                continue
            omitted_location, candidate_path = match
            event_seqs = (
                finding.claim.seq,
                *(seq for seq in attempt.event_seqs if seq > finding.claim.seq),
            )
            explanations.append(
                CausalExplanation(
                    explanation_id=f"explanation:{len(explanations) + 1}",
                    kind="namespace_truncation_failure",
                    confidence="correlated",
                    subject=attempt.fullname,
                    effect_status="failed",
                    cause_finding_id=finding.finding_id,
                    finder_type_name=finding.claim.finder_type_name,
                    omitted_location=omitted_location,
                    candidate_path=candidate_path,
                    event_seqs=tuple(dict.fromkeys(event_seqs)),
                    next_observation=None,
                )
            )
    for resolution in standard_resolutions:
        if resolution.category != "namespace" and resolution.origin is not None:
            components = [
                event
                for seq in resolution.component_event_seqs
                if isinstance((event := events_by_seq.get(seq)), DeepDiagnosticCall)
                and event.boundary == "path_entry_finder"
                and event.outcome == "found"
                and event.path is not None
            ]
            selected_index = next(
                (
                    index
                    for index in range(len(components) - 1, -1, -1)
                    if _path_contains(components[index].path, resolution.origin)
                ),
                None,
            )
            if selected_index is not None and selected_index > 0:
                candidate = components[0]
                selected = components[selected_index]
                event_seqs = tuple(
                    dict.fromkeys(
                        (
                            *((resolution.event_seq,) if resolution.event_seq is not None else ()),
                            candidate.seq,
                            selected.seq,
                        )
                    )
                )
                explanations.append(
                    CausalExplanation(
                        explanation_id=f"explanation:{len(explanations) + 1}",
                        kind="namespace_candidate_displaced",
                        confidence="captured",
                        subject=resolution.fullname,
                        effect_status="regular_module_selected",
                        cause_finding_id=None,
                        finder_type_name=resolution.finder_type_name,
                        omitted_location="",
                        candidate_path=candidate.path or "",
                        event_seqs=event_seqs,
                        next_observation=None,
                        origin=resolution.origin,
                    )
                )
        if not resolution.later_finders:
            continue
        event_seqs = (() if resolution.event_seq is None else (resolution.event_seq,)) + resolution.component_event_seqs
        explanations.append(
            CausalExplanation(
                explanation_id=f"explanation:{len(explanations) + 1}",
                kind="standard_winner_precedence",
                confidence="captured" if resolution.evidence_level == "captured" else "inferred",
                subject=resolution.fullname,
                effect_status=resolution.category,
                cause_finding_id=None,
                finder_type_name=resolution.finder_type_name,
                omitted_location="",
                candidate_path="",
                event_seqs=event_seqs,
                next_observation=(None if resolution.evidence_level == "captured" else "enable_deep_import_outcomes"),
                origin=resolution.origin,
                standard_attempt_id=resolution.attempt_id,
                later_finders=resolution.later_finders,
            )
        )
    for finding in findings:
        if finding.kind == "finder_side_effect" and finding.claim is not None:
            claim = finding.claim
            outcome = "finder_raised" if claim.exception_type_name is not None else "finder_returned_none"
            explanations.append(
                CausalExplanation(
                    explanation_id=f"explanation:{len(explanations) + 1}",
                    kind="finder_side_effect",
                    confidence="captured",
                    subject=finding.module,
                    effect_status=outcome,
                    cause_finding_id=finding.finding_id,
                    finder_type_name=claim.finder_type_name,
                    omitted_location="",
                    candidate_path="",
                    event_seqs=(claim.seq,),
                    next_observation=None,
                    boundary="find_spec",
                    state_before=claim.module_state_before,
                    state_after=claim.module_state_after,
                )
            )
        elif finding.kind in ("module_replacement", "repeated_loader_execution") and finding.deep_call is not None:
            call = finding.deep_call
            baseline = finding.module_state_baseline or call.module_state_before
            target_diverged = (
                baseline is not None
                and baseline.state == "object"
                and call.target_state is not None
                and call.target_state.state == "object"
                and baseline.object_id != call.target_state.object_id
                and (
                    finding.module_state_baseline is not None
                    or not (
                        call.module_state_after is not None
                        and call.module_state_after.state == "object"
                        and baseline.object_id != call.module_state_after.object_id
                    )
                )
            )
            explanations.append(
                CausalExplanation(
                    explanation_id=f"explanation:{len(explanations) + 1}",
                    kind=finding.kind,
                    confidence="captured",
                    subject=finding.module,
                    effect_status="separate_module_executed" if target_diverged else "module_identity_replaced",
                    cause_finding_id=finding.finding_id,
                    finder_type_name=call.object_type_name,
                    omitted_location="",
                    candidate_path="",
                    event_seqs=tuple(dict.fromkeys((*finding.supporting_event_seqs, call.seq))),
                    next_observation=None,
                    boundary=call.boundary,
                    state_before=baseline,
                    state_after=call.target_state if target_diverged else call.module_state_after,
                )
            )
    return _append_ambiguous_explanations(tuple(explanations))


def _path_contains(directory: str | None, path: str) -> bool:
    """Return whether an exact captured path entry contains a selected origin."""
    if directory is None:
        return False
    try:
        return os.path.commonpath((_path_key(directory), _path_key(path))) == _path_key(directory)
    except (OSError, ValueError):
        return False


def _append_ambiguous_explanations(
    explanations: tuple[CausalExplanation, ...],
) -> tuple[CausalExplanation, ...]:
    """Expose equally supported, contradictory conclusions without selecting one."""
    groups: dict[tuple[str, tuple[int, ...]], list[CausalExplanation]] = {}
    for explanation in explanations:
        if explanation.event_seqs:
            groups.setdefault((explanation.subject, explanation.event_seqs), []).append(explanation)

    ambiguous: list[CausalExplanation] = []
    for (subject, event_seqs), group in groups.items():
        conclusions = {(item.kind, item.effect_status) for item in group}
        confidences = {item.confidence for item in group}
        if len(group) < 2 or len(conclusions) < 2 or len(confidences) != 1:
            continue
        ambiguous.append(
            CausalExplanation(
                explanation_id=f"explanation:{len(explanations) + len(ambiguous) + 1}",
                kind="ambiguous_contention",
                confidence="unknown",
                subject=subject,
                effect_status="conflicting_explanations",
                cause_finding_id=None,
                finder_type_name="",
                omitted_location="",
                candidate_path="",
                event_seqs=event_seqs,
                next_observation="capture_a_more_specific_boundary",
                alternatives=tuple(item.explanation_id for item in group),
            )
        )
    return explanations + tuple(ambiguous)


def _omitted_module_candidate(omitted_locations: tuple[str, ...], relative_parts: list[str]) -> tuple[str, str] | None:
    """Find a report-time child directory or module file under an omitted path."""
    for location in omitted_locations:
        stem = os.path.join(location, *relative_parts)
        try:
            if os.path.isdir(stem):
                return location, stem
            for suffix in _MODULE_FILE_SUFFIXES:
                candidate = stem + suffix
                if os.path.isfile(candidate):
                    return location, candidate
        except OSError:
            continue
    return None


def _meta_short_circuit_claim_seqs(events: list[MonitorEvent]) -> set[int]:
    """Identify claims captured before the import-time PathFinder position."""
    latest_audit: dict[tuple[int, str], ImportAuditStart] = {}
    claim_seqs: set[int] = set()
    for event in events:
        if isinstance(event, ImportAuditStart):
            latest_audit[(event.thread_id, event.fullname)] = event
            continue
        if not isinstance(event, FindSpecCall) or not event.found:
            continue
        audit = latest_audit.get((event.thread_id, event.fullname))
        if audit is None or audit.seq >= event.seq or "PathFinder" not in audit.meta_path_type_names:
            continue
        try:
            custom_index = audit.meta_path_type_names.index(event.finder_type_name)
            path_finder_index = audit.meta_path_type_names.index("PathFinder")
        except ValueError:
            continue
        if custom_index >= path_finder_index:
            continue
        claim_seqs.add(event.seq)
    return claim_seqs


def _path_hook_shadow_findings(events: list[MonitorEvent], offset: int) -> list[Finding]:
    """Report paths accepted by distinct hooks across captured deep calls."""
    accepted: dict[str, DeepDiagnosticCall] = {}
    findings: list[Finding] = []
    emitted: set[tuple[str, int, int]] = set()
    for event in events:
        if (
            not isinstance(event, DeepDiagnosticCall)
            or event.boundary != "path_hook"
            or event.outcome != "returned"
            or event.path is None
        ):
            continue
        earlier = accepted.get(event.path)
        if earlier is None:
            accepted[event.path] = event
            continue
        if earlier.object_id == event.object_id:
            continue
        key = (event.path, earlier.object_id, event.object_id)
        if key in emitted:
            continue
        emitted.add(key)
        findings.append(
            Finding(
                f"finding:{offset + len(findings) + 1}",
                "path_hook_shadow",
                event.path,
                deep_call=event,
                evidence_level="structural_inference",
                limitations=("acceptance_was_captured_across_distinct_resolution_states",),
                subject_kind="path",
                supporting_event_seqs=(earlier.seq,),
            )
        )
    return findings


def _failed_after_mutation_findings(events: list[MonitorEvent], offset: int) -> list[Finding]:
    """Pair failures only with structural mutations captured inside that attempt."""
    mutation_seqs: list[int] = []
    started: dict[int, DeepImportEvent] = {}
    findings: list[Finding] = []
    for event in events:
        if isinstance(
            event,
            (
                MetaPathMutation,
                MetaPathReassignment,
                PathHooksMutation,
                PathHooksReassignment,
                SysPathMutation,
                SysPathReassignment,
                ImporterCacheDiff,
            ),
        ):
            mutation_seqs.append(event.seq)
            continue
        if not isinstance(event, DeepImportEvent):
            continue
        if event.outcome == "started":
            started[event.attempt_id] = event
            continue
        if event.outcome != "failed":
            continue
        start = started.get(event.attempt_id)
        if start is None:
            continue
        mutation_seq = next((seq for seq in reversed(mutation_seqs) if start.seq < seq < event.seq), None)
        if mutation_seq is None:
            continue
        findings.append(
            Finding(
                f"finding:{offset + len(findings) + 1}",
                "failed_after_mutation",
                event.fullname,
                evidence_level="structural_inference",
                limitations=("temporal_correlation_does_not_prove_mutation_caused_failure",),
                supporting_event_seqs=(mutation_seq, start.seq, event.seq),
            )
        )
    return findings


def _finder_side_effect_findings(events: list[MonitorEvent], offset: int) -> list[Finding]:
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
                f"finding:{offset + len(findings) + 1}",
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


def _resolution_routes(
    name: str,
    module: object,
    winner: FindSpecCall,
    route_number: int,
    comparison_number: int,
    structural_context: _StructuralContext,
    meta_short_circuit: bool,
    report_errors: list[ReportError],
) -> tuple[ResolutionRoute, ResolutionRoute, RouteComparison, StructuralComparison]:
    """Describe a captured claim and an independent standard-path probe."""
    observed = winner.spec_summary
    if observed is not None:
        observed = _post_hoc_spec_summary(module, observed)
    structural_comparison = _structural_comparison(winner.search_path, structural_context)
    captured_route = ResolutionRoute(
        route_id=f"route:{route_number}",
        module=name,
        kind="captured_claim",
        purpose="record_selected_custom_meta_path_route",
        limitations=(),
        evidence_level="captured",
        state_phase="import",
        predicts_alternative_winner=False,
        finder_type_name=winner.finder_type_name,
        finder_id=winner.finder_id,
        status="found",
        spec_summary=observed,
        exception_type_name=None,
        event_seq=winner.seq,
        search_path=winner.search_path,
        search_path_kind=winner.search_path_kind,
        search_path_phase="import",
        signals=("meta_path_short_circuit",) if meta_short_circuit else (),
    )
    target: types.ModuleType | None = None
    target_available = winner.target_state is None
    if (
        winner.target_state is not None
        and type(module) is types.ModuleType
        and id(module) == winner.target_state.object_id
    ):
        target = module
        target_available = True
    standard_route = _probe_standard_path(
        f"route:{route_number + 1}",
        name,
        winner.search_path,
        winner.search_path_kind,
        target,
        target_available,
    )
    if standard_route.status == "failed":
        report_errors.append(ReportError("standard_path_probe", standard_route.exception_type_name or "Exception"))
    comparison = _compare_routes(
        f"comparison:{comparison_number}",
        captured_route.route_id,
        standard_route.route_id,
        observed,
        standard_route.spec_summary,
        structural_comparison,
        left_status=captured_route.status,
        right_status=standard_route.status,
    )
    return captured_route, standard_route, comparison, structural_comparison


def _corroborated_route_finding(
    name: str,
    winner: FindSpecCall,
    captured_route: ResolutionRoute,
    standard_route: ResolutionRoute,
    comparison: RouteComparison,
    structural_comparison: StructuralComparison,
    attempts: tuple[ImportAttempt, ...],
    finding_number: int,
    meta_short_circuit: bool,
) -> Finding | None:
    """Promote a route difference only when a descendant effect corroborates it."""
    captured = captured_route.spec_summary
    standard = standard_route.spec_summary
    if (
        captured is None
        or standard is None
        or captured.is_namespace is not True
        or standard.is_namespace is not True
        or not comparison.only_in_right_route
    ):
        return None
    prefix = name + "."
    exact_failure = False
    for attempt in attempts:
        if (
            not attempt.fullname.startswith(prefix)
            or attempt.progress != "failed"
            or not any(seq > winner.seq for seq in attempt.event_seqs)
        ):
            continue
        relative_parts = attempt.fullname[len(prefix) :].split(".")
        if _omitted_module_candidate(comparison.only_in_right_route, relative_parts) is None:
            continue
        exact_failure = True
    if not exact_failure:
        return None
    signals: list[str] = []
    if meta_short_circuit:
        signals.append("meta_path_short_circuit")
    if structural_comparison.importer_cache_changed_paths:
        signals.append("importer_cache_changed")
    return Finding(
        f"finding:{finding_number}",
        "namespace_truncation",
        name,
        winner,
        route_ids=(captured_route.route_id, standard_route.route_id),
        route_comparison_id=comparison.comparison_id,
        structural_comparison=structural_comparison,
        evidence_level="correlated",
        limitations=(
            "standard_path_probe_uses_report_time_importer_state",
            "standard_path_probe_does_not_predict_alternative_winner",
            "standard_path_probe_skips_intervening_meta_path_finders",
        ),
        severity="actionable",
        signals=tuple(signals),
    )


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


def _compare_routes(
    comparison_id: str,
    left_route_id: str,
    right_route_id: str,
    left: SpecSummary | None,
    right: SpecSummary | None,
    structural_comparison: StructuralComparison,
    *,
    left_status: str = "found",
    right_status: str = "found",
) -> RouteComparison:
    """Compare two independent routes without assigning either one authority."""
    if left is None or right is None:
        return RouteComparison(
            comparison_id,
            left_route_id,
            right_route_id,
            left_status != right_status,
            False,
            None,
            None,
            None,
            None,
            (),
            (),
            None,
            "unavailable" if left is None else left.locations_state,
            "unavailable" if right is None else right.locations_state,
            structural_comparison,
        )
    loader_differs = (
        None
        if "loader:missing" in left.unavailable_fields or "loader:missing" in right.unavailable_fields
        else _loader_type(left) != _loader_type(right)
    )
    origin_differs = _safe_path_value_changed(left.origin, right.origin)
    cached_differs = _safe_path_value_changed(left.cached, right.cached)
    package_differs = (
        None if left.is_package is None or right.is_package is None else left.is_package != right.is_package
    )
    left_locations = _string_locations(left)
    right_locations = _string_locations(right)
    only_in_right: tuple[str, ...] = ()
    only_in_left: tuple[str, ...] = ()
    reordered: bool | None = None
    if left_locations is not None and right_locations is not None:
        left_keys = tuple(_path_key(path) for path in left_locations)
        right_keys = tuple(_path_key(path) for path in right_locations)
        only_in_right, only_in_left = _location_delta(left_locations, left_keys, right_locations, right_keys)
        reordered = not only_in_right and not only_in_left and left_keys != right_keys
    locations_complete = not (left.is_package is True and left.locations_state in ("deferred", "failed")) and not (
        right.is_package is True and right.locations_state in ("deferred", "failed")
    )
    complete = not left.unavailable_fields and not right.unavailable_fields and locations_complete
    return RouteComparison(
        comparison_id,
        left_route_id,
        right_route_id,
        left_status != right_status,
        complete,
        loader_differs,
        origin_differs,
        cached_differs,
        package_differs,
        only_in_left,
        only_in_right,
        reordered,
        left.locations_state,
        right.locations_state,
        structural_comparison,
    )


def _compare_specs(left: SpecSummary, right: SpecSummary) -> RouteComparison:
    """Compatibility helper for focused unit tests of neutral spec comparison."""
    return _compare_routes(
        "comparison:test",
        "route:left",
        "route:right",
        left,
        right,
        StructuralComparison(None, None, (), ()),
    )


def _module_replacement_findings(events: list[MonitorEvent], offset: int) -> list[Finding]:
    """Return exact object replacements captured across deep loader boundaries."""
    findings: list[Finding] = []
    previous_exec: dict[str, DeepDiagnosticCall] = {}
    for event in events:
        if not isinstance(event, DeepDiagnosticCall) or not event.boundary.startswith("loader_"):
            continue
        before = event.module_state_before
        after = event.module_state_after
        target = event.target_state
        prior = previous_exec.get(event.fullname or "")
        if event.boundary == "loader_exec_module" and event.fullname is not None:
            previous_exec[event.fullname] = event
        cache_replaced = (
            before is not None
            and after is not None
            and before.state == "object"
            and after.state == "object"
            and before.object_id != after.object_id
        )
        target_diverged = (
            event.boundary == "loader_exec_module"
            and before is not None
            and before.state == "object"
            and target is not None
            and target.state == "object"
            and before.object_id != target.object_id
        )
        prior_state = None if prior is None else (prior.target_state or prior.module_state_after)
        prior_diverged = (
            prior_state is not None
            and prior is not None
            and prior.object_id == event.object_id
            and prior_state.state == "object"
            and target is not None
            and target.state == "object"
            and prior_state.object_id != target.object_id
        )
        if event.fullname is None or not (cache_replaced or target_diverged or prior_diverged):
            continue
        kind = "repeated_loader_execution" if prior_diverged else "module_replacement"
        findings.append(
            Finding(
                f"finding:{offset + len(findings) + 1}",
                kind,
                event.fullname,
                deep_call=event,
                module_state_baseline=prior_state if prior_diverged else None,
                supporting_event_seqs=((prior.seq,) if prior_diverged and prior is not None else ()),
                evidence_level="captured",
                limitations=("boundary_delta_does_not_reconstruct_intermediate_states",),
                severity="actionable",
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


def _probe_standard_path(
    route_id: str,
    name: str,
    search_path: tuple[str, ...],
    search_path_kind: str,
    target: types.ModuleType | None,
    target_available: bool,
) -> ResolutionRoute:
    """Probe PathFinder as one route without treating it as the alternative winner."""
    if not target_available:
        return _standard_path_route(
            route_id,
            name,
            search_path,
            search_path_kind,
            "target_unavailable",
        )
    try:
        spec = PathFinder.find_spec(name, search_path, target)
        if spec is None:
            return _standard_path_route(route_id, name, search_path, search_path_kind, "not_found")
        summary, _loader = summarize_spec(spec, iterate_foreign_locations=True)
        return _standard_path_route(route_id, name, search_path, search_path_kind, "found", summary)
    except BaseException as exc:  # A broken finder chain must not break the report.
        return _standard_path_route(
            route_id,
            name,
            search_path,
            search_path_kind,
            "failed",
            exception_type_name=type_name(exc),
        )


def _standard_path_route(
    route_id: str,
    name: str,
    search_path: tuple[str, ...],
    search_path_kind: str,
    status: str,
    spec_summary: SpecSummary | None = None,
    exception_type_name: str | None = None,
) -> ResolutionRoute:
    """Construct one uniformly qualified standard-path probe result."""
    return ResolutionRoute(
        route_id=route_id,
        module=name,
        kind="standard_path_probe",
        purpose="show_standard_path_route_bypassed_by_captured_claim",
        limitations=(
            "captured_search_path_with_report_time_path_hook_and_cache_state",
            "does_not_predict_alternative_winner",
            "skips_intervening_meta_path_finders",
        ),
        evidence_level="live_probe",
        state_phase="report",
        predicts_alternative_winner=False,
        finder_type_name="PathFinder",
        finder_id=id(PathFinder),
        status=status,
        spec_summary=spec_summary,
        exception_type_name=exception_type_name,
        event_seq=None,
        search_path=search_path,
        search_path_kind=search_path_kind,
        search_path_phase="import",
    )


def _same_path(a: str | None, b: str | None) -> bool:
    """Compare filesystem paths after absolutization and case normalization."""
    if a is None or b is None:
        return a == b
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def json_document(document: ReportDocument) -> ReportJSON:
    """Project one report document onto the stable JSON schema."""
    mutations = sum(isinstance(event, MetaPathMutation) for event in document.events)
    reassignments = sum(isinstance(event, MetaPathReassignment) for event in document.events)
    path_hook_mutations = sum(isinstance(event, PathHooksMutation) for event in document.events)
    path_hook_reassignments = sum(isinstance(event, PathHooksReassignment) for event in document.events)
    sys_path_mutations = sum(isinstance(event, SysPathMutation) for event in document.events)
    sys_path_reassignments = sum(isinstance(event, SysPathReassignment) for event in document.events)
    importer_cache_diffs = sum(isinstance(event, ImporterCacheDiff) for event in document.events)
    audit_starts = sum(isinstance(event, ImportAuditStart) for event in document.events)
    calls = sum(isinstance(event, FindSpecCall) for event in document.events)
    deep_calls = sum(isinstance(event, DeepDiagnosticCall) for event in document.events)
    deep_import_events = sum(isinstance(event, DeepImportEvent) for event in document.events)
    standard_finder_calls = sum(isinstance(event, StandardFinderCall) for event in document.events)
    report_status: ReportStatus = (
        "partial"
        if document.report_errors or any(isinstance(event, InternalError) for event in document.events)
        else "complete"
    )
    version = sys.version_info
    mechanisms: list[MechanismJSON] = [
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
        _route_analysis_mechanism(document),
        _mechanism("path_hooks_mutations", document.path_hooks_enabled, path_hook_mutations, "best_effort"),
        _mechanism(
            "path_hooks_reassignments",
            document.path_hooks_enabled,
            path_hook_reassignments,
            "import_boundaries",
        ),
        _mechanism("sys_path_mutations", document.sys_path_enabled, sys_path_mutations, "best_effort"),
        _mechanism(
            "sys_path_reassignments",
            document.sys_path_enabled,
            sys_path_reassignments,
            "import_boundaries",
        ),
        _cache_snapshot_mechanism(document),
        _mechanism(
            "importer_cache_diffs",
            document.importer_cache_enabled,
            importer_cache_diffs,
            "passive_boundaries",
        ),
    ]
    return {
        "schema": {"major": _SCHEMA_MAJOR, "minor": _SCHEMA_MINOR, "name": _SCHEMA_NAME},
        "report_status": report_status,
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
            "frozen_bootstrap": _json_frozen_bootstrap(document.frozen_bootstrap),
            "enabled": document.monitor_enabled,
            "mechanisms": mechanisms,
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
        "resolution_routes": [_json_resolution_route(route) for route in document.resolution_routes],
        "route_comparisons": [_json_route_comparison(comparison) for comparison in document.route_comparisons],
        "timeline": [_json_event(event) for event in document.events],
        "findings": [_json_finding(finding) for finding in document.findings],
        "explanations": [_json_explanation(explanation) for explanation in document.explanations],
        "summary": _json_summary(document.summary),
        "target_outcome": _json_target_outcome(document.target_outcome),
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


def _mechanism(name: str, enabled: bool, retained: int, completeness: str) -> MechanismJSON:
    """Describe a lifetime-growing producer without implying perfect observation."""
    return {
        "capacity": None,
        "completeness": completeness,
        "dropped": 0,
        "enabled": enabled,
        "name": name,
        "overflow_policy": "retain_all",
        "retained": retained,
        "shutdown": "synchronous_no_retry",
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


def _json_finder_contract(contract: FinderContract) -> FinderContractJSON:
    """Project one finder contract without consulting the live finder."""
    return {
        "category": _finder_contract_category(contract),
        "id": _finder_contract_id(contract),
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


def _route_analysis_mechanism(document: ReportDocument) -> MechanismJSON:
    return {
        "capacity": None,
        "comparison_count": len(document.route_comparisons),
        "completeness": "reported_custom_winners",
        "dropped": 0,
        "enabled": document.monitor_enabled,
        "name": "resolution_route_analysis",
        "overflow_policy": "retain_all",
        "retained": len(document.resolution_routes),
        "shutdown": "synchronous_no_retry",
    }


def _cache_snapshot_mechanism(document: ReportDocument) -> MechanismJSON:
    return {
        "capacity": 2,
        "coalesced": document.importer_cache_coalesced,
        "completeness": "passive_boundaries",
        "dropped": 0,
        "enabled": document.importer_cache_enabled,
        "name": "importer_cache_snapshots",
        "observations": document.importer_cache_observations,
        "overflow_policy": "replace_latest",
        "retained": min(document.importer_cache_observations, 2),
        "shutdown": "synchronous_no_retry",
    }


def _finder_contract_id(contract: FinderContract) -> str:
    """Return the document-scoped id for one observed finder contract."""
    return f"finder-contract:0x{contract.finder_id:x}"


def _json_early_site_bootstrap(bootstrap: EarlySiteBootstrap | None) -> EarlySiteBootstrapJSON | None:
    """Serialize early activation provenance without consulting live state."""
    if bootstrap is None:
        return None
    return {
        "activation_source": bootstrap.activation_source,
        "earlier_pth_files": list(bootstrap.earlier_pth_files),
        "path": bootstrap.path,
        "site_packages": bootstrap.site_packages,
    }


def _json_frozen_bootstrap(bootstrap: FrozenBootstrap | None) -> FrozenBootstrapJSON | None:
    """Serialize frozen activation provenance without consulting live state."""
    if bootstrap is None:
        return None
    return {
        "boundary": bootstrap.boundary,
        "integration": bootstrap.integration,
        "path": bootstrap.path,
    }


def _json_module_state(state: ModuleCacheState | None) -> ModuleStateJSON | None:
    """Serialize target-module identity evidence."""
    if state is None:
        return None
    return {
        "object_id": None if state.object_id is None else f"0x{state.object_id:x}",
        "state": state.state,
        "type_name": state.type_name,
    }


def _json_event(event: MonitorEvent) -> EventJSON:
    """Serialize a public event record without inspecting foreign objects."""
    result: EventJSON = {"id": f"event:{event.seq}", "seq": event.seq, "kind": ""}
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
                "target_state": _json_module_state(event.target_state),
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
                "target_state": _json_module_state(event.target_state),
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
    elif isinstance(event, SysPathMutation):
        result.update(
            {
                "added": list(event.added),
                "contents_after": list(event.contents_after),
                "kind": "sys_path_mutation",
                "op": event.op,
                "removed": list(event.removed),
                "stack": [_json_frame(frame) for frame in event.stack],
                "thread_name": event.thread_name,
            }
        )
    elif isinstance(event, SysPathReassignment):
        result.update(
            {
                "during_import": event.during_import,
                "kind": "sys_path_reassignment",
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


def _json_import_attempt(attempt: ImportAttempt) -> ImportAttemptJSON:
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


def _json_standard_resolution(resolution: StandardResolution) -> StandardResolutionJSON:
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


def _json_resolution_route(route: ResolutionRoute) -> ResolutionRouteJSON:
    """Serialize one route without implying that a probe predicts a winner."""
    summary = route.spec_summary
    return {
        "id": route.route_id,
        "module": route.module,
        "kind": route.kind,
        "purpose": route.purpose,
        "limitations": list(route.limitations),
        "evidence_level": route.evidence_level,
        "state_phase": route.state_phase,
        "predicts_alternative_winner": route.predicts_alternative_winner,
        "finder_type_name": route.finder_type_name,
        "finder_id": None if route.finder_id is None else f"0x{route.finder_id:x}",
        "status": route.status,
        "spec": None if summary is None else _json_spec_summary(summary),
        "exception_type_name": route.exception_type_name,
        "event_ref": None if route.event_seq is None else f"event:{route.event_seq}",
        "search_path": list(route.search_path),
        "search_path_kind": route.search_path_kind,
        "search_path_phase": route.search_path_phase,
        "signals": list(route.signals),
    }


def _json_route_comparison(comparison: RouteComparison) -> RouteComparisonJSON:
    """Serialize symmetric route differences and stable route references."""
    structural = comparison.structural_comparison
    return {
        "id": comparison.comparison_id,
        "left_route_ref": comparison.left_route_id,
        "right_route_ref": comparison.right_route_id,
        "complete": comparison.complete,
        "status_differs": comparison.status_differs,
        "loader_type_differs": comparison.loader_type_differs,
        "origin_differs": comparison.origin_differs,
        "cached_differs": comparison.cached_differs,
        "package_status_differs": comparison.package_status_differs,
        "only_in_left_route": list(comparison.only_in_left_route),
        "only_in_right_route": list(comparison.only_in_right_route),
        "locations_reordered": comparison.locations_reordered,
        "left_locations_state": comparison.left_locations_state,
        "right_locations_state": comparison.right_locations_state,
        "structural_comparison": {
            "evidence_level": structural.evidence_level,
            "importer_cache": {
                "changed": structural.importer_cache_changed,
                "changed_paths": list(structural.importer_cache_changed_paths),
                "change_event_refs": [f"event:{seq}" for seq in structural.importer_cache_event_seqs],
                "install_snapshot_ref": "snapshot:importer-cache:install",
                "report_snapshot_ref": "snapshot:importer-cache:report",
            },
            "path_hooks": {
                "changed": structural.path_hooks_changed,
                "install_snapshot_ref": "snapshot:path-hooks:install",
                "report_snapshot_ref": "snapshot:path-hooks:report",
            },
        },
    }


def _json_import_object(reference: ImportObjectRef) -> ImportObjectJSON:
    """Serialize safe import-object identity metadata."""
    return {
        "name": reference.name,
        "object_id": f"0x{reference.object_id:x}",
        "type_name": reference.type_name,
    }


def _json_spec_value(value: str | ImportObjectRef | None) -> SpecValueJSON:
    if isinstance(value, ImportObjectRef):
        result: ImportObjectValueJSON = {"kind": "object", **_json_import_object(value)}
        return result
    return value


def _json_spec_summary(summary: SpecSummary) -> SpecSummaryJSON:
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


def _json_module_metadata(entry: ModuleMetadata) -> ModuleMetadataJSON:
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


def _json_loader_inventory(inventory: LoaderInventory) -> LoaderInventoryInfo:
    """Serialize the exhaustive post-hoc module grouping."""
    unavailable = [entry for entry in inventory.entries if entry.inspection != "available"]
    groups: list[LoaderGroupJSON] = []
    for loader, entries in _loader_inventory_groups(inventory):
        groups.append(
            {
                "loader": None if loader is None else _json_import_object(loader),
                "modules": [_json_module_metadata(entry) for entry in entries],
            }
        )
    return {
        "available": inventory.available,
        "evidence": "post_hoc",
        "groups": groups,
        "non_string_keys_omitted": inventory.non_string_keys,
        "phase": "report",
        "unavailable": [_json_module_metadata(entry) for entry in sorted(unavailable, key=lambda item: item.name)],
    }


def _json_importer_cache_entry(entry: ImporterCacheEntry) -> ImporterCacheEntryJSON:
    """Serialize one path and its cached finder or negative marker."""
    return {
        "finder": None if entry.finder is None else _json_import_object(entry.finder),
        "path": entry.path,
    }


def _json_importer_cache_replacement(replacement: ImporterCacheReplacement) -> ImporterCacheReplacementJSON:
    """Serialize one cached-finder replacement."""
    return {
        "after": None if replacement.after is None else _json_import_object(replacement.after),
        "before": None if replacement.before is None else _json_import_object(replacement.before),
        "path": replacement.path,
    }


def _json_frame(frame: "FrameSummary") -> FrameJSON:
    """Serialize a captured frame without resolving its source line."""
    return {"filename": frame.filename, "function": frame.name, "lineno": frame.lineno}


def _json_finding(finding: Finding) -> FindingJSON:
    """Serialize mechanics; human wording is deliberately not contractual."""
    event_refs: list[str] = []
    if finding.claim is not None:
        event_refs.append(f"event:{finding.claim.seq}")
    if finding.deep_call is not None:
        event_refs.append(f"event:{finding.deep_call.seq}")
    if finding.finder_contract is not None and finding.finder_contract.observation_seq is not None:
        event_refs.append(f"event:{finding.finder_contract.observation_seq}")
    event_refs.extend(f"event:{seq}" for seq in finding.supporting_event_seqs)
    if finding.structural_comparison is not None:
        event_refs.extend(f"event:{seq}" for seq in finding.structural_comparison.importer_cache_event_seqs)
    event_refs = list(dict.fromkeys(event_refs))
    evidence: FindingEvidenceJSON = {
        "event_refs": event_refs,
        "level": finding.evidence_level,
        "limitations": list(finding.limitations),
    }
    result: FindingJSON = {
        "evidence": evidence,
        "id": finding.finding_id,
        "kind": finding.kind,
        "module": finding.module,
        "module_state_baseline": _json_module_state(finding.module_state_baseline),
        "severity": finding.severity,
        "signals": list(finding.signals),
        "subject": {"kind": finding.subject_kind, "value": finding.module},
    }
    if finding.finder_contract is not None:
        result["finder_contract_ref"] = _finder_contract_id(finding.finder_contract)
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
                else "declined"
            )
    if finding.deep_call is not None:
        result["deep_call"] = {
            "boundary": finding.deep_call.boundary,
            "event_ref": f"event:{finding.deep_call.seq}",
            "module_state_after": _json_module_state(finding.deep_call.module_state_after),
            "module_state_before": _json_module_state(finding.deep_call.module_state_before),
            "outcome": finding.deep_call.outcome,
            "target_state": _json_module_state(finding.deep_call.target_state),
        }
    if finding.route_ids:
        result["route_refs"] = list(finding.route_ids)
    if finding.route_comparison_id is not None:
        result["route_comparison_ref"] = finding.route_comparison_id
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


def _json_explanation(explanation: CausalExplanation) -> ExplanationJSON:
    """Serialize causal joins without making human prose a schema contract."""
    return {
        "alternatives": list(explanation.alternatives),
        "candidate_path": explanation.candidate_path,
        "boundary": explanation.boundary,
        "cause_finding_ref": explanation.cause_finding_id,
        "confidence": explanation.confidence,
        "effect_status": explanation.effect_status,
        "event_refs": [f"event:{seq}" for seq in explanation.event_seqs],
        "finder_type_name": explanation.finder_type_name,
        "id": explanation.explanation_id,
        "kind": explanation.kind,
        "later_finders": list(explanation.later_finders),
        "next_observation": explanation.next_observation,
        "origin": explanation.origin,
        "omitted_location": explanation.omitted_location,
        "subject": explanation.subject,
        "standard_attempt_ref": (
            None if explanation.standard_attempt_id is None else f"attempt:{explanation.standard_attempt_id}"
        ),
        "state_after": _json_module_state(explanation.state_after),
        "state_before": _json_module_state(explanation.state_before),
    }


def _json_summary(summary: ReportSummary) -> SummaryInfo:
    """Serialize the shared counts-and-references summary."""
    return {
        "actionable": summary.actionable,
        "warning": summary.warning,
        "informational": summary.informational,
        "unresolved_import_count": summary.unresolved_import_count,
        "top_finding_ref": summary.top_finding_id,
        "top_explanation_ref": summary.top_explanation_id,
    }


def _json_target_outcome(outcome: TargetOutcome | None) -> TargetOutcomeJSON | None:
    """Serialize the recorded target completion, when one exists."""
    if outcome is None:
        return None
    return {
        "kind": outcome.kind,
        "exception_type_name": outcome.exception_type_name,
        "missing_module": outcome.missing_module,
        "exit_code": outcome.exit_code,
    }


def failed_json_document(error_name: str) -> ReportJSON:
    """Return valid JSON structure when ordinary report generation fails."""
    return {
        "schema": {"major": _SCHEMA_MAJOR, "minor": _SCHEMA_MINOR, "name": _SCHEMA_NAME},
        "report_status": "generation_failed",
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
        "standard_resolutions": [],
        "resolution_routes": [],
        "route_comparisons": [],
        "timeline": [],
        "findings": [],
        "explanations": [],
        "summary": {
            "actionable": 0,
            "warning": 0,
            "informational": 0,
            "unresolved_import_count": 0,
            "top_finding_ref": None,
            "top_explanation_ref": None,
        },
        "target_outcome": None,
        "diagnostics": {
            "internal_error_refs": [],
            "report_errors": [{"exception_type_name": error_name, "where": "report_generation"}],
            "skipped_finders": [],
        },
    }
