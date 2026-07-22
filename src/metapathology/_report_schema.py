"""Typed contract and integrity checks for the machine-readable report.

The vocabularies below are defined at module scope (not under ``TYPE_CHECKING``)
because :mod:`scripts.generate_report_schema` resolves these annotations at
runtime via :func:`typing.get_type_hints` to emit the bundled JSON Schema. They
mirror the internal aliases in :mod:`metapathology._records` and
:mod:`metapathology._report_model`; those remain the source of truth for
production typing, while these are the frozen public schema vocabulary.
"""

from typing import Literal, TypedDict

ReportStatus = Literal["complete", "partial", "generation_failed"]

# JSON ``kind`` discriminants for timeline events. Mirrors the ``EVENT_KIND``
# registry values in ``_report_events``; keep the two in lockstep (asserted by
# ``test_schema_vocabulary``).
EventKind = Literal[
    "import_audit_start",
    "deep_diagnostic_call",
    "deep_import_event",
    "import_call",
    "standard_finder_call",
    "find_spec_call",
    "importer_cache_diff",
    "meta_path_mutation",
    "meta_path_reassignment",
    "path_hooks_mutation",
    "path_hooks_reassignment",
    "sys_path_mutation",
    "sys_path_reassignment",
    "internal_error",
]

# Closed vocabularies mirrored from the internal typing aliases.
SnapshotKind = Literal["meta_path", "path_hooks", "importer_cache"]
SnapshotPhase = Literal["install", "report"]
OverflowPolicy = Literal["retain_all", "replace_latest"]
Shutdown = Literal["synchronous_no_retry"]
CacheState = Literal["unavailable", "missing", "none", "object"]
ProtocolAvailability = Literal["callable", "non_callable", "indeterminate", "absent"]
LocationsState = Literal["not_applicable", "captured", "post_hoc", "deferred", "failed"]
LocationsComparisonState = Literal["not_applicable", "captured", "post_hoc", "deferred", "failed", "unavailable"]
SearchPathKind = Literal["sys_path", "parent_path"]
SearchPathPhase = Literal["import"]
StatePhase = Literal["import", "report"]
ResolutionCategory = Literal["namespace", "built_in", "frozen", "source", "bytecode", "extension", "zip"]
StandardEvidenceLevel = Literal["captured", "inferred"]
RouteKind = Literal["captured_claim", "standard_path_probe"]
RoutePurpose = Literal[
    "record_selected_custom_meta_path_route",
    "show_standard_path_route_bypassed_by_captured_claim",
]
RouteEvidenceLevel = Literal["captured", "live_probe"]
RouteStatus = Literal["found", "not_found", "failed", "target_unavailable"]
ImportPresence = Literal["unknown", "present_at_report", "absent_at_report"]
ImportProgress = Literal[
    "started",
    "loaded",
    "failed",
    "found",
    "not_found",
    "returned",
    "raised",
    "unobserved_reentrant",
    "unknown",
    "finder_claimed",
    "finder_raised",
]
DeepBoundary = Literal["path_entry_finder", "loader_create_module", "loader_exec_module", "path_hook"]
MutationOp = Literal[
    "append",
    "insert",
    "extend",
    "remove",
    "pop",
    "clear",
    "reverse",
    "sort",
    "__setitem__",
    "__delitem__",
    "__iadd__",
    "__imul__",
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
FindingDetail = Literal["bare", "correlation", "contract", "claim", "route", "deep_call"]

# Closed vocabularies for fields that were previously open ``str``. Mirrors the
# internal producers (``_report_json`` mechanism builders, ``_report_analysis``
# finder-contract category, ``_finder_attribution`` observation, and
# ``_module_metadata``); keep in lockstep (asserted by ``test_schema_vocabulary``).
MechanismName = Literal[
    "meta_path_mutations",
    "meta_path_reassignments",
    "import_audit_starts",
    "finder_attribution",
    "finder_contracts",
    "deep_diagnostics",
    "deep_import_outcomes",
    "deep_import_calls",
    "standard_finder_aggregate",
    "resolution_route_analysis",
    "path_hooks_mutations",
    "path_hooks_reassignments",
    "sys_path_mutations",
    "sys_path_reassignments",
    "importer_cache_snapshots",
    "importer_cache_diffs",
]
# Static completeness labels plus the three per-mechanism status vocabularies
# (StandardFinderStatus, DeepImportOutcomesStatus, DeepImportCallsStatus in
# ``_monitor_model``), flattened and de-duplicated.
MechanismCompleteness = Literal[
    "best_effort",
    "import_boundaries",
    "resolution_starts",
    "instrumented_finders",
    "first_observation_per_identity",
    "delegated_boundaries",
    "reported_custom_winners",
    "passive_boundaries",
    "disabled",
    "unavailable_existing_profiler",
    "active_path_finder_aggregate",
    "unsupported_path_finder_boundary",
    "inactive_after_uninstall",
    "refused_existing_profiler",
    "unsupported_boundary",
    "active_current_and_future_threading_threads_cache_hits_not_observed",
    "active_all_threads_including_cache_hits",
]
FinderContractCategory = Literal["indeterminate", "legacy_only", "modern", "modern_and_legacy", "protocol_less"]
FinderContractObservation = Literal["install", "reassignment", "mutation"]
ModuleInspection = Literal["available", "unavailable"]
ModuleLoaderSource = Literal["spec", "module", "none"]
# Effective ``outcome`` vocabularies for deep-diagnostic and speculative-replay
# payloads. Mirrors ``DeepOutcome`` and ``SpeculativeReplayOutcome`` in
# ``_records``. ``FindingEvidenceJSON.outcome`` stays ``str``: it embeds the
# raising exception type (``raised:<type>``) and is not a closed vocabulary.
DeepOutcome = Literal[
    "started",
    "loaded",
    "failed",
    "found",
    "not_found",
    "returned",
    "raised",
    "unobserved_reentrant",
]
SpeculativeReplayOutcome = Literal[
    "returned_spec",
    "returned_none",
    "raised",
    "declined_target_unavailable",
    "finder_unavailable",
    "unsupported_finder",
]


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
    state: CacheState
    type_name: str | None


class SpecSummaryJSON(TypedDict):
    cached: SpecValueJSON
    is_namespace: bool | None
    is_package: bool | None
    loader: ImportObjectJSON | None
    locations_state: LocationsState
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
    completeness: MechanismCompleteness
    dropped: int
    enabled: bool
    name: MechanismName
    overflow_policy: OverflowPolicy
    retained: int
    shutdown: Shutdown


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


# Snapshots share one envelope `{id, kind, phase, data}`; `kind` selects the
# per-kind payload. `entries` differs in element type per kind and
# `non_string_keys` exists only on the importer-cache kind, so nesting under
# `data` lets the schema follow the discriminant instead of an untagged union.


class MetaPathSnapshotDataJSON(TypedDict):
    entries: list[str] | None


class PathHooksSnapshotDataJSON(TypedDict):
    entries: list[ImportObjectJSON] | None


class ImporterCacheSnapshotDataJSON(TypedDict):
    entries: list[ImporterCacheEntryJSON] | None
    non_string_keys: int | None


SnapshotDataJSON = MetaPathSnapshotDataJSON | PathHooksSnapshotDataJSON | ImporterCacheSnapshotDataJSON


class SnapshotJSON(TypedDict):
    data: SnapshotDataJSON
    id: str
    kind: SnapshotKind
    phase: SnapshotPhase


class ProtocolJSON(TypedDict):
    availability: ProtocolAvailability
    defined_by: str | None
    evidence: str


class FinderContractJSON(TypedDict):
    category: FinderContractCategory
    id: str
    finder_id: str
    finder_type_name: str
    find_module: ProtocolJSON
    find_spec: ProtocolJSON
    observation: FinderContractObservation
    observation_event_ref: str | None
    position: int


class ImportAttemptJSON(TypedDict):
    id: str
    fullname: str
    start_event_ref: str
    evidence_event_refs: list[str]
    thread_id: int
    thread_name: str
    progress: ImportProgress
    presence: ImportPresence


class StandardResolutionJSON(TypedDict):
    attempt_ref: str
    category: ResolutionCategory
    component_event_refs: list[str]
    evidence_level: StandardEvidenceLevel
    event_ref: str | None
    finder_type_name: str
    fullname: str
    later_finders: list[str]
    loader_type_name: str | None
    origin: str | None
    state_phase: StatePhase


class ResolutionRouteJSON(TypedDict):
    id: str
    module: str
    kind: RouteKind
    purpose: RoutePurpose
    limitations: list[str]
    evidence_level: RouteEvidenceLevel
    state_phase: StatePhase
    predicts_alternative_winner: bool
    finder_type_name: str
    finder_id: str | None
    status: RouteStatus
    spec: SpecSummaryJSON | None
    exception_type_name: str | None
    event_ref: str | None
    search_path: list[str]
    search_path_kind: SearchPathKind
    search_path_phase: SearchPathPhase
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
    evidence_level: Literal["structural_comparison"]
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
    left_locations_state: LocationsComparisonState
    right_locations_state: LocationsComparisonState
    structural_comparison: StructuralComparisonJSON


class ObjectSizeJSON(TypedDict):
    object_id: str
    size: int | None


class MetaPathStateJSON(TypedDict):
    entries: list[str]
    object_id: str


# --- Timeline event envelope + per-kind payloads ----------------------------
#
# Every timeline event is ``{id, seq, kind, data}``: a shared envelope plus a
# fully-typed, kind-specific ``data`` payload. ``kind`` is the discriminant for
# the ``EventDataJSON`` union. Each payload TypedDict is total; the matching
# ``_json_<kind>`` builder always emits every field.


class ImportAuditStartDataJSON(TypedDict):
    evidence: Literal["resolution_started"]
    fullname: str | None
    importer_cache: ObjectSizeJSON | None
    attempt_ref: str
    meta_path: MetaPathStateJSON
    path_hooks_id: str | None
    thread_name: str
    thread_id: int


class DeepDiagnosticCallDataJSON(TypedDict):
    boundary: DeepBoundary
    evidence: Literal["deep_delegation"]
    exception_type_name: str | None
    fullname: str | None
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    object_id: str
    object_type_name: str
    outcome: DeepOutcome
    path: str | None
    returned_finder: ImportObjectJSON | None
    target_state: ModuleStateJSON | None
    thread_name: str
    thread_id: int


class DeepImportEventDataJSON(TypedDict):
    attempt_ref: str
    evidence: Literal["exact_import_boundary"]
    fullname: str
    outcome: DeepOutcome
    thread_id: int
    thread_name: str


class ImportCallDataJSON(TypedDict):
    evidence: Literal["import_call_wrapper"]
    exception_type_name: str | None
    fromlist: list[str]
    importing_module: str | None
    level: int
    module_state_before: ModuleStateJSON | None
    name: str
    outcome: DeepOutcome
    thread_id: int
    thread_name: str


class StandardFinderCallDataJSON(TypedDict):
    attempt_ref: str
    evidence: Literal["captured_standard_finder_boundary"]
    finder_type_name: str
    fullname: str
    spec: SpecSummaryJSON | None
    thread_id: int
    thread_name: str


class FindSpecCallDataJSON(TypedDict):
    exception_type_name: str | None
    finder_id: str
    finder_type_name: str
    found: bool
    fullname: str
    loader_type_name: str | None
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    origin: str | None
    search_path: list[str]
    search_path_kind: SearchPathKind
    spec: SpecSummaryJSON | None
    target_state: ModuleStateJSON | None
    thread_name: str
    thread_id: int


class ImporterCacheDiffDataJSON(TypedDict):
    added: list[ImporterCacheEntryJSON]
    non_string_keys_after: int
    non_string_keys_before: int
    observation: str
    removed: list[ImporterCacheEntryJSON]
    replaced: list[ImporterCacheReplacementJSON]
    thread_name: str


class MetaPathMutationDataJSON(TypedDict):
    added: list[str]
    contents_after: list[str]
    op: MutationOp
    removed: list[str]
    stack: list[FrameJSON]
    thread_name: str


class ReassignmentDataJSON(TypedDict):
    during_import: str | None
    new_contents: list[str]
    old_contents: list[str]
    stack: list[FrameJSON]
    thread_name: str


class PathHooksMutationDataJSON(TypedDict):
    added: list[ImportObjectJSON]
    contents_after: list[ImportObjectJSON]
    op: MutationOp
    removed: list[ImportObjectJSON]
    stack: list[FrameJSON]
    thread_name: str


class PathHooksReassignmentDataJSON(TypedDict):
    during_import: str | None
    new_contents: list[ImportObjectJSON]
    old_contents: list[ImportObjectJSON]
    stack: list[FrameJSON]
    thread_name: str


class SysPathMutationDataJSON(TypedDict):
    added: list[str]
    contents_after: list[str]
    op: MutationOp
    removed: list[str]
    stack: list[FrameJSON]
    thread_name: str


class InternalErrorDataJSON(TypedDict):
    exception_type_name: str
    message: str | None
    where: str


EventDataJSON = (
    ImportAuditStartDataJSON
    | DeepDiagnosticCallDataJSON
    | DeepImportEventDataJSON
    | ImportCallDataJSON
    | StandardFinderCallDataJSON
    | FindSpecCallDataJSON
    | ImporterCacheDiffDataJSON
    | MetaPathMutationDataJSON
    | ReassignmentDataJSON
    | PathHooksMutationDataJSON
    | PathHooksReassignmentDataJSON
    | SysPathMutationDataJSON
    | InternalErrorDataJSON
)


class EventJSON(TypedDict):
    id: str
    seq: int
    kind: EventKind
    data: EventDataJSON


class ModuleMetadataJSON(TypedDict):
    inspection: ModuleInspection
    loader_agreement: bool | None
    loader_source: ModuleLoaderSource
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
    evidence: Literal["post_hoc"]
    groups: list[LoaderGroupJSON]
    non_string_keys_omitted: int
    phase: Literal["report"]
    unavailable: list[ModuleMetadataJSON]


class FindingEvidenceJSON(TypedDict, total=False):
    event_refs: list[str]
    level: FindingEvidenceLevel
    limitations: list[str]
    finder_claim: str
    module_spec: str
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: str


class FindingSubjectJSON(TypedDict):
    kind: FindingSubjectKind
    value: str


class FindingClaimJSON(TypedDict):
    event_ref: str
    finder_id: str
    finder_type_name: str
    loader_type_name: str | None
    origin: str | None
    search_path: list[str]
    search_path_kind: SearchPathKind
    spec: SpecSummaryJSON | None


class FindingDeepCallJSON(TypedDict):
    boundary: DeepBoundary
    event_ref: str
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: DeepOutcome
    target_state: ModuleStateJSON | None


# Findings share one envelope plus a `detail`-tagged `data` payload. Every
# `Finding.kind` maps to exactly one evidence family (see `_report_model`), so
# `data.detail` selects which variant-specific members are present instead of a
# flat `total=False` grab-bag on the finding itself.


class BareEvidenceJSON(TypedDict):
    detail: Literal["bare"]


class CorrelationEvidenceJSON(TypedDict):
    detail: Literal["correlation"]
    attempt_refs: list[str]


class ContractEvidenceJSON(TypedDict):
    detail: Literal["contract"]
    finder_contract_ref: str


class ClaimEvidenceJSON(TypedDict):
    detail: Literal["claim"]
    claim: FindingClaimJSON


class RouteEvidenceJSON(TypedDict):
    detail: Literal["route"]
    claim: FindingClaimJSON
    route_refs: list[str]
    route_comparison_ref: str
    structural_comparison: StructuralComparisonJSON


class DeepCallEvidenceJSON(TypedDict):
    detail: Literal["deep_call"]
    deep_call: FindingDeepCallJSON
    module_state_baseline: ModuleStateJSON | None


FindingEvidenceDataJSON = (
    BareEvidenceJSON
    | CorrelationEvidenceJSON
    | ContractEvidenceJSON
    | ClaimEvidenceJSON
    | RouteEvidenceJSON
    | DeepCallEvidenceJSON
)


class FindingJSON(TypedDict):
    data: FindingEvidenceDataJSON
    evidence: FindingEvidenceJSON
    id: str
    kind: FindingKind
    module: str
    severity: FindingSeverity
    signals: list[str]
    subject: FindingSubjectJSON


class ExplanationJSON(TypedDict):
    alternatives: list[str]
    candidate_path: str | None
    boundary: str | None
    cause_finding_ref: str | None
    confidence: ExplanationConfidence
    effect_status: str | None
    event_refs: list[str]
    finder_type_name: str | None
    id: str
    kind: ExplanationKind
    later_finders: list[str]
    next_observation: str | None
    origin: str | None
    omitted_location: str | None
    subject: str
    standard_attempt_ref: str | None
    state_after: ModuleStateJSON | None
    state_before: ModuleStateJSON | None


class SeverityCountsJSON(TypedDict):
    actionable: int
    warning: int
    informational: int


class SummaryInfo(TypedDict):
    counts: SeverityCountsJSON
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


class SpeculativeReplayJSON(TypedDict):
    attempt_event_ref: str
    diff_event_ref: str
    displaced_finder: ImportObjectJSON
    exception_type_name: str | None
    fullname: str
    outcome: SpeculativeReplayOutcome
    path: str
    spec: SpecSummaryJSON | None
    state_phase: StatePhase


class SpeculativeReplayInfoJSON(TypedDict):
    enabled: bool
    omitted: int
    probe_cap: int
    replays: list[SpeculativeReplayJSON]


class ReportJSON(TypedDict):
    """Complete schema 2.0 report document."""

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
    speculative_replay: SpeculativeReplayInfoJSON
    target_outcome: TargetOutcomeJSON | None
    diagnostics: DiagnosticsInfo


__all__ = ["ReportJSON", "ReportStatus"]
