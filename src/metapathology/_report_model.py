"""Immutable data model shared by report capture, analysis, and rendering."""

from metapathology._module_metadata import ModuleMetadata
from metapathology._record import _Record
from metapathology._records import (
    DeepDiagnosticCall,
    FinderContract,
    FindSpecCall,
    ImporterCacheEntry,
    ModuleCacheState,
    MonitorEvent,
    ObjectRef,
    SpecSummary,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    from metapathology._monitor_model import (
        DeepImportCallsStatus,
        DeepImportOutcomesStatus,
        StandardFinderStatus,
        TargetOutcomeKind,
    )
    from metapathology._records import DeepOutcome, SearchPathKind

    ExplanationConfidence = Literal["captured", "correlated", "inferred", "unknown"]
    ExplanationKind = Literal[
        "ambiguous_contention",
        "captured_claim",
        "finder",
        "finder_side_effect",
        "module_replacement",
        "namespace_candidate_displaced",
        "namespace_truncation_failure",
        "path",
        "repeated_load_failure",
        "repeated_loader_execution",
        "standard_path_probe",
        "standard_winner_precedence",
    ]
    FindingSeverity = Literal["actionable", "warning", "informational"]
    FindingSubjectKind = Literal["module", "finder", "path"]
    FindingKind = Literal[
        "failed_after_mutation",
        "finder_side_effect",
        "legacy_finder_contract",
        "module_replacement",
        "namespace_truncation",
        "no_spec",
        "path_hook_shadow",
        "regular_module_shadows_namespace",
        "repeated_load_failure",
        "repeated_loader_execution",
    ]
    FindingEvidenceLevel = Literal["post_hoc", "captured", "correlated", "structural_inference"]
    ImportPresence = Literal["unknown", "present_at_report", "absent_at_report"]
    ResolutionCategory = Literal["namespace", "built_in", "frozen", "source", "bytecode", "extension", "zip"]
    StatePhase = Literal["import", "report"]
    StandardEvidenceLevel = Literal["captured", "inferred"]
    RouteKind = Literal["captured_claim", "standard_path_probe"]
    RoutePurpose = Literal[
        "record_selected_custom_meta_path_route",
        "show_standard_path_route_bypassed_by_captured_claim",
    ]
    RouteEvidenceLevel = Literal["captured", "live_probe"]
    RouteStatus = Literal["found", "not_found", "failed", "target_unavailable"]
    SearchPathPhase = Literal["import"]
    LocationsComparisonState = Literal["not_applicable", "captured", "post_hoc", "deferred", "failed", "unavailable"]
    ImportProgress = DeepOutcome | Literal["unknown", "finder_claimed", "finder_raised"]
    EffectStatus = (
        ResolutionCategory
        | Literal[
            "failed",
            "regular_module_selected",
            "descendant_failed",
            "separate_module_executed",
            "module_identity_replaced",
            "later_import_failed",
            "conflicting_explanations",
            "finder_raised",
            "finder_returned_none",
        ]
    )


class ReportError(_Record):
    """One failure while copying or analysing report-time state."""

    where: str
    exception_type_name: str


class TargetOutcome(_Record):
    """Plain reduction of how the monitored target finished."""

    kind: "TargetOutcomeKind"
    exception_type_name: str | None
    missing_module: str | None
    exit_code: int | None


class ReportSummary(_Record):
    """Counts and top references shared by the text verdict and JSON summary."""

    actionable: int
    warning: int
    informational: int
    unresolved_import_count: int
    top_finding_id: str | None
    top_explanation_id: str | None


class SkippedFinder(_Record):
    """Report-safe description of an uninstrumented finder."""

    finder_type_name: str
    reason: str
    expected: bool


class LoaderInventory(_Record):
    """Post-hoc metadata copied from one ``sys.modules`` snapshot."""

    available: bool
    entries: tuple[ModuleMetadata, ...]
    non_string_keys: int


class ImportAttempt(_Record):
    """Conservative report-time correlation of one import audit start."""

    attempt_id: int
    fullname: str
    start_event_seq: int
    event_seqs: tuple[int, ...]
    thread_id: int
    thread_name: str
    progress: "ImportProgress"
    presence: "ImportPresence"


class StandardResolution(_Record):
    """Captured or conservatively inferred standard resolution evidence."""

    attempt_id: int
    fullname: str
    finder_type_name: str
    category: "ResolutionCategory"
    loader_type_name: str | None
    origin: str | None
    evidence_level: "StandardEvidenceLevel"
    state_phase: "StatePhase"
    event_seq: int | None
    component_event_seqs: tuple[int, ...]
    later_finders: tuple[str, ...]


class ResolutionRoute(_Record):
    """One captured or probed route to resolving a module name."""

    route_id: str
    module: str
    kind: "RouteKind"
    purpose: "RoutePurpose"
    limitations: tuple[str, ...]
    evidence_level: "RouteEvidenceLevel"
    state_phase: "StatePhase"
    predicts_alternative_winner: bool
    finder_type_name: str
    finder_id: int | None
    status: "RouteStatus"
    spec_summary: SpecSummary | None
    exception_type_name: str | None
    event_seq: int | None
    search_path: tuple[str, ...]
    search_path_kind: "SearchPathKind"
    search_path_phase: "SearchPathPhase"
    signals: tuple[str, ...] = ()


class StructuralComparison(_Record):
    """Identity-only comparison of install and report-time import structure."""

    path_hooks_changed: bool | None
    importer_cache_changed: bool | None
    importer_cache_changed_paths: tuple[str, ...]
    importer_cache_event_seqs: tuple[int, ...]
    evidence_level: 'Literal["structural_comparison"]' = "structural_comparison"


class RouteComparison(_Record):
    """Neutral semantic differences between two resolution routes."""

    comparison_id: str
    left_route_id: str
    right_route_id: str
    status_differs: bool
    complete: bool
    loader_type_differs: bool | None
    origin_differs: bool | None
    cached_differs: bool | None
    package_status_differs: bool | None
    only_in_left_route: tuple[str, ...]
    only_in_right_route: tuple[str, ...]
    locations_reordered: bool | None
    left_locations_state: "LocationsComparisonState"
    right_locations_state: "LocationsComparisonState"
    structural_comparison: StructuralComparison


class Finding(_Record):
    """Structured evidence for one human or machine-readable finding."""

    finding_id: str
    kind: "FindingKind"
    module: str
    claim: FindSpecCall | None = None
    route_ids: tuple[str, ...] = ()
    route_comparison_id: str | None = None
    structural_comparison: StructuralComparison | None = None
    deep_call: DeepDiagnosticCall | None = None
    evidence_level: "FindingEvidenceLevel" = "post_hoc"
    limitations: tuple[str, ...] = ()
    subject_kind: "FindingSubjectKind" = "module"
    finder_contract: FinderContract | None = None
    supporting_event_seqs: tuple[int, ...] = ()
    severity: "FindingSeverity" = "warning"
    signals: tuple[str, ...] = ()
    module_state_baseline: ModuleCacheState | None = None
    attempt_ids: tuple[int, ...] = ()


class CausalExplanation(_Record):
    """Deterministic synthesis joining a primary finding to its likely effect."""

    explanation_id: str
    kind: "ExplanationKind"
    confidence: "ExplanationConfidence"
    subject: str
    effect_status: "EffectStatus"
    cause_finding_id: str | None
    finder_type_name: str
    omitted_location: str
    candidate_path: str
    event_seqs: tuple[int, ...]
    next_observation: str | None
    origin: str | None = None
    standard_attempt_id: int | None = None
    later_finders: tuple[str, ...] = ()
    boundary: str | None = None
    state_before: ModuleCacheState | None = None
    state_after: ModuleCacheState | None = None
    alternatives: tuple[str, ...] = ()


class EarlySiteBootstrap(_Record):
    """Report-safe provenance for generated early site activation."""

    path: str
    site_packages: str
    activation_source: str
    earlier_pth_files: tuple[str, ...]


class FrozenBootstrap(_Record):
    """Report-safe provenance for activation inside a frozen application."""

    integration: str
    path: str
    boundary: str


class ReportDocument(_Record):
    """One sequence-cutoff snapshot shared by all report renderers."""

    generated_at: str
    attempts: tuple[ImportAttempt, ...]
    cutoff_seq: int
    monitor_enabled: bool
    baseline_module_count: int
    initial_meta_path: tuple[str, ...]
    current_meta_path: tuple[str, ...] | None
    path_hooks_enabled: bool
    initial_path_hooks: tuple[ObjectRef, ...]
    current_path_hooks: tuple[ObjectRef, ...] | None
    importer_cache_enabled: bool
    initial_importer_cache: tuple[ImporterCacheEntry, ...]
    initial_importer_cache_non_string_keys: int
    current_importer_cache: tuple[ImporterCacheEntry, ...] | None
    current_importer_cache_non_string_keys: int | None
    importer_cache_observations: int
    importer_cache_coalesced: int
    loader_inventory: LoaderInventory
    modules_since_install: tuple[str, ...] | None
    events: tuple[MonitorEvent, ...]
    explanations: tuple[CausalExplanation, ...]
    early_site_bootstrap: EarlySiteBootstrap | None
    frozen_bootstrap: FrozenBootstrap | None
    deep_diagnostics: tuple[str, ...]
    deep_import_outcomes_status: "DeepImportOutcomesStatus"
    deep_import_calls_status: "DeepImportCallsStatus"
    skipped_finders: tuple[SkippedFinder, ...]
    standard_resolutions: tuple[StandardResolution, ...]
    standard_finder_status: "StandardFinderStatus"
    findings: tuple[Finding, ...]
    resolution_routes: tuple[ResolutionRoute, ...]
    route_comparisons: tuple[RouteComparison, ...]
    finder_contracts: tuple[FinderContract, ...]
    report_errors: tuple[ReportError, ...]
    summary: ReportSummary
    sys_path_enabled: bool
    target_outcome: TargetOutcome | None
    cwd: str | None
    argv: tuple[str, ...]
