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
    "import_branch_exploration_started",
    "import_branch_exploration_call",
    "import_search_started",
    "import_mechanism_call",
    "import_result",
    "import_call",
    "path_finder_call",
    "meta_path_finder_call",
    "importer_cache_change",
    "meta_path_change",
    "meta_path_replacement",
    "path_hooks_change",
    "path_hooks_replacement",
    "sys_path_change",
    "sys_path_replacement",
    "monitoring_error",
]
ImportBranchBoundary = Literal["meta_path", "path_hook", "path_entry"]
ImportBranchTriggerOutcome = Literal["found", "returned", "raised"]
ImportBranchExplorationOutcome = Literal[
    "found",
    "not_found",
    "returned_finder",
    "declined",
    "raised",
    "unsupported",
    "negative_cache",
]

# Closed vocabularies mirrored from the internal typing aliases.
SnapshotKind = Literal["meta_path", "path_hooks", "importer_cache"]
SnapshotPhase = Literal["install", "report"]
OverflowPolicy = Literal["retain_all", "replace_latest"]
Shutdown = Literal["synchronous_no_retry"]
CacheState = Literal["unavailable", "missing", "none", "object"]
ProtocolAvailability = Literal["callable", "non_callable", "indeterminate", "absent"]
LocationsState = Literal["not_applicable", "captured", "current_state", "deferred", "failed"]
LocationsComparisonState = Literal["not_applicable", "captured", "current_state", "deferred", "failed", "unavailable"]
SearchPathKind = Literal["sys_path", "parent_path", "path_entry"]
SearchPathPhase = Literal["import"]
StatePhase = Literal["import", "report"]
ResolutionCategory = Literal["namespace", "built_in", "frozen", "source", "bytecode", "extension", "zip"]
StandardEvidenceLevel = Literal["observed", "inferred"]
FinderResultKind = Literal[
    "observed_finder_result",
    "standard_path_check",
    "displaced_finder_check",
    "import_branch_exploration_result",
]
FinderResultPurpose = Literal[
    "record_custom_finder_result",
    "compare_with_current_pathfinder",
    "check_displaced_importer_cache_finder",
    "record_skipped_import_candidate_result",
]
FinderResultEvidenceLevel = Literal["observed", "current_state_check", "explored"]
FinderResultStatus = Literal[
    "found", "not_found", "failed", "target_unavailable", "finder_unavailable", "unsupported_finder"
]
CheckKind = Literal["standard_path", "displaced_finder"]
CheckStatus = Literal["active", "disabled", "unavailable"]
ImportPresence = Literal["unknown", "present_at_report", "absent_at_report"]
ImportSearchProgress = Literal[
    "started",
    "loaded",
    "failed",
    "found",
    "not_found",
    "returned",
    "raised",
    "unobserved_reentrant",
    "unknown",
    "finder_found",
    "finder_raised",
]
ImportMechanismBoundary = Literal["path_entry_finder", "loader_create_module", "loader_exec_module", "path_hook"]
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
FindingSeverity = Literal["problem", "risk", "note"]
FindingSubjectKind = Literal["module", "finder", "path"]
FindingKind = Literal[
    "import_failed_after_state_change",
    "finder_changed_module_cache",
    "legacy_finder_api",
    "module_replacement",
    "missing_namespace_locations",
    "module_without_spec",
    "competing_path_hooks",
    "module_hides_namespace",
    "module_failed_after_loading",
    "module_executed_again",
]
FindingEvidenceLevel = Literal["current_state", "observed", "correlated", "inferred_from_state"]
ExplanationConfidence = Literal["observed", "correlated", "inferred", "unknown"]
ExplanationKind = Literal[
    "ambiguous_contention",
    "observed_finder_result",
    "finder",
    "finder_changed_module_cache",
    "module_replacement",
    "namespace_candidate_hidden",
    "missing_namespace_locations_failure",
    "path",
    "module_failed_after_loading",
    "module_executed_again",
    "standard_path_check",
    "path_finder_precedence",
]
FindingDetail = Literal[
    "events_only",
    "correlation",
    "finder_api",
    "finder_call",
    "result_comparison",
    "import_mechanism_call",
]

