"""Typed contract and integrity checks for the machine-readable report."""

from typing import Literal, TypedDict

ReportStatus = Literal["complete", "partial", "generation_failed"]


class SchemaVersion(TypedDict):
    major: int
    minor: int
    name: str


class ToolInfo(TypedDict):
    name: str
    version: str


class ProcessBaseInfo(TypedDict):
    pid: int


class ProcessInfo(ProcessBaseInfo, total=False):
    argv: list[str]
    cwd: str | None
    executable: str | None
    implementation: str
    parent_pid: int
    platform: str
    python_version: str


class ImportObjectJSON(TypedDict):
    name: str | None
    object_id: str
    type_name: str


class ImportObjectValueJSON(ImportObjectJSON):
    kind: Literal["object"]


SpecValueJSON = str | ImportObjectValueJSON | None


class ModuleStateJSON(TypedDict):
    object_id: str | None
    state: str
    type_name: str | None


class SpecSummaryJSON(TypedDict):
    cached: SpecValueJSON
    is_namespace: bool | None
    is_package: bool | None
    loader: ImportObjectJSON | None
    locations_state: str
    origin: SpecValueJSON
    spec: ImportObjectJSON
    submodule_search_locations: list[SpecValueJSON] | None
    unavailable_fields: list[str]


class FrameJSON(TypedDict):
    filename: str
    function: str
    lineno: int | None


class MechanismBaseJSON(TypedDict):
    capacity: int | None
    completeness: str
    dropped: int
    enabled: bool
    name: str
    overflow_policy: str
    retained: int
    shutdown: str


class MechanismJSON(MechanismBaseJSON, total=False):
    coalesced: int
    comparison_count: int
    observations: int


class EarlySiteBootstrapJSON(TypedDict):
    activation_source: str
    earlier_pth_files: list[str]
    path: str
    site_packages: str


class FrozenBootstrapJSON(TypedDict):
    boundary: str
    integration: str
    path: str | None


class ImporterCacheEntryJSON(TypedDict):
    finder: ImportObjectJSON | None
    path: str


class ImporterCacheReplacementJSON(TypedDict):
    after: ImportObjectJSON | None
    before: ImportObjectJSON | None
    path: str


class CaptureInfo(TypedDict, total=False):
    baseline_module_count: int
    cutoff_seq: int | None
    early_site_bootstrap: EarlySiteBootstrapJSON | None
    frozen_bootstrap: FrozenBootstrapJSON | None
    enabled: bool
    mechanisms: list[MechanismJSON]
    modules_since_install: list[str] | None


class SnapshotBaseJSON(TypedDict):
    entries: list[str] | list[ImportObjectJSON] | list[ImporterCacheEntryJSON] | None
    id: str
    kind: str
    phase: str


class SnapshotJSON(SnapshotBaseJSON, total=False):
    non_string_keys: int | None


class ProtocolJSON(TypedDict):
    availability: str
    defined_by: str | None
    evidence: str


class FinderContractJSON(TypedDict):
    category: str
    id: str
    finder_id: str
    finder_type_name: str
    find_module: ProtocolJSON
    find_spec: ProtocolJSON
    observation: str
    observation_event_ref: str | None
    position: int


class ImportAttemptJSON(TypedDict):
    id: str
    fullname: str
    start_event_ref: str
    evidence_event_refs: list[str]
    thread_id: int
    thread_name: str
    progress: str
    presence: str


class StandardResolutionJSON(TypedDict):
    attempt_ref: str
    category: str
    component_event_refs: list[str]
    evidence_level: str
    event_ref: str | None
    finder_type_name: str
    fullname: str
    later_finders: list[str]
    loader_type_name: str | None
    origin: str | None
    state_phase: str


class ResolutionRouteJSON(TypedDict):
    id: str
    module: str
    kind: str
    purpose: str
    limitations: list[str]
    evidence_level: str
    state_phase: str
    predicts_alternative_winner: bool
    finder_type_name: str
    finder_id: str | None
    status: str
    spec: SpecSummaryJSON | None
    exception_type_name: str | None
    event_ref: str | None
    search_path: list[str]
    search_path_kind: str
    search_path_phase: str
    signals: list[str]


class ImporterCacheComparisonJSON(TypedDict):
    changed: bool | None
    changed_paths: list[str]
    change_event_refs: list[str]
    install_snapshot_ref: str
    report_snapshot_ref: str


class PathHooksComparisonJSON(TypedDict):
    changed: bool | None
    install_snapshot_ref: str
    report_snapshot_ref: str


class StructuralComparisonJSON(TypedDict):
    evidence_level: str
    importer_cache: ImporterCacheComparisonJSON
    path_hooks: PathHooksComparisonJSON


class RouteComparisonJSON(TypedDict):
    id: str
    left_route_ref: str
    right_route_ref: str
    complete: bool
    status_differs: bool
    loader_type_differs: bool | None
    origin_differs: bool | None
    cached_differs: bool | None
    package_status_differs: bool | None
    only_in_left_route: list[str]
    only_in_right_route: list[str]
    locations_reordered: bool | None
    left_locations_state: str
    right_locations_state: str
    structural_comparison: StructuralComparisonJSON


class EventBaseJSON(TypedDict):
    id: str
    seq: int
    kind: str


class ObjectSizeJSON(TypedDict):
    object_id: str
    size: int | None


