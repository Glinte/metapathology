"""Immutable data model shared by report capture, analysis, and rendering."""

import os

from metapathology._module_metadata import ModuleMetadata
from metapathology._record import _Record
from metapathology._records import (
    FinderAPIObservation,
    ImporterCacheEntry,
    ImportMechanismCall,
    MetaPathFinderCall,
    ModuleCacheState,
    ModuleSpecSnapshot,
    MonitorEvent,
    ObjectIdentity,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    from metapathology._monitor_model import (
        ImportCallsCaptureStatus,
        ImportResultsCaptureStatus,
        PathFinderCaptureStatus,
        ProgramOutcomeKind,
        UnsafeImportBranchExplorationStatus,
    )
    from metapathology._records import ImportMechanismOutcome, SearchPathKind

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
    FindingDetail = Literal[
        "events_only",
        "correlation",
        "finder_api",
        "finder_call",
        "result_comparison",
        "import_mechanism_call",
    ]
    ImportPresence = Literal["unknown", "present_at_report", "absent_at_report"]
    ResolutionCategory = Literal["namespace", "built_in", "frozen", "source", "bytecode", "extension", "zip"]
    StatePhase = Literal["import", "report"]
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
    SearchPathPhase = Literal["import"]
    LocationsComparisonState = Literal[
        "not_applicable", "captured", "current_state", "deferred", "failed", "unavailable"
    ]
    ImportSearchProgress = ImportMechanismOutcome | Literal["unknown", "finder_found", "finder_raised"]
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


class ProgramOutcome(_Record):
    """Plain reduction of how the monitored program finished."""

    kind: "ProgramOutcomeKind"
    exception_type_name: str | None
    missing_module: str | None
    exit_code: int | None


class ReportSummary(_Record):
    """Counts and top references shared by the text verdict and JSON summary."""

    problem: int
    risk: int
    note: int
    unresolved_import_count: int
    top_finding_id: str | None
    top_explanation_id: str | None


class SkippedFinder(_Record):
    """Report-safe description of an uninstrumented finder."""

    finder_type_name: str
    reason: str
    expected: bool


class LoaderInventory(_Record):
    """Current metadata copied from one ``sys.modules`` snapshot."""

    available: bool
    entries: tuple[ModuleMetadata, ...]
    non_string_keys: int


class ImportSearch(_Record):
    """Conservative report-time correlation of one observed import search."""

    search_id: int
    fullname: str
    start_event_seq: int
    event_seqs: tuple[int, ...]
    thread_id: int
    thread_name: str
    progress: "ImportSearchProgress"
    presence: "ImportPresence"


class StandardResolution(_Record):
    """Captured or conservatively inferred standard resolution evidence."""

    search_id: int
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


class FinderResult(_Record):
    """One observed or current-state result for a module search."""

    result_id: str
    module: str
    kind: "FinderResultKind"
    purpose: "FinderResultPurpose"
    limitations: tuple[str, ...]
    evidence_level: "FinderResultEvidenceLevel"
    state_phase: "StatePhase"
    predicts_alternative_winner: bool
    finder_type_name: str
    finder_id: int | None
    status: "FinderResultStatus"
    spec_summary: ModuleSpecSnapshot | None
    exception_type_name: str | None
    source_event_seqs: tuple[int, ...]
    search_path: tuple[str, ...]
    search_path_kind: "SearchPathKind"
    search_path_phase: "SearchPathPhase"
    signals: tuple[str, ...] = ()

    @classmethod
    def _for_comparison(
        cls,
        result_id: str,
        summary: ModuleSpecSnapshot | None,
        *,
        status: "FinderResultStatus" = "found",
    ) -> "FinderResult":
        """Construct the minimal synthetic result used by focused value tests."""
        return cls(
            result_id=result_id,
            module="test",
            kind="observed_finder_result",
            purpose="record_custom_finder_result",
            limitations=(),
            evidence_level="observed",
            state_phase="import",
            predicts_alternative_winner=False,
            finder_type_name="test",
            finder_id=None,
            status=status,
            spec_summary=summary,
            exception_type_name=None,
            source_event_seqs=(),
            search_path=(),
            search_path_kind="sys_path",
            search_path_phase="import",
        )

    def compare(
        self,
        other: "FinderResult",
        *,
        comparison_id: str,
        structural_comparison: "StructuralComparison",
    ) -> "FinderResultComparison":
        """Compare two independent results without assigning either authority."""
        left = self.spec_summary
        right = other.spec_summary
        if left is None or right is None:
            return _incomplete_result_comparison(comparison_id, self, other, structural_comparison)
        location_difference = _compare_locations(left, right)
        return FinderResultComparison(
            comparison_id=comparison_id,
            left_result_id=self.result_id,
            right_result_id=other.result_id,
            status_differs=self.status != other.status,
            complete=_comparison_is_complete(left, right),
            loader_type_differs=_loader_difference(left, right),
            origin_differs=_safe_path_value_changed(left.origin, right.origin),
            cached_differs=_safe_path_value_changed(left.cached, right.cached),
            package_status_differs=(
                None if left.is_package is None or right.is_package is None else left.is_package != right.is_package
            ),
            only_in_left_result=location_difference[0],
            only_in_right_result=location_difference[1],
            locations_reordered=location_difference[2],
            left_locations_state=left.locations_state,
            right_locations_state=right.locations_state,
            structural_comparison=structural_comparison,
        )


class StructuralComparison(_Record):
    """Identity-only comparison of install and report-time import structure."""

    path_hooks_changed: bool | None
    importer_cache_changed: bool | None
    importer_cache_changed_paths: tuple[str, ...]
    importer_cache_event_seqs: tuple[int, ...]
    evidence_level: 'Literal["structural_comparison"]' = "structural_comparison"


class FinderResultComparison(_Record):
    """Neutral semantic differences between two finder results."""

    comparison_id: str
    left_result_id: str
    right_result_id: str
    status_differs: bool
    complete: bool
    loader_type_differs: bool | None
    origin_differs: bool | None
    cached_differs: bool | None
    package_status_differs: bool | None
    only_in_left_result: tuple[str, ...]
    only_in_right_result: tuple[str, ...]
    locations_reordered: bool | None
    left_locations_state: "LocationsComparisonState"
    right_locations_state: "LocationsComparisonState"
    structural_comparison: StructuralComparison

    def has_differences(self) -> bool:
        """Return whether the comparison contains any observed difference."""
        return any(
            (
                self.status_differs,
                self.loader_type_differs is True,
                self.origin_differs is True,
                self.cached_differs is True,
                self.package_status_differs is True,
                bool(self.only_in_left_result),
                bool(self.only_in_right_result),
                self.locations_reordered is True,
            )
        )


def _incomplete_result_comparison(
    comparison_id: str,
    left: FinderResult,
    right: FinderResult,
    structural_comparison: StructuralComparison,
) -> FinderResultComparison:
    return FinderResultComparison(
        comparison_id,
        left.result_id,
        right.result_id,
        left.status != right.status,
        False,
        None,
        None,
        None,
        None,
        (),
        (),
        None,
        "unavailable" if left.spec_summary is None else left.spec_summary.locations_state,
        "unavailable" if right.spec_summary is None else right.spec_summary.locations_state,
        structural_comparison,
    )


def _loader_difference(left: ModuleSpecSnapshot, right: ModuleSpecSnapshot) -> bool | None:
    if "loader:missing" in left.unavailable_fields or "loader:missing" in right.unavailable_fields:
        return None
    left_type = None if left.loader is None else left.loader.type_name
    right_type = None if right.loader is None else right.loader.type_name
    return left_type != right_type


def _safe_path_value_changed(
    left: str | ObjectIdentity | None,
    right: str | ObjectIdentity | None,
) -> bool | None:
    if type(left) is str and type(right) is str:
        return _path_key(left) != _path_key(right)
    if left is None and right is None:
        return False
    if isinstance(left, ObjectIdentity) or isinstance(right, ObjectIdentity):
        return None
    return True


def _comparison_is_complete(left: ModuleSpecSnapshot, right: ModuleSpecSnapshot) -> bool:
    incomplete_location_states = ("deferred", "failed")
    locations_complete = not (left.is_package is True and left.locations_state in incomplete_location_states)
    locations_complete = locations_complete and not (
        right.is_package is True and right.locations_state in incomplete_location_states
    )
    return not left.unavailable_fields and not right.unavailable_fields and locations_complete


def _compare_locations(
    left: ModuleSpecSnapshot,
    right: ModuleSpecSnapshot,
) -> tuple[tuple[str, ...], tuple[str, ...], bool | None]:
    left_locations = _string_locations(left)
    right_locations = _string_locations(right)
    if left_locations is None or right_locations is None:
        return (), (), None
    left_keys = tuple(_path_key(path) for path in left_locations)
    right_keys = tuple(_path_key(path) for path in right_locations)
    only_in_right, only_in_left = _location_delta(left_locations, left_keys, right_locations, right_keys)
    reordered = not only_in_right and not only_in_left and left_keys != right_keys
    return only_in_left, only_in_right, reordered


def _string_locations(summary: ModuleSpecSnapshot) -> tuple[str, ...] | None:
    locations = summary.submodule_search_locations
    if locations is None or any(type(location) is not str for location in locations):
        return None
    return tuple(location for location in locations if type(location) is str)


def _location_delta(
    left: tuple[str, ...],
    left_keys: tuple[str, ...],
    right: tuple[str, ...],
    right_keys: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return right-only then left-only multiset differences in display order."""
    unmatched_left = list(zip(left_keys, left, strict=True))
    only_in_right: list[str] = []
    for right_key, right_path in zip(right_keys, right, strict=True):
        for index, (left_key, _left_path) in enumerate(unmatched_left):
            if right_key == left_key:
                del unmatched_left[index]
                break
        else:
            only_in_right.append(right_path)
    return tuple(only_in_right), tuple(path for _key, path in unmatched_left)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


class EventEvidence(_Record):
    """Finding carrying no structured evidence beyond its supporting events."""

    detail: 'Literal["events_only"]' = "events_only"


class CorrelationEvidence(_Record):
    """Finding correlated across several import searches."""

    search_ids: tuple[int, ...]
    detail: 'Literal["correlation"]' = "correlation"


class FinderAPIEvidence(_Record):
    """Finding backed by one observed finder API."""

    finder_api: FinderAPIObservation
    detail: 'Literal["finder_api"]' = "finder_api"


class FinderCallEvidence(_Record):
    """Finding backed by one observed ``find_spec`` call."""

    finder_call: MetaPathFinderCall
    detail: 'Literal["finder_call"]' = "finder_call"


class FinderComparisonEvidence(_Record):
    """Finding backed by an observed finder call and current-state comparison."""

    finder_call: MetaPathFinderCall
    result_ids: tuple[str, ...]
    result_comparison_id: str
    structural_comparison: StructuralComparison
    detail: 'Literal["result_comparison"]' = "result_comparison"


class ImportMechanismEvidence(_Record):
    """Finding backed by one detailed import-mechanism call."""

    import_mechanism_call: ImportMechanismCall
    module_state_baseline: ModuleCacheState | None = None
    detail: 'Literal["import_mechanism_call"]' = "import_mechanism_call"


# Evidence variants are a total function of ``Finding.kind``: each kind always
# builds exactly one variant (see ``_report_analysis``). Consumers narrow on the
# variant type instead of re-checking optional fields.
FindingEvidence = (
    EventEvidence
    | CorrelationEvidence
    | FinderAPIEvidence
    | FinderCallEvidence
    | FinderComparisonEvidence
    | ImportMechanismEvidence
)


class Finding(_Record):
    """Structured evidence for one human or machine-readable finding."""

    finding_id: str
    kind: "FindingKind"
    module: str
    evidence: FindingEvidence = EventEvidence()
    evidence_level: "FindingEvidenceLevel" = "current_state"
    limitations: tuple[str, ...] = ()
    subject_kind: "FindingSubjectKind" = "module"
    supporting_event_seqs: tuple[int, ...] = ()
    severity: "FindingSeverity" = "risk"
    signals: tuple[str, ...] = ()


def finding_finder_call(finding: "Finding") -> MetaPathFinderCall | None:
    """Return the observed finder call carried by a finding."""
    evidence = finding.evidence
    if isinstance(evidence, (FinderCallEvidence, FinderComparisonEvidence)):
        return evidence.finder_call
    return None


def finding_import_mechanism_call(finding: "Finding") -> ImportMechanismCall | None:
    """Return the detailed import-mechanism call carried by a finding."""
    evidence = finding.evidence
    return evidence.import_mechanism_call if isinstance(evidence, ImportMechanismEvidence) else None


def finding_structural_comparison(finding: "Finding") -> StructuralComparison | None:
    """Return the structural comparison a finding carries, if any."""
    evidence = finding.evidence
    return evidence.structural_comparison if isinstance(evidence, FinderComparisonEvidence) else None


def finding_result_comparison_id(finding: "Finding") -> str | None:
    """Return the finder-result comparison id referenced by a finding."""
    evidence = finding.evidence
    return evidence.result_comparison_id if isinstance(evidence, FinderComparisonEvidence) else None


def finding_result_ids(finding: "Finding") -> tuple[str, ...]:
    """Return the finder-result ids referenced by a finding."""
    evidence = finding.evidence
    return evidence.result_ids if isinstance(evidence, FinderComparisonEvidence) else ()


def finding_finder_api(finding: "Finding") -> FinderAPIObservation | None:
    """Return the finder API evidence carried by a finding."""
    evidence = finding.evidence
    return evidence.finder_api if isinstance(evidence, FinderAPIEvidence) else None


def finding_search_ids(finding: "Finding") -> tuple[int, ...]:
    """Return the correlated import-search ids carried by a finding."""
    evidence = finding.evidence
    return evidence.search_ids if isinstance(evidence, CorrelationEvidence) else ()


def finding_module_state_baseline(finding: "Finding") -> ModuleCacheState | None:
    """Return the detailed-call module-state baseline a finding carries, if any."""
    evidence = finding.evidence
    return evidence.module_state_baseline if isinstance(evidence, ImportMechanismEvidence) else None


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
    standard_search_id: int | None = None
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


class ProcessInfo(_Record):
    """Host-process identity shared by the text header and JSON ``process`` block."""

    generated_at: str
    cwd: str | None
    argv: tuple[str, ...]


class CaptureInfo(_Record):
    """Monitor-lifetime facts that are not per-mechanism snapshots."""

    cutoff_seq: int
    monitor_enabled: bool
    import_audit_enabled: bool
    meta_path_enabled: bool
    finder_attribution_enabled: bool
    baseline_module_count: int
    modules_since_install: tuple[str, ...] | None
    early_site_bootstrap: EarlySiteBootstrap | None
    frozen_bootstrap: FrozenBootstrap | None
    detailed_capture: tuple[str, ...]
    import_results_capture_status: "ImportResultsCaptureStatus"
    import_calls_capture_status: "ImportCallsCaptureStatus"
    path_finder_capture_status: "PathFinderCaptureStatus"
    unsafe_import_branch_exploration_status: "UnsafeImportBranchExplorationStatus"


class MetaPathSnapshot(_Record):
    """Install-time and report-time ``sys.meta_path`` entry names.

    The snapshots remain useful context when list observation is disabled;
    ``enabled`` says whether mutations were observed and ``current`` is None
    only when the report-time snapshot could not be copied.
    """

    enabled: bool
    initial: tuple[str, ...]
    current: tuple[str, ...] | None


class PathHooksSnapshot(_Record):
    """Install-time and report-time ``sys.path_hooks`` identities."""

    enabled: bool
    initial: tuple[ObjectIdentity, ...]
    current: tuple[ObjectIdentity, ...] | None


class ImporterCacheSnapshot(_Record):
    """Install-time and report-time ``sys.path_importer_cache`` state."""

    enabled: bool
    initial: tuple[ImporterCacheEntry, ...]
    initial_non_string_keys: int
    current: tuple[ImporterCacheEntry, ...] | None
    current_non_string_keys: int | None
    observations: int
    coalesced: int


class CheckRun(_Record):
    """Resource and availability summary for one report-time check kind."""

    kind: "CheckKind"
    status: "CheckStatus"
    unavailable_reasons: tuple[str, ...]
    candidates: int
    results: int
    foreign_calls: int
    capacity: int | None
    omitted: int


class AnalysisResult(_Record):
    """Everything derived at report time from the copied event log and state."""

    searches: tuple[ImportSearch, ...]
    events: tuple[MonitorEvent, ...]
    findings: tuple[Finding, ...]
    explanations: tuple[CausalExplanation, ...]
    finder_results: tuple[FinderResult, ...]
    finder_result_comparisons: tuple[FinderResultComparison, ...]
    standard_resolutions: tuple[StandardResolution, ...]
    finder_apis: tuple[FinderAPIObservation, ...]
    loader_inventory: LoaderInventory
    skipped_finders: tuple[SkippedFinder, ...]
    summary: ReportSummary
    program_outcome: ProgramOutcome | None
    report_errors: tuple[ReportError, ...]
    checks: tuple[CheckRun, ...] = ()


class ReportDocument(_Record):
    """One sequence-cutoff snapshot shared by all report renderers."""

    process: ProcessInfo
    capture: CaptureInfo
    meta_path: MetaPathSnapshot
    path_hooks: PathHooksSnapshot
    importer_cache: ImporterCacheSnapshot
    sys_path_enabled: bool
    analysis: AnalysisResult
