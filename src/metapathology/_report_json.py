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
    DeepDiagnosticCall,
    DeepImportEvent,
    FinderContract,
    FindSpecCall,
    ImportAuditStart,
    ImporterCacheDiff,
    ImporterCacheEntry,
    ImporterCacheReplacement,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    ModuleCacheState,
    MonitorEvent,
    ObjectRef,
    PathHooksMutation,
    PathHooksReassignment,
    SpecSummary,
    StandardFinderCall,
    SysPathMutation,
    SysPathReassignment,
)
from metapathology._report_analysis import _finder_contract_category
from metapathology._report_model import (
    CausalExplanation,
    EarlySiteBootstrap,
    Finding,
    FrozenBootstrap,
    ImportAttempt,
    LoaderInventory,
    ReportDocument,
    ReportSummary,
    ResolutionRoute,
    RouteComparison,
    StandardResolution,
    TargetOutcome,
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

TYPE_CHECKING = False

if TYPE_CHECKING:
    from traceback import FrameSummary


_SCHEMA_NAME = "metapathology.report"
_SCHEMA_MAJOR = 1
_SCHEMA_MINOR = 1


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


def _json_import_object(reference: ObjectRef) -> ImportObjectJSON:
    """Serialize safe import-object identity metadata."""
    return {
        "name": reference.name,
        "object_id": f"0x{reference.object_id:x}",
        "type_name": reference.type_name,
    }


def _json_spec_value(value: str | ObjectRef | None) -> SpecValueJSON:
    if isinstance(value, ObjectRef):
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
) -> tuple[tuple[ObjectRef | None, tuple[ModuleMetadata, ...]], ...]:
    """Group available module records by effective loader identity."""
    groups: dict[int | None, tuple[ObjectRef | None, list[ModuleMetadata]]] = {}
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
    if finding.attempt_ids:
        result["attempt_refs"] = [f"attempt:{attempt_id}" for attempt_id in finding.attempt_ids]
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