# Closed vocabularies for fields that were previously open ``str``. Mirrors the
# internal producers (``_report_json`` mechanism builders, ``_report_analysis``
# finder-contract category, ``_finder_attribution`` observation, and
# ``_module_metadata``); keep in lockstep (asserted by ``test_schema_vocabulary``).
MechanismName = Literal[
    "unsafe_import_branch_exploration",
    "meta_path_changes",
    "meta_path_replacements",
    "import_searches",
    "finder_attribution",
    "finder_apis",
    "detailed_capture",
    "import_results",
    "import_calls",
    "path_finder_calls",
    "finder_result_analysis",
    "path_hooks_changes",
    "path_hooks_replacements",
    "sys_path_changes",
    "sys_path_replacements",
    "importer_cache_snapshots",
    "importer_cache_changes",
]
# Static completeness labels plus the three per-mechanism status vocabularies
# (PathFinderCaptureStatus, ImportResultsCaptureStatus, ImportCallsCaptureStatus in
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
    "active_exhaustive_direct_siblings",
    "partial_profiler_unavailable",
    "partial_capture_prerequisites_disabled",
]
FinderAPIObservationCategory = Literal["indeterminate", "legacy_only", "modern", "modern_and_legacy", "protocol_less"]
FinderAPIObservationKind = Literal["install", "replacement", "change"]
ModuleInspection = Literal["available", "unavailable"]
ModuleLoaderSource = Literal["spec", "module", "none"]
# Effective ``outcome`` vocabulary for detailed-diagnostic payloads. Mirrors
# ``ImportMechanismOutcome`` in ``_records``. ``FindingEvidenceJSON.outcome`` stays ``str``: it embeds the
# raising exception type (``raised:<type>``) and is not a closed vocabulary.
ImportMechanismOutcome = Literal[
    "started",
    "loaded",
    "failed",
    "found",
    "not_found",
    "returned",
    "raised",
    "unobserved_reentrant",
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


class ModuleSpecSnapshotJSON(TypedDict):
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


class RemoteAttachmentJSON(TypedDict):
    installed_at: str
    observation_boundary: str
    session_id: str
    transport: str


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
    remote_attachment: RemoteAttachmentJSON | None
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


class FinderAPIObservationJSON(TypedDict):
    category: FinderAPIObservationCategory
    id: str
    finder_id: str
    finder_type_name: str
    find_module: ProtocolJSON
    find_spec: ProtocolJSON
    observation: FinderAPIObservationKind
    observation_event_ref: str | None
    position: int


class ImportSearchJSON(TypedDict):
    id: str
    fullname: str
    start_event_ref: str
    evidence_event_refs: list[str]
    thread_id: int
    thread_name: str
    progress: ImportSearchProgress
    presence: ImportPresence


class StandardResolutionJSON(TypedDict):
    search_ref: str
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


class FinderResultJSON(TypedDict):
    id: str
    module: str
    kind: FinderResultKind
    purpose: FinderResultPurpose
    limitations: list[str]
    evidence_level: FinderResultEvidenceLevel
    state_phase: StatePhase
    predicts_alternative_winner: bool
    finder_type_name: str
    finder_id: str | None
    status: FinderResultStatus
    spec: ModuleSpecSnapshotJSON | None
    exception_type_name: str | None
    source_event_refs: list[str]
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


class FinderResultComparisonJSON(TypedDict):
    id: str
    left_result_ref: str
    right_result_ref: str
    complete: bool
    status_differs: bool
    loader_type_differs: bool | None
    origin_differs: bool | None
    cached_differs: bool | None
    package_status_differs: bool | None
    only_in_left_result: list[str]
    only_in_right_result: list[str]
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
# Every timeline event is ``{id, sequence, kind, data}``: a shared envelope plus a
# fully-typed, kind-specific ``data`` payload. ``kind`` is the discriminant for
# the ``EventDataJSON`` union. Each payload TypedDict is total; the matching
# ``_json_<kind>`` builder always emits every field.


class ImportSearchStartedDataJSON(TypedDict):
    evidence: Literal["resolution_started"]
    fullname: str | None
    importer_cache: ObjectSizeJSON | None
    search_ref: str
    meta_path: MetaPathStateJSON
    path_hooks_id: str | None
    thread_name: str
    thread_id: int


class ImportBranchExplorationStartedDataJSON(TypedDict):
    boundary: ImportBranchBoundary
    evidence: Literal["unsafe_import_branch_exploration"]
    fullname: str | None
    path: str | None
    trigger: ImportObjectJSON
    trigger_event_ref: str | None
    trigger_index: int
    trigger_outcome: ImportBranchTriggerOutcome
    thread_id: int
    thread_name: str


class ImportBranchExplorationCallDataJSON(TypedDict):
    boundary: ImportBranchBoundary
    candidate: ImportObjectJSON
    candidate_index: int
    evidence: Literal["unsafe_import_branch_exploration"]
    exception_type_name: str | None
    exploration_ref: str
    fullname: str | None
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: ImportBranchExplorationOutcome
    path: str | None
    returned_finder: ImportObjectJSON | None
    spec: ModuleSpecSnapshotJSON | None
    thread_id: int
    thread_name: str


class ImportMechanismCallDataJSON(TypedDict):
    boundary: ImportMechanismBoundary
    evidence: Literal["detailed_delegation"]
    exception_type_name: str | None
    fullname: str | None
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    object_id: str
    object_type_name: str
    outcome: ImportMechanismOutcome
    path: str | None
    returned_finder: ImportObjectJSON | None
    target_state: ModuleStateJSON | None
    thread_name: str
    thread_id: int


class ImportResultDataJSON(TypedDict):
    search_ref: str
    evidence: Literal["exact_import_boundary"]
    fullname: str
    outcome: ImportMechanismOutcome
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
    outcome: ImportMechanismOutcome
    thread_id: int
    thread_name: str


class PathFinderCallDataJSON(TypedDict):
    search_ref: str
    evidence: Literal["observed_path_finder_boundary"]
    finder_type_name: str
    fullname: str
    spec: ModuleSpecSnapshotJSON | None
    thread_id: int
    thread_name: str


class MetaPathFinderCallDataJSON(TypedDict):
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
    spec: ModuleSpecSnapshotJSON | None
    target_state: ModuleStateJSON | None
    thread_name: str
    thread_id: int


class ImporterCacheChangeDataJSON(TypedDict):
    added: list[ImporterCacheEntryJSON]
    non_string_keys_after: int
    non_string_keys_before: int
    observation: str
    removed: list[ImporterCacheEntryJSON]
    replaced: list[ImporterCacheReplacementJSON]
    thread_name: str


class MetaPathChangeDataJSON(TypedDict):
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


class PathHooksChangeDataJSON(TypedDict):
    added: list[ImportObjectJSON]
    contents_after: list[ImportObjectJSON]
    op: MutationOp
    removed: list[ImportObjectJSON]
    stack: list[FrameJSON]
    thread_name: str


class PathHooksReplacementDataJSON(TypedDict):
    during_import: str | None
    new_contents: list[ImportObjectJSON]
    old_contents: list[ImportObjectJSON]
    stack: list[FrameJSON]
    thread_name: str


class SysPathChangeDataJSON(TypedDict):
    added: list[str]
    contents_after: list[str]
    op: MutationOp
    removed: list[str]
    stack: list[FrameJSON]
    thread_name: str


class MonitoringErrorDataJSON(TypedDict):
    exception_type_name: str
    message: str | None
    where: str


EventDataJSON = (
    ImportBranchExplorationCallDataJSON
    | ImportBranchExplorationStartedDataJSON
    | ImportSearchStartedDataJSON
    | ImportMechanismCallDataJSON
    | ImportResultDataJSON
    | ImportCallDataJSON
    | PathFinderCallDataJSON
    | MetaPathFinderCallDataJSON
    | ImporterCacheChangeDataJSON
    | MetaPathChangeDataJSON
    | ReassignmentDataJSON
    | PathHooksChangeDataJSON
    | PathHooksReplacementDataJSON
    | SysPathChangeDataJSON
    | MonitoringErrorDataJSON
)


class EventJSON(TypedDict):
    id: str
    sequence: int
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
    spec: ModuleSpecSnapshotJSON | None
    spec_present: bool | None


class LoaderGroupJSON(TypedDict):
    loader: ImportObjectJSON | None
    modules: list[ModuleMetadataJSON]


class LoaderInventoryInfo(TypedDict):
    available: bool
    evidence: Literal["current_state"]
    groups: list[LoaderGroupJSON]
    non_string_keys_omitted: int
    phase: Literal["report"]
    unavailable: list[ModuleMetadataJSON]


class FindingEvidenceJSON(TypedDict, total=False):
    event_refs: list[str]
    level: FindingEvidenceLevel
    limitations: list[str]
    finder_call_status: str
    module_spec_status: str
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: str


class FindingSubjectJSON(TypedDict):
    kind: FindingSubjectKind
    value: str


class FindingFinderCallJSON(TypedDict):
    event_ref: str
    finder_id: str
    finder_type_name: str
    loader_type_name: str | None
    origin: str | None
    search_path: list[str]
    search_path_kind: SearchPathKind
    spec: ModuleSpecSnapshotJSON | None


class FindingImportMechanismCallJSON(TypedDict):
    boundary: ImportMechanismBoundary
    event_ref: str
    module_state_after: ModuleStateJSON | None
    module_state_before: ModuleStateJSON | None
    outcome: ImportMechanismOutcome
    target_state: ModuleStateJSON | None


# Findings share one envelope plus a `detail`-tagged `data` payload. Every
# `Finding.kind` maps to exactly one evidence family (see `_report_model`), so
# `data.detail` selects which variant-specific members are present instead of a
# flat `total=False` grab-bag on the finding itself.


class EventEvidenceJSON(TypedDict):
    detail: Literal["events_only"]


class CorrelationEvidenceJSON(TypedDict):
    detail: Literal["correlation"]
    search_refs: list[str]


class FinderAPIEvidenceJSON(TypedDict):
    detail: Literal["finder_api"]
    finder_api_ref: str


class FinderCallEvidenceJSON(TypedDict):
    detail: Literal["finder_call"]
    finder_call: FindingFinderCallJSON


class FinderComparisonEvidenceJSON(TypedDict):
    detail: Literal["result_comparison"]
    finder_call: FindingFinderCallJSON
    finder_result_refs: list[str]
    finder_result_comparison_ref: str
    structural_comparison: StructuralComparisonJSON


class ImportMechanismEvidenceJSON(TypedDict):
    detail: Literal["import_mechanism_call"]
    import_mechanism_call: FindingImportMechanismCallJSON
    module_state_baseline: ModuleStateJSON | None


FindingEvidenceDataJSON = (
    EventEvidenceJSON
    | CorrelationEvidenceJSON
    | FinderAPIEvidenceJSON
    | FinderCallEvidenceJSON
    | FinderComparisonEvidenceJSON
    | ImportMechanismEvidenceJSON
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
    standard_search_ref: str | None
    state_after: ModuleStateJSON | None
    state_before: ModuleStateJSON | None


class SeverityCountsJSON(TypedDict):
    problem: int
    risk: int
    note: int


class SummaryInfo(TypedDict):
    counts: SeverityCountsJSON
    unresolved_import_count: int
    top_finding_ref: str | None
    top_explanation_ref: str | None


class ProgramOutcomeJSON(TypedDict):
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
    monitoring_error_refs: list[str]
    report_errors: list[ReportErrorJSON]
    skipped_finders: list[SkippedFinderJSON]


class CheckRunJSON(TypedDict):
    kind: CheckKind
    status: CheckStatus
    unavailable_reasons: list[str]
    candidates: int
    results: int
    foreign_calls: int
    capacity: int | None
    omitted: int


class ReportJSON(TypedDict):
    """Complete schema 3.1 report document."""

    schema: SchemaVersion
    report_status: ReportStatus
    tool: ToolInfo
    generated_at: str
    process: ProcessInfo
    capture: CaptureInfo
    snapshots: list[SnapshotJSON]
    loader_inventory: LoaderInventoryInfo
    finder_apis: list[FinderAPIObservationJSON]
    import_searches: list[ImportSearchJSON]
    standard_resolutions: list[StandardResolutionJSON]
    finder_results: list[FinderResultJSON]
    finder_result_comparisons: list[FinderResultComparisonJSON]
    timeline: list[EventJSON]
    findings: list[FindingJSON]
    explanations: list[ExplanationJSON]
    summary: SummaryInfo
    checks: list[CheckRunJSON]
    program_outcome: ProgramOutcomeJSON | None
    diagnostics: DiagnosticsInfo


__all__ = ["ReportJSON", "ReportStatus"]
