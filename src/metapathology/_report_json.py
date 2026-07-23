"""Stable machine-readable projection of a report document.

This module is a leaf stage: it consumes captured and derived report models,
never inspects import state, and projects them onto the versioned JSON schema.
"""

import os
import sys
import time

from metapathology import __version__
from metapathology._module_metadata import ModuleMetadata
from metapathology._records import (
    FinderAPIObservation,
    ImportBranchExplorationCall,
    ImportBranchExplorationStarted,
    ImportCall,
    ImporterCacheChange,
    ImporterCacheEntry,
    ImporterCacheReplacement,
    ImportMechanismCall,
    ImportResult,
    ImportSearchStarted,
    MetaPathChange,
    MetaPathFinderCall,
    MetaPathReplacement,
    ModuleCacheState,
    ModuleSpecSnapshot,
    MonitorEvent,
    MonitoringError,
    ObjectIdentity,
    PathFinderCall,
    PathHooksChange,
    PathHooksReplacement,
    SysPathChange,
    SysPathReplacement,
)
from metapathology._report_analysis import _finder_api_category
from metapathology._report_events import EVENT_COUNT_KEY, EVENT_KIND
from metapathology._report_model import (
    CausalExplanation,
    CheckRun,
    CorrelationEvidence,
    EarlySiteBootstrap,
    FinderAPIEvidence,
    FinderCallEvidence,
    FinderComparisonEvidence,
    FinderResult,
    FinderResultComparison,
    Finding,
    FrozenBootstrap,
    ImportMechanismEvidence,
    ImportSearch,
    LoaderInventory,
    ProgramOutcome,
    ReportDocument,
    ReportSummary,
    StandardResolution,
    StructuralComparison,
    finding_finder_api,
    finding_finder_call,
    finding_import_mechanism_call,
    finding_structural_comparison,
)
from metapathology._report_schema import (
    CheckRunJSON,
    DiagnosticsInfo,
    EarlySiteBootstrapJSON,
    EventDataJSON,
    EventJSON,
    ExplanationJSON,
    FinderAPIObservationJSON,
    FinderResultComparisonJSON,
    FinderResultJSON,
    FindingEvidenceDataJSON,
    FindingEvidenceJSON,
    FindingFinderCallJSON,
    FindingImportMechanismCallJSON,
    FindingJSON,
    FrameJSON,
    FrozenBootstrapJSON,
    ImportBranchExplorationCallDataJSON,
    ImportBranchExplorationStartedDataJSON,
    ImportCallDataJSON,
    ImporterCacheChangeDataJSON,
    ImporterCacheEntryJSON,
    ImporterCacheReplacementJSON,
    ImportMechanismCallDataJSON,
    ImportObjectJSON,
    ImportObjectValueJSON,
    ImportResultDataJSON,
    ImportSearchJSON,
    ImportSearchStartedDataJSON,
    LoaderGroupJSON,
    LoaderInventoryInfo,
    MechanismCompleteness,
    MechanismJSON,
    MechanismName,
    MetaPathChangeDataJSON,
    MetaPathFinderCallDataJSON,
    ModuleMetadataJSON,
    ModuleSpecSnapshotJSON,
    ModuleStateJSON,
    MonitoringErrorDataJSON,
    PathFinderCallDataJSON,
    PathHooksChangeDataJSON,
    PathHooksReplacementDataJSON,
    ProcessInfo,
    ProgramOutcomeJSON,
    ReassignmentDataJSON,
    ReportJSON,
    ReportStatus,
    SnapshotJSON,
    SpecValueJSON,
    StandardResolutionJSON,
    StructuralComparisonJSON,
    SummaryInfo,
    SysPathChangeDataJSON,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from traceback import FrameSummary

    from metapathology._report_events import EventCountKind


_SCHEMA_NAME = "metapathology.report"
_SCHEMA_MAJOR = 3
_SCHEMA_MINOR = 1


def _json_events(
    events: "Iterable[MonitorEvent]",
) -> "tuple[list[EventJSON], dict[EventCountKind, int], list[str]]":
    """Serialize and aggregate the exhaustive event log in one traversal."""
    counts: dict[EventCountKind, int] = {
        "audit_starts": 0,
        "calls": 0,
        "import_mechanism_calls": 0,
        "import_results": 0,
        "import_calls": 0,
        "importer_cache_changes": 0,
        "meta_path_changes": 0,
        "path_hooks_changes": 0,
        "path_hooks_replacements": 0,
        "meta_path_replacements": 0,
        "path_finder_calls": 0,
        "sys_path_changes": 0,
        "sys_path_replacements": 0,
        "import_branch_exploration_starts": 0,
        "import_branch_exploration_calls": 0,
    }
    timeline: list[EventJSON] = []
    monitoring_error_refs: list[str] = []
    for event in events:
        timeline.append(_json_event(event))
        count_key = EVENT_COUNT_KEY.get(type(event))
        if count_key is not None:
            counts[count_key] += 1
        elif isinstance(event, MonitoringError):
            monitoring_error_refs.append(f"event:{event.sequence}")
    return timeline, counts, monitoring_error_refs


def json_document(document: ReportDocument) -> ReportJSON:
    """Project one report document onto the stable JSON schema."""
    return _build_json_document(document)


def _build_json_document(document: ReportDocument) -> ReportJSON:
    """Project one report document onto the stable JSON schema."""
    timeline, event_counts, monitoring_error_refs = _json_events(document.analysis.events)
    report_status: ReportStatus = "partial" if document.analysis.report_errors or monitoring_error_refs else "complete"
    return {
        "schema": {"major": _SCHEMA_MAJOR, "minor": _SCHEMA_MINOR, "name": _SCHEMA_NAME},
        "report_status": report_status,
        "tool": {"name": "metapathology", "version": __version__},
        "generated_at": document.process.generated_at,
        "process": _json_process(document),
        "capture": {
            "baseline_module_count": document.capture.baseline_module_count,
            "cutoff_seq": document.capture.cutoff_seq,
            "early_site_bootstrap": _json_early_site_bootstrap(document.capture.early_site_bootstrap),
            "frozen_bootstrap": _json_frozen_bootstrap(document.capture.frozen_bootstrap),
            "enabled": document.capture.monitor_enabled,
            "mechanisms": _json_mechanisms(document, event_counts),
            "modules_since_install": None
            if document.capture.modules_since_install is None
            else list(document.capture.modules_since_install),
        },
        "snapshots": _json_snapshots(document),
        "loader_inventory": _json_loader_inventory(document.analysis.loader_inventory),
        "finder_apis": [_json_finder_api(observation) for observation in document.analysis.finder_apis],
        "import_searches": [_json_import_search(search) for search in document.analysis.searches],
        "standard_resolutions": [
            _json_standard_resolution(resolution) for resolution in document.analysis.standard_resolutions
        ],
        "finder_results": [_json_resolution_result(result) for result in document.analysis.finder_results],
        "finder_result_comparisons": [
            _json_result_comparison(comparison) for comparison in document.analysis.finder_result_comparisons
        ],
        "timeline": timeline,
        "findings": [_json_finding(finding) for finding in document.analysis.findings],
        "explanations": [_json_explanation(explanation) for explanation in document.analysis.explanations],
        "summary": _json_summary(document.analysis.summary),
        "checks": [_json_check_run(run) for run in document.analysis.checks],
        "program_outcome": _json_program_outcome(document.analysis.program_outcome),
        "diagnostics": _json_diagnostics(document, monitoring_error_refs),
    }


def _json_process(document: ReportDocument) -> ProcessInfo:
    """Project process metadata that is completed from the current interpreter."""
    version = sys.version_info
    return {
        "argv": list(document.process.argv),
        "cwd": document.process.cwd,
        "executable": sys.executable if isinstance(sys.executable, str) else None,
        "implementation": sys.implementation.name,
        "parent_pid": os.getppid(),
        "pid": os.getpid(),
        "platform": sys.platform,
        "python_version": f"{version.major}.{version.minor}.{version.micro}",
    }


def _json_mechanisms(
    document: ReportDocument,
    event_counts: "dict[EventCountKind, int]",
) -> list[MechanismJSON]:
    """Describe the independently enabled capture mechanisms."""
    return [
        _mechanism(
            "meta_path_changes", document.capture.meta_path_enabled, event_counts["meta_path_changes"], "best_effort"
        ),
        _mechanism(
            "meta_path_replacements",
            document.capture.meta_path_enabled and document.capture.import_audit_enabled,
            event_counts["meta_path_replacements"],
            "import_boundaries",
        ),
        _mechanism(
            "import_searches",
            document.capture.import_audit_enabled,
            event_counts["audit_starts"],
            "resolution_starts",
        ),
        _mechanism(
            "finder_attribution",
            document.capture.finder_attribution_enabled,
            event_counts["calls"],
            "instrumented_finders",
        ),
        _mechanism(
            "finder_apis",
            document.capture.finder_attribution_enabled,
            len(document.analysis.finder_apis),
            "first_observation_per_identity",
        ),
        _mechanism(
            "detailed_capture",
            bool(document.capture.detailed_capture),
            event_counts["import_mechanism_calls"],
            "delegated_boundaries",
        ),
        _mechanism(
            "import_results",
            "import_results" in document.capture.detailed_capture,
            event_counts["import_results"],
            document.capture.import_results_capture_status,
        ),
        _mechanism(
            "import_calls",
            "import_calls" in document.capture.detailed_capture,
            event_counts["import_calls"],
            document.capture.import_calls_capture_status,
        ),
        _mechanism(
            "path_finder_calls",
            document.capture.path_finder_capture_status.startswith("active_"),
            event_counts["path_finder_calls"],
            document.capture.path_finder_capture_status,
        ),
        _mechanism(
            "unsafe_import_branch_exploration",
            document.capture.unsafe_import_branch_exploration_status.startswith(("active_", "partial_")),
            event_counts["import_branch_exploration_calls"],
            document.capture.unsafe_import_branch_exploration_status,
        ),
        _result_analysis_mechanism(document),
        _mechanism(
            "path_hooks_changes", document.path_hooks.enabled, event_counts["path_hooks_changes"], "best_effort"
        ),
        _mechanism(
            "path_hooks_replacements",
            document.path_hooks.enabled and document.capture.import_audit_enabled,
            event_counts["path_hooks_replacements"],
            "import_boundaries",
        ),
        _mechanism("sys_path_changes", document.sys_path_enabled, event_counts["sys_path_changes"], "best_effort"),
        _mechanism(
            "sys_path_replacements",
            document.sys_path_enabled and document.capture.import_audit_enabled,
            event_counts["sys_path_replacements"],
            "import_boundaries",
        ),
        _cache_snapshot_mechanism(document),
        _mechanism(
            "importer_cache_changes",
            document.importer_cache.enabled,
            event_counts["importer_cache_changes"],
            "passive_boundaries",
        ),
    ]


def _json_snapshots(document: ReportDocument) -> list[SnapshotJSON]:
    """Project install- and report-time import-state snapshots."""
    return [
        {
            "data": {"entries": list(document.meta_path.initial)},
            "id": "snapshot:install",
            "kind": "meta_path",
            "phase": "install",
        },
        {
            "data": {"entries": None if document.meta_path.current is None else list(document.meta_path.current)},
            "id": "snapshot:report",
            "kind": "meta_path",
            "phase": "report",
        },
        {
            "data": {"entries": [_json_import_object(reference) for reference in document.path_hooks.initial]},
            "id": "snapshot:path-hooks:install",
            "kind": "path_hooks",
            "phase": "install",
        },
        {
            "data": {
                "entries": None
                if document.path_hooks.current is None
                else [_json_import_object(reference) for reference in document.path_hooks.current]
            },
            "id": "snapshot:path-hooks:report",
            "kind": "path_hooks",
            "phase": "report",
        },
        {
            "data": {
                "entries": [_json_importer_cache_entry(entry) for entry in document.importer_cache.initial],
                "non_string_keys": document.importer_cache.initial_non_string_keys,
            },
            "id": "snapshot:importer-cache:install",
            "kind": "importer_cache",
            "phase": "install",
        },
        {
            "data": {
                "entries": None
                if document.importer_cache.current is None
                else [_json_importer_cache_entry(entry) for entry in document.importer_cache.current],
                "non_string_keys": document.importer_cache.current_non_string_keys,
            },
            "id": "snapshot:importer-cache:report",
            "kind": "importer_cache",
            "phase": "report",
        },
    ]


def _json_diagnostics(document: ReportDocument, monitoring_error_refs: list[str]) -> DiagnosticsInfo:
    """Project failures isolated while observing or reporting."""
    return {
        "monitoring_error_refs": monitoring_error_refs,
        "report_errors": [
            {"exception_type_name": error.exception_type_name, "where": error.where}
            for error in document.analysis.report_errors
        ],
        "skipped_finders": [
            {
                "expected": skipped.expected,
                "finder_type_name": skipped.finder_type_name,
                "reason": skipped.reason,
            }
            for skipped in document.analysis.skipped_finders
        ],
    }


def _mechanism(name: MechanismName, enabled: bool, retained: int, completeness: MechanismCompleteness) -> MechanismJSON:
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


def _json_finder_api(observation: FinderAPIObservation) -> FinderAPIObservationJSON:
    """Project one finder API observation without consulting the live finder."""
    return {
        "category": _finder_api_category(observation),
        "id": _finder_api_id(observation),
        "finder_id": f"0x{observation.finder_id:x}",
        "finder_type_name": observation.finder_type_name,
        "find_module": {
            "availability": observation.find_module.availability,
            "defined_by": observation.find_module.defined_by,
            "evidence": observation.find_module.evidence,
        },
        "find_spec": {
            "availability": observation.find_spec.availability,
            "defined_by": observation.find_spec.defined_by,
            "evidence": observation.find_spec.evidence,
        },
        "observation": observation.observation,
        "observation_event_ref": (
            None if observation.observation_seq is None else f"event:{observation.observation_seq}"
        ),
        "position": observation.position,
    }


def _result_analysis_mechanism(document: ReportDocument) -> MechanismJSON:
    return {
        "capacity": None,
        "comparison_count": len(document.analysis.finder_result_comparisons),
        "completeness": "reported_custom_winners",
        "dropped": 0,
        "enabled": document.capture.monitor_enabled,
        "name": "finder_result_analysis",
        "overflow_policy": "retain_all",
        "retained": len(document.analysis.finder_results),
        "shutdown": "synchronous_no_retry",
    }


def _cache_snapshot_mechanism(document: ReportDocument) -> MechanismJSON:
    return {
        "capacity": 2,
        "coalesced": document.importer_cache.coalesced,
        "completeness": "passive_boundaries",
        "dropped": 0,
        "enabled": document.importer_cache.enabled,
        "name": "importer_cache_snapshots",
        "observations": document.importer_cache.observations,
        "overflow_policy": "replace_latest",
        "retained": min(document.importer_cache.observations, 2),
        "shutdown": "synchronous_no_retry",
    }


def _finder_api_id(observation: FinderAPIObservation) -> str:
    """Return the document-scoped id for one observed finder API."""
    return f"finder-api:0x{observation.finder_id:x}"


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


def _json_import_search_started(event: ImportSearchStarted) -> ImportSearchStartedDataJSON:
    return {
        "evidence": "resolution_started",
        "fullname": event.fullname,
        "importer_cache": None
        if event.importer_cache_id is None
        else {
            "object_id": f"0x{event.importer_cache_id:x}",
            "size": event.importer_cache_size,
        },
        "search_ref": f"search:{event.search_id}",
        "meta_path": {
            "entries": list(event.meta_path_type_names),
            "object_id": f"0x{event.meta_path_id:x}",
        },
        "path_hooks_id": None if event.path_hooks_id is None else f"0x{event.path_hooks_id:x}",
        "thread_name": event.thread_name,
        "thread_id": event.thread_id,
    }


def _json_import_branch_exploration_started(
    event: ImportBranchExplorationStarted,
) -> ImportBranchExplorationStartedDataJSON:
    return {
        "boundary": event.boundary,
        "evidence": "unsafe_import_branch_exploration",
        "fullname": event.fullname,
        "path": event.path,
        "trigger": _json_import_object(event.trigger),
        "trigger_event_ref": None if event.trigger_event_seq is None else f"event:{event.trigger_event_seq}",
        "trigger_index": event.trigger_index,
        "trigger_outcome": event.trigger_outcome,
        "thread_id": event.thread_id,
        "thread_name": event.thread_name,
    }


def _json_import_branch_exploration_call(
    event: ImportBranchExplorationCall,
) -> ImportBranchExplorationCallDataJSON:
    return {
        "boundary": event.boundary,
        "candidate": _json_import_object(event.candidate),
        "candidate_index": event.candidate_index,
        "evidence": "unsafe_import_branch_exploration",
        "exception_type_name": event.exception_type_name,
        "exploration_ref": f"event:{event.exploration_seq}",
        "fullname": event.fullname,
        "module_state_after": _json_module_state(event.module_state_after),
        "module_state_before": _json_module_state(event.module_state_before),
        "outcome": event.outcome,
        "path": event.path,
        "returned_finder": None if event.returned_finder is None else _json_import_object(event.returned_finder),
        "spec": None if event.spec_summary is None else _json_spec_summary(event.spec_summary),
        "thread_id": event.thread_id,
        "thread_name": event.thread_name,
    }


def _json_import_mechanism_call(event: ImportMechanismCall) -> ImportMechanismCallDataJSON:
    return {
        "boundary": event.boundary,
        "evidence": "detailed_delegation",
        "exception_type_name": event.exception_type_name,
        "fullname": event.fullname,
        "module_state_after": _json_module_state(event.module_state_after),
        "module_state_before": _json_module_state(event.module_state_before),
        "object_id": f"0x{event.object_id:x}",
        "object_type_name": event.object_type_name,
        "outcome": event.outcome,
        "path": event.path,
        "returned_finder": None if event.returned_finder is None else _json_import_object(event.returned_finder),
        "target_state": _json_module_state(event.target_state),
        "thread_name": event.thread_name,
        "thread_id": event.thread_id,
    }


def _json_import_result(event: ImportResult) -> ImportResultDataJSON:
    return {
        "search_ref": f"search:{event.search_id}",
        "evidence": "exact_import_boundary",
        "fullname": event.fullname,
        "outcome": event.outcome,
        "thread_id": event.thread_id,
        "thread_name": event.thread_name,
    }


def _json_import_call(event: ImportCall) -> ImportCallDataJSON:
    return {
        "evidence": "import_call_wrapper",
        "exception_type_name": event.exception_type_name,
        "fromlist": list(event.fromlist),
        "importing_module": event.importing_module,
        "level": event.level,
        "module_state_before": _json_module_state(event.module_state_before),
        "name": event.name,
        "outcome": event.outcome,
        "thread_id": event.thread_id,
        "thread_name": event.thread_name,
    }


def _json_path_finder_call(event: PathFinderCall) -> PathFinderCallDataJSON:
    return {
        "search_ref": f"search:{event.search_id}",
        "evidence": "observed_path_finder_boundary",
        "finder_type_name": event.finder_type_name,
        "fullname": event.fullname,
        "spec": _json_spec_summary(event.spec_summary),
        "thread_id": event.thread_id,
        "thread_name": event.thread_name,
    }


def _json_meta_path_finder_call(event: MetaPathFinderCall) -> MetaPathFinderCallDataJSON:
    return {
        "exception_type_name": event.exception_type_name,
        "finder_id": f"0x{event.finder_id:x}",
        "finder_type_name": event.finder_type_name,
        "found": event.found,
        "fullname": event.fullname,
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


def _json_importer_cache_change(event: ImporterCacheChange) -> ImporterCacheChangeDataJSON:
    return {
        "added": [_json_importer_cache_entry(entry) for entry in event.added],
        "non_string_keys_after": event.non_string_keys_after,
        "non_string_keys_before": event.non_string_keys_before,
        "observation": event.observation,
        "removed": [_json_importer_cache_entry(entry) for entry in event.removed],
        "replaced": [_json_importer_cache_replacement(item) for item in event.replaced],
        "thread_name": event.thread_name,
    }


def _json_meta_path_change(event: MetaPathChange) -> MetaPathChangeDataJSON:
    return {
        "added": list(event.added),
        "contents_after": list(event.contents_after),
        "op": event.op,
        "removed": list(event.removed),
        "stack": [_json_frame(frame) for frame in event.stack],
        "thread_name": event.thread_name,
    }


def _json_meta_path_replacement(event: MetaPathReplacement) -> ReassignmentDataJSON:
    return {
        "during_import": event.during_import,
        "new_contents": list(event.new_contents),
        "old_contents": list(event.old_contents),
        "stack": [_json_frame(frame) for frame in event.stack],
        "thread_name": event.thread_name,
    }


def _json_path_hooks_change(event: PathHooksChange) -> PathHooksChangeDataJSON:
    return {
        "added": [_json_import_object(reference) for reference in event.added],
        "contents_after": [_json_import_object(reference) for reference in event.contents_after],
        "op": event.op,
        "removed": [_json_import_object(reference) for reference in event.removed],
        "stack": [_json_frame(frame) for frame in event.stack],
        "thread_name": event.thread_name,
    }


def _json_path_hooks_replacement(event: PathHooksReplacement) -> PathHooksReplacementDataJSON:
    return {
        "during_import": event.during_import,
        "new_contents": [_json_import_object(reference) for reference in event.new_contents],
        "old_contents": [_json_import_object(reference) for reference in event.old_contents],
        "stack": [_json_frame(frame) for frame in event.stack],
        "thread_name": event.thread_name,
    }


def _json_sys_path_change(event: SysPathChange) -> SysPathChangeDataJSON:
    return {
        "added": list(event.added),
        "contents_after": list(event.contents_after),
        "op": event.op,
        "removed": list(event.removed),
        "stack": [_json_frame(frame) for frame in event.stack],
        "thread_name": event.thread_name,
    }


def _json_sys_path_replacement(event: SysPathReplacement) -> ReassignmentDataJSON:
    return {
        "during_import": event.during_import,
        "new_contents": list(event.new_contents),
        "old_contents": list(event.old_contents),
        "stack": [_json_frame(frame) for frame in event.stack],
        "thread_name": event.thread_name,
    }


def _json_monitoring_error(event: MonitoringError) -> MonitoringErrorDataJSON:
    return {
        "exception_type_name": event.exception_type_name,
        "message": event.message,
        "where": event.where,
    }


# One serializer per event type, returning the kind-specific ``data`` payload.
# ``_json_event`` wraps it in the shared ``{id, sequence, kind, data}`` envelope. New
# event types must be added here and in ``_report_events`` together.
_EVENT_JSON_BUILDERS: "dict[type[MonitorEvent], Callable[..., EventDataJSON]]" = {
    ImportBranchExplorationStarted: _json_import_branch_exploration_started,
    ImportBranchExplorationCall: _json_import_branch_exploration_call,
    ImportSearchStarted: _json_import_search_started,
    ImportMechanismCall: _json_import_mechanism_call,
    ImportResult: _json_import_result,
    ImportCall: _json_import_call,
    PathFinderCall: _json_path_finder_call,
    MetaPathFinderCall: _json_meta_path_finder_call,
    ImporterCacheChange: _json_importer_cache_change,
    MetaPathChange: _json_meta_path_change,
    MetaPathReplacement: _json_meta_path_replacement,
    PathHooksChange: _json_path_hooks_change,
    PathHooksReplacement: _json_path_hooks_replacement,
    SysPathChange: _json_sys_path_change,
    SysPathReplacement: _json_sys_path_replacement,
    MonitoringError: _json_monitoring_error,
}


def _json_event(event: MonitorEvent) -> EventJSON:
    """Wrap one event's kind-specific payload in the shared envelope."""
    return {
        "id": f"event:{event.sequence}",
        "sequence": event.sequence,
        "kind": EVENT_KIND[type(event)],
        "data": _EVENT_JSON_BUILDERS[type(event)](event),
    }


def _json_import_search(search: ImportSearch) -> ImportSearchJSON:
    """Serialize one derived search while retaining links to raw evidence."""
    return {
        "id": f"search:{search.search_id}",
        "fullname": search.fullname,
        "start_event_ref": f"event:{search.start_event_seq}",
        "evidence_event_refs": [f"event:{sequence}" for sequence in search.event_seqs],
        "thread_id": search.thread_id,
        "thread_name": search.thread_name,
        "progress": search.progress,
        "presence": search.presence,
    }


def _json_standard_resolution(resolution: StandardResolution) -> StandardResolutionJSON:
    """Serialize provenance without presenting inference as a raw event."""
    return {
        "search_ref": f"search:{resolution.search_id}",
        "category": resolution.category,
        "component_event_refs": [f"event:{sequence}" for sequence in resolution.component_event_seqs],
        "evidence_level": resolution.evidence_level,
        "event_ref": None if resolution.event_seq is None else f"event:{resolution.event_seq}",
        "finder_type_name": resolution.finder_type_name,
        "fullname": resolution.fullname,
        "later_finders": list(resolution.later_finders),
        "loader_type_name": resolution.loader_type_name,
        "origin": resolution.origin,
        "state_phase": resolution.state_phase,
    }


def _json_resolution_result(result: FinderResult) -> FinderResultJSON:
    """Serialize one result without implying that a check predicts a winner."""
    summary = result.spec_summary
    return {
        "id": result.result_id,
        "module": result.module,
        "kind": result.kind,
        "purpose": result.purpose,
        "limitations": list(result.limitations),
        "evidence_level": result.evidence_level,
        "state_phase": result.state_phase,
        "predicts_alternative_winner": result.predicts_alternative_winner,
        "finder_type_name": result.finder_type_name,
        "finder_id": None if result.finder_id is None else f"0x{result.finder_id:x}",
        "status": result.status,
        "spec": None if summary is None else _json_spec_summary(summary),
        "exception_type_name": result.exception_type_name,
        "source_event_refs": [f"event:{sequence}" for sequence in result.source_event_seqs],
        "search_path": list(result.search_path),
        "search_path_kind": result.search_path_kind,
        "search_path_phase": result.search_path_phase,
        "signals": list(result.signals),
    }


def _json_result_comparison(comparison: FinderResultComparison) -> FinderResultComparisonJSON:
    """Serialize symmetric result differences and stable result references."""
    structural = comparison.structural_comparison
    return {
        "id": comparison.comparison_id,
        "left_result_ref": comparison.left_result_id,
        "right_result_ref": comparison.right_result_id,
        "complete": comparison.complete,
        "status_differs": comparison.status_differs,
        "loader_type_differs": comparison.loader_type_differs,
        "origin_differs": comparison.origin_differs,
        "cached_differs": comparison.cached_differs,
        "package_status_differs": comparison.package_status_differs,
        "only_in_left_result": list(comparison.only_in_left_result),
        "only_in_right_result": list(comparison.only_in_right_result),
        "locations_reordered": comparison.locations_reordered,
        "left_locations_state": comparison.left_locations_state,
        "right_locations_state": comparison.right_locations_state,
        "structural_comparison": {
            "evidence_level": structural.evidence_level,
            "importer_cache": {
                "changed": structural.importer_cache_changed,
                "changed_paths": list(structural.importer_cache_changed_paths),
                "change_event_refs": [f"event:{sequence}" for sequence in structural.importer_cache_event_seqs],
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


def _json_import_object(reference: ObjectIdentity) -> ImportObjectJSON:
    """Serialize safe import-object identity metadata."""
    return {
        "name": reference.name,
        "object_id": f"0x{reference.object_id:x}",
        "type_name": reference.type_name,
    }


def _json_spec_value(value: str | ObjectIdentity | None) -> SpecValueJSON:
    if isinstance(value, ObjectIdentity):
        result: ImportObjectValueJSON = {"kind": "object", **_json_import_object(value)}
        return result
    return value


def _json_check_run(run: CheckRun) -> CheckRunJSON:
    """Project one check policy/resource summary."""
    return {
        "kind": run.kind,
        "status": run.status,
        "unavailable_reasons": list(run.unavailable_reasons),
        "candidates": run.candidates,
        "results": run.results,
        "foreign_calls": run.foreign_calls,
        "capacity": run.capacity,
        "omitted": run.omitted,
    }


def _json_spec_summary(summary: ModuleSpecSnapshot) -> ModuleSpecSnapshotJSON:
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
) -> tuple[tuple[ObjectIdentity | None, tuple[ModuleMetadata, ...]], ...]:
    """Group available module records by effective loader identity."""
    groups: dict[int | None, tuple[ObjectIdentity | None, list[ModuleMetadata]]] = {}
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
        "evidence": "current_state",
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
    return {
        "data": _json_finding_data(finding),
        "evidence": _json_finding_evidence(finding),
        "id": finding.finding_id,
        "kind": finding.kind,
        "module": finding.module,
        "severity": finding.severity,
        "signals": list(finding.signals),
        "subject": {"kind": finding.subject_kind, "value": finding.module},
    }


def _json_finding_evidence(finding: Finding) -> FindingEvidenceJSON:
    event_refs: list[str] = []
    finder_call = finding_finder_call(finding)
    if finder_call is not None:
        event_refs.append(f"event:{finder_call.sequence}")
    import_mechanism_call = finding_import_mechanism_call(finding)
    if import_mechanism_call is not None:
        event_refs.append(f"event:{import_mechanism_call.sequence}")
    finder_api = finding_finder_api(finding)
    if finder_api is not None and finder_api.observation_seq is not None:
        event_refs.append(f"event:{finder_api.observation_seq}")
    event_refs.extend(f"event:{sequence}" for sequence in finding.supporting_event_seqs)
    structural = finding_structural_comparison(finding)
    if structural is not None:
        event_refs.extend(f"event:{sequence}" for sequence in structural.importer_cache_event_seqs)
    evidence: FindingEvidenceJSON = {
        "event_refs": list(dict.fromkeys(event_refs)),
        "level": finding.evidence_level,
        "limitations": list(finding.limitations),
    }
    if finding.kind == "module_without_spec":
        evidence["finder_call_status"] = "not_recorded"
        evidence["module_spec_status"] = "missing"
    if finding.kind == "finder_changed_module_cache" and finder_call is not None:
        evidence["module_state_after"] = _json_module_state(finder_call.module_state_after)
        evidence["module_state_before"] = _json_module_state(finder_call.module_state_before)
        evidence["outcome"] = (
            f"raised:{finder_call.exception_type_name}" if finder_call.exception_type_name is not None else "declined"
        )
    return evidence


def _json_finding_data(finding: Finding) -> FindingEvidenceDataJSON:
    """Dispatch on the evidence variant to build the ``detail``-tagged payload."""
    evidence = finding.evidence
    if isinstance(evidence, CorrelationEvidence):
        return {
            "detail": "correlation",
            "search_refs": [f"search:{search_id}" for search_id in evidence.search_ids],
        }
    if isinstance(evidence, FinderAPIEvidence):
        return {"detail": "finder_api", "finder_api_ref": _finder_api_id(evidence.finder_api)}
    if isinstance(evidence, FinderCallEvidence):
        return {"detail": "finder_call", "finder_call": _json_finding_finder_call(evidence.finder_call)}
    if isinstance(evidence, FinderComparisonEvidence):
        return {
            "detail": "result_comparison",
            "finder_call": _json_finding_finder_call(evidence.finder_call),
            "finder_result_refs": list(evidence.result_ids),
            "finder_result_comparison_ref": evidence.result_comparison_id,
            "structural_comparison": _json_structural_comparison(evidence.structural_comparison),
        }
    if isinstance(evidence, ImportMechanismEvidence):
        return {
            "detail": "import_mechanism_call",
            "import_mechanism_call": _json_finding_import_mechanism_call(evidence.import_mechanism_call),
            "module_state_baseline": _json_module_state(evidence.module_state_baseline),
        }
    return {"detail": "events_only"}


def _json_finding_finder_call(finder_call: MetaPathFinderCall) -> FindingFinderCallJSON:
    return {
        "event_ref": f"event:{finder_call.sequence}",
        "finder_id": f"0x{finder_call.finder_id:x}",
        "finder_type_name": finder_call.finder_type_name,
        "loader_type_name": finder_call.loader_type_name,
        "origin": finder_call.origin,
        "search_path": list(finder_call.search_path),
        "search_path_kind": finder_call.search_path_kind,
        "spec": None if finder_call.spec_summary is None else _json_spec_summary(finder_call.spec_summary),
    }


def _json_finding_import_mechanism_call(import_mechanism_call: ImportMechanismCall) -> FindingImportMechanismCallJSON:
    return {
        "boundary": import_mechanism_call.boundary,
        "event_ref": f"event:{import_mechanism_call.sequence}",
        "module_state_after": _json_module_state(import_mechanism_call.module_state_after),
        "module_state_before": _json_module_state(import_mechanism_call.module_state_before),
        "outcome": import_mechanism_call.outcome,
        "target_state": _json_module_state(import_mechanism_call.target_state),
    }


def _json_structural_comparison(comparison: StructuralComparison) -> StructuralComparisonJSON:
    return {
        "evidence_level": comparison.evidence_level,
        "importer_cache": {
            "changed": comparison.importer_cache_changed,
            "changed_paths": list(comparison.importer_cache_changed_paths),
            "change_event_refs": [f"event:{sequence}" for sequence in comparison.importer_cache_event_seqs],
            "install_snapshot_ref": "snapshot:importer-cache:install",
            "report_snapshot_ref": "snapshot:importer-cache:report",
        },
        "path_hooks": {
            "changed": comparison.path_hooks_changed,
            "install_snapshot_ref": "snapshot:path-hooks:install",
            "report_snapshot_ref": "snapshot:path-hooks:report",
        },
    }


def _json_explanation(explanation: CausalExplanation) -> ExplanationJSON:
    """Serialize causal joins without making human prose a schema contract."""
    return {
        "alternatives": list(explanation.alternatives),
        "candidate_path": explanation.candidate_path,
        "boundary": explanation.boundary,
        "cause_finding_ref": explanation.cause_finding_id,
        "confidence": explanation.confidence,
        "effect_status": explanation.effect_status,
        "event_refs": [f"event:{sequence}" for sequence in explanation.event_seqs],
        "finder_type_name": explanation.finder_type_name,
        "id": explanation.explanation_id,
        "kind": explanation.kind,
        "later_finders": list(explanation.later_finders),
        "next_observation": explanation.next_observation,
        "origin": explanation.origin,
        "omitted_location": explanation.omitted_location,
        "subject": explanation.subject,
        "standard_search_ref": (
            None if explanation.standard_search_id is None else f"search:{explanation.standard_search_id}"
        ),
        "state_after": _json_module_state(explanation.state_after),
        "state_before": _json_module_state(explanation.state_before),
    }


def _json_summary(summary: ReportSummary) -> SummaryInfo:
    """Serialize the shared counts-and-references summary."""
    return {
        "counts": {
            "problem": summary.problem,
            "risk": summary.risk,
            "note": summary.note,
        },
        "unresolved_import_count": summary.unresolved_import_count,
        "top_finding_ref": summary.top_finding_id,
        "top_explanation_ref": summary.top_explanation_id,
    }


def _json_program_outcome(outcome: ProgramOutcome | None) -> ProgramOutcomeJSON | None:
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
            "evidence": "current_state",
            "groups": [],
            "non_string_keys_omitted": 0,
            "phase": "report",
            "unavailable": [],
        },
        "finder_apis": [],
        "import_searches": [],
        "standard_resolutions": [],
        "finder_results": [],
        "finder_result_comparisons": [],
        "timeline": [],
        "findings": [],
        "explanations": [],
        "summary": {
            "counts": {"problem": 0, "risk": 0, "note": 0},
            "unresolved_import_count": 0,
            "top_finding_ref": None,
            "top_explanation_ref": None,
        },
        "checks": [],
        "program_outcome": None,
        "diagnostics": {
            "monitoring_error_refs": [],
            "report_errors": [{"exception_type_name": error_name, "where": "report_generation"}],
            "skipped_finders": [],
        },
    }
