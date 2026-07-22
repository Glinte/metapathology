"""Immutable data model shared by report capture, analysis, and rendering."""

import os

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
    SpeculativeReplay,
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
    FindingDetail = Literal["bare", "correlation", "contract", "claim", "route", "deep_call"]
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

    @classmethod
    def _for_comparison(
        cls,
        route_id: str,
        summary: SpecSummary | None,
        *,
        status: "RouteStatus" = "found",
    ) -> "ResolutionRoute":
        """Construct the minimal synthetic route used by focused value tests."""
        return cls(
            route_id=route_id,
            module="test",
            kind="captured_claim",
            purpose="record_selected_custom_meta_path_route",
            limitations=(),
            evidence_level="captured",
            state_phase="import",
            predicts_alternative_winner=False,
            finder_type_name="test",
            finder_id=None,
            status=status,
            spec_summary=summary,
            exception_type_name=None,
            event_seq=None,
            search_path=(),
            search_path_kind="sys_path",
            search_path_phase="import",
        )

    def compare(
        self,
        other: "ResolutionRoute",
        *,
        comparison_id: str,
        structural_comparison: "StructuralComparison",
    ) -> "RouteComparison":
        """Compare two independent routes without assigning either authority."""
        left = self.spec_summary
        right = other.spec_summary
        if left is None or right is None:
            return _incomplete_route_comparison(comparison_id, self, other, structural_comparison)
        location_difference = _compare_locations(left, right)
        return RouteComparison(
            comparison_id=comparison_id,
            left_route_id=self.route_id,
            right_route_id=other.route_id,
            status_differs=self.status != other.status,
            complete=_comparison_is_complete(left, right),
            loader_type_differs=_loader_difference(left, right),
            origin_differs=_safe_path_value_changed(left.origin, right.origin),
            cached_differs=_safe_path_value_changed(left.cached, right.cached),
            package_status_differs=(
                None if left.is_package is None or right.is_package is None else left.is_package != right.is_package
            ),
            only_in_left_route=location_difference[0],
            only_in_right_route=location_difference[1],
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

    def has_differences(self) -> bool:
        """Return whether the comparison contains any observed difference."""
        return any(
            (
                self.status_differs,
                self.loader_type_differs is True,
                self.origin_differs is True,
                self.cached_differs is True,
                self.package_status_differs is True,
                bool(self.only_in_left_route),
                bool(self.only_in_right_route),
                self.locations_reordered is True,
            )
        )


def _incomplete_route_comparison(
    comparison_id: str,
    left: ResolutionRoute,
    right: ResolutionRoute,
    structural_comparison: StructuralComparison,
) -> RouteComparison:
    return RouteComparison(
        comparison_id,
        left.route_id,
        right.route_id,
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


def _loader_difference(left: SpecSummary, right: SpecSummary) -> bool | None:
    if "loader:missing" in left.unavailable_fields or "loader:missing" in right.unavailable_fields:
        return None
    left_type = None if left.loader is None else left.loader.type_name
    right_type = None if right.loader is None else right.loader.type_name
    return left_type != right_type


def _safe_path_value_changed(
    left: str | ObjectRef | None,
    right: str | ObjectRef | None,
) -> bool | None:
    if type(left) is str and type(right) is str:
        return _path_key(left) != _path_key(right)
    if left is None and right is None:
        return False
    if isinstance(left, ObjectRef) or isinstance(right, ObjectRef):
        return None
    return True


def _comparison_is_complete(left: SpecSummary, right: SpecSummary) -> bool:
    incomplete_location_states = ("deferred", "failed")
    locations_complete = not (left.is_package is True and left.locations_state in incomplete_location_states)
    locations_complete = locations_complete and not (
        right.is_package is True and right.locations_state in incomplete_location_states
    )
    return not left.unavailable_fields and not right.unavailable_fields and locations_complete


def _compare_locations(
    left: SpecSummary,
    right: SpecSummary,
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


def _string_locations(summary: SpecSummary) -> tuple[str, ...] | None:
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


class BareEvidence(_Record):
    """Finding carrying no structured evidence beyond its supporting events."""

    detail: 'Literal["bare"]' = "bare"


class CorrelationEvidence(_Record):
    """Finding correlated across several import attempts by shared route."""

    attempt_ids: tuple[int, ...]
    detail: 'Literal["correlation"]' = "correlation"


class ContractEvidence(_Record):
    """Finding backed by one observed finder contract."""

    finder_contract: FinderContract
    detail: 'Literal["contract"]' = "contract"


class ClaimEvidence(_Record):
    """Finding backed by one captured ``find_spec`` claim."""

    claim: FindSpecCall
    detail: 'Literal["claim"]' = "claim"


class RouteEvidence(_Record):
    """Finding backed by a captured claim compared against a probed route."""

    claim: FindSpecCall
    route_ids: tuple[str, ...]
    route_comparison_id: str
    structural_comparison: StructuralComparison
    detail: 'Literal["route"]' = "route"


class DeepCallEvidence(_Record):
    """Finding backed by one deep-diagnostic delegation boundary."""

    deep_call: DeepDiagnosticCall
    module_state_baseline: ModuleCacheState | None = None
    detail: 'Literal["deep_call"]' = "deep_call"


# Evidence variants are a total function of ``Finding.kind``: each kind always
# builds exactly one variant (see ``_report_analysis``). Consumers narrow on the
# variant type instead of re-checking optional fields.
FindingEvidence = (
    BareEvidence | CorrelationEvidence | ContractEvidence | ClaimEvidence | RouteEvidence | DeepCallEvidence
)


class Finding(_Record):
    """Structured evidence for one human or machine-readable finding."""

    finding_id: str
    kind: "FindingKind"
    module: str
    evidence: FindingEvidence = BareEvidence()
    evidence_level: "FindingEvidenceLevel" = "post_hoc"
    limitations: tuple[str, ...] = ()
    subject_kind: "FindingSubjectKind" = "module"
    supporting_event_seqs: tuple[int, ...] = ()
    severity: "FindingSeverity" = "warning"
    signals: tuple[str, ...] = ()


def finding_claim(finding: "Finding") -> FindSpecCall | None:
    """Return the captured claim a finding carries, regardless of its kind."""
    evidence = finding.evidence
    if isinstance(evidence, (ClaimEvidence, RouteEvidence)):
        return evidence.claim
    return None


def finding_deep_call(finding: "Finding") -> DeepDiagnosticCall | None:
    """Return the deep-diagnostic boundary a finding carries, if any."""
    evidence = finding.evidence
    return evidence.deep_call if isinstance(evidence, DeepCallEvidence) else None


def finding_structural_comparison(finding: "Finding") -> StructuralComparison | None:
    """Return the structural comparison a finding carries, if any."""
    evidence = finding.evidence
    return evidence.structural_comparison if isinstance(evidence, RouteEvidence) else None


def finding_route_comparison_id(finding: "Finding") -> str | None:
    """Return the route-comparison id a finding references, if any."""
    evidence = finding.evidence
    return evidence.route_comparison_id if isinstance(evidence, RouteEvidence) else None


def finding_route_ids(finding: "Finding") -> tuple[str, ...]:
    """Return the route ids a finding references, if any."""
    evidence = finding.evidence
    return evidence.route_ids if isinstance(evidence, RouteEvidence) else ()


def finding_finder_contract(finding: "Finding") -> FinderContract | None:
    """Return the finder contract a finding carries, if any."""
    evidence = finding.evidence
    return evidence.finder_contract if isinstance(evidence, ContractEvidence) else None


def finding_attempt_ids(finding: "Finding") -> tuple[int, ...]:
    """Return the correlated attempt ids a finding carries, if any."""
    evidence = finding.evidence
    return evidence.attempt_ids if isinstance(evidence, CorrelationEvidence) else ()


def finding_module_state_baseline(finding: "Finding") -> ModuleCacheState | None:
    """Return the deep-call module-state baseline a finding carries, if any."""
    evidence = finding.evidence
    return evidence.module_state_baseline if isinstance(evidence, DeepCallEvidence) else None


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


class ProcessInfo(_Record):
    """Host-process identity shared by the text header and JSON ``process`` block."""

    generated_at: str
    cwd: str | None
    argv: tuple[str, ...]


class CaptureInfo(_Record):
    """Monitor-lifetime facts that are not per-mechanism snapshots."""

    cutoff_seq: int
    monitor_enabled: bool
    baseline_module_count: int
    modules_since_install: tuple[str, ...] | None
    early_site_bootstrap: EarlySiteBootstrap | None
    frozen_bootstrap: FrozenBootstrap | None
    deep_diagnostics: tuple[str, ...]
    deep_import_outcomes_status: "DeepImportOutcomesStatus"
    deep_import_calls_status: "DeepImportCallsStatus"
    standard_finder_status: "StandardFinderStatus"


class MetaPathSnapshot(_Record):
    """Install-time and report-time ``sys.meta_path`` entry names.

    ``sys.meta_path`` is always monitored when the monitor is enabled, so this
    record carries no ``enabled`` flag; ``current`` is None only when the
    report-time snapshot could not be copied.
    """

    initial: tuple[str, ...]
    current: tuple[str, ...] | None


class PathHooksSnapshot(_Record):
    """Install-time and report-time ``sys.path_hooks`` identities."""

    enabled: bool
    initial: tuple[ObjectRef, ...]
    current: tuple[ObjectRef, ...] | None


class ImporterCacheSnapshot(_Record):
    """Install-time and report-time ``sys.path_importer_cache`` state."""

    enabled: bool
    initial: tuple[ImporterCacheEntry, ...]
    initial_non_string_keys: int
    current: tuple[ImporterCacheEntry, ...] | None
    current_non_string_keys: int | None
    observations: int
    coalesced: int


class AnalysisResult(_Record):
    """Everything derived at report time from the copied event log and state."""

    attempts: tuple[ImportAttempt, ...]
    events: tuple[MonitorEvent, ...]
    findings: tuple[Finding, ...]
    explanations: tuple[CausalExplanation, ...]
    resolution_routes: tuple[ResolutionRoute, ...]
    route_comparisons: tuple[RouteComparison, ...]
    standard_resolutions: tuple[StandardResolution, ...]
    finder_contracts: tuple[FinderContract, ...]
    loader_inventory: LoaderInventory
    skipped_finders: tuple[SkippedFinder, ...]
    summary: ReportSummary
    target_outcome: TargetOutcome | None
    report_errors: tuple[ReportError, ...]
    speculative_replay_enabled: bool = False
    speculative_replays: tuple[SpeculativeReplay, ...] = ()
    speculative_replays_omitted: int = 0


class ReportDocument(_Record):
    """One sequence-cutoff snapshot shared by all report renderers."""

    process: ProcessInfo
    capture: CaptureInfo
    meta_path: MetaPathSnapshot
    path_hooks: PathHooksSnapshot
    importer_cache: ImporterCacheSnapshot
    sys_path_enabled: bool
    analysis: AnalysisResult