class MetaPathStateJSON(TypedDict):
    entries: list[str]
    object_id: str


class EventJSON(EventBaseJSON, total=False):
    added: list[str] | list[ImportObjectJSON] | list[ImporterCacheEntryJSON]
    attempt_id: int
    boundary: str
    contents_after: list[str] | list[ImportObjectJSON]
    during_import: str | None
    evidence: str
    exception_type_name: str | None
    finder_id: str
    finder_type_name: str
    found: bool
    fullname: str | None
    importer_cache: ObjectSizeJSON | None
    loader_type_name: str | None
    message: str | None
    meta_path: MetaPathStateJSON
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    new_contents: list[str] | list[ImportObjectJSON]
    non_string_keys_after: int
    non_string_keys_before: int
    object_id: str
    object_type_name: str
    observation: str
    old_contents: list[str] | list[ImportObjectJSON]
    op: str
    origin: str | None
    outcome: str
    path: str | None
    path_hooks_id: str | None
    removed: list[str] | list[ImportObjectJSON] | list[ImporterCacheEntryJSON]
    replaced: list[ImporterCacheReplacementJSON]
    search_path: list[str]
    search_path_kind: str
    spec: SpecSummaryJSON | None
    stack: list[FrameJSON]
    target_state: ModuleStateJSON | None
    thread_id: int
    thread_name: str
    where: str


class ModuleMetadataJSON(TypedDict):
    inspection: str
    loader_agreement: bool | None
    loader_source: str
    module: ImportObjectJSON
    module_loader: ImportObjectJSON | None
    module_loader_available: bool
    name: str
    reason: str | None
    spec: SpecSummaryJSON | None
    spec_present: bool | None


class LoaderGroupJSON(TypedDict):
    loader: ImportObjectJSON | None
    modules: list[ModuleMetadataJSON]


class LoaderInventoryInfo(TypedDict):
    available: bool
    evidence: str
    groups: list[LoaderGroupJSON]
    non_string_keys_omitted: int
    phase: str
    unavailable: list[ModuleMetadataJSON]


class FindingEvidenceJSON(TypedDict, total=False):
    event_refs: list[str]
    level: str
    limitations: list[str]
    finder_claim: str
    module_spec: str
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: str


class FindingSubjectJSON(TypedDict):
    kind: str
    value: str


class FindingClaimJSON(TypedDict):
    event_ref: str
    finder_id: str
    finder_type_name: str
    loader_type_name: str | None
    origin: str | None
    search_path: list[str]
    search_path_kind: str
    spec: SpecSummaryJSON | None


class FindingDeepCallJSON(TypedDict):
    boundary: str
    event_ref: str
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: str
    target_state: ModuleStateJSON | None


class FindingBaseJSON(TypedDict):
    evidence: FindingEvidenceJSON
    id: str
    kind: str
    module: str
    module_state_baseline: ModuleStateJSON | None
    severity: str
    signals: list[str]
    subject: FindingSubjectJSON


class FindingJSON(FindingBaseJSON, total=False):
    attempt_refs: list[str]
    finder_contract_ref: str
    claim: FindingClaimJSON
    deep_call: FindingDeepCallJSON
    route_refs: list[str]
    route_comparison_ref: str
    structural_comparison: StructuralComparisonJSON


class ExplanationJSON(TypedDict):
    alternatives: list[str]
    candidate_path: str | None
    boundary: str | None
    cause_finding_ref: str | None
    confidence: str
    effect_status: str | None
    event_refs: list[str]
    finder_type_name: str | None
    id: str
    kind: str
    later_finders: list[str]
    next_observation: str | None
    origin: str | None
    omitted_location: str | None
    subject: str
    standard_attempt_ref: str | None
    state_after: ModuleStateJSON | None
    state_before: ModuleStateJSON | None


class SummaryInfo(TypedDict):
    actionable: int
    warning: int
    informational: int
    unresolved_import_count: int
    top_finding_ref: str | None
    top_explanation_ref: str | None


class TargetOutcomeJSON(TypedDict):
    kind: str
    exception_type_name: str | None
    missing_module: str | None
    exit_code: int | None


class ReportErrorJSON(TypedDict):
    exception_type_name: str
    where: str


class SkippedFinderJSON(TypedDict):
    expected: bool
    finder_type_name: str
    reason: str


class DiagnosticsInfo(TypedDict):
    internal_error_refs: list[str]
    report_errors: list[ReportErrorJSON]
    skipped_finders: list[SkippedFinderJSON]


class ReportJSON(TypedDict):
    """Complete schema 1.0 report document."""

    schema: SchemaVersion
    report_status: ReportStatus
    tool: ToolInfo
    generated_at: str
    process: ProcessInfo
    capture: CaptureInfo
    snapshots: list[SnapshotJSON]
    loader_inventory: LoaderInventoryInfo
    finder_contracts: list[FinderContractJSON]
    import_attempts: list[ImportAttemptJSON]
    standard_resolutions: list[StandardResolutionJSON]
    resolution_routes: list[ResolutionRouteJSON]
    route_comparisons: list[RouteComparisonJSON]
    timeline: list[EventJSON]
    findings: list[FindingJSON]
    explanations: list[ExplanationJSON]
    summary: SummaryInfo
    target_outcome: TargetOutcomeJSON | None
    diagnostics: DiagnosticsInfo


__all__ = ["ReportJSON", "ReportStatus"]
