"""Live interpreter capture and report-document assembly."""

import os
import sys
import time

from metapathology._module_metadata import ModuleMetadata, inspect_module
from metapathology._records import ObjectRef, type_name
from metapathology._report_analysis import (
    _causal_explanations,
    _import_attempts,
    _namespace_displacement_findings,
    _repeated_load_failure_findings,
    _report_summary,
    _standard_resolutions,
    _suspicious_findings,
)
from metapathology._report_model import (
    EarlySiteBootstrap,
    FrozenBootstrap,
    LoaderInventory,
    ReportDocument,
    ReportError,
    SkippedFinder,
    TargetOutcome,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._monitor import Monitor
    from metapathology._monitor_model import MonitorSnapshot


_STANDARD_CLASS_FINDER_REASON_PREFIX = "standard CPython class finder;"


def capture_document(monitor: "Monitor") -> ReportDocument:
    """Copy evidence at one sequence cutoff before deriving any findings."""
    snapshot = monitor._report_state()
    events = list(snapshot.events)
    finder_contracts = list(snapshot.finder_contracts)
    importer_cache = snapshot.importer_cache
    report_errors: list[ReportError] = []
    current_meta_path = _current_meta_path_names(report_errors)
    current_path_hooks = _current_path_hooks(monitor, snapshot, report_errors)
    module_items = _module_items(report_errors)
    loader_inventory = _loader_inventory(module_items)
    attempts = _import_attempts(events, module_items)
    standard_resolutions = _standard_resolutions(events, attempts, loader_inventory.entries)
    modules_since_install = _modules_since_install(snapshot.baseline_modules, module_items)
    skipped_finders = tuple(
        SkippedFinder(
            type_name(finder),
            reason,
            reason.startswith(_STANDARD_CLASS_FINDER_REASON_PREFIX),
        )
        for finder, reason in snapshot.skipped_finders
    )
    with monitor._report_analysis():
        findings, resolution_routes, route_comparisons = _suspicious_findings(
            snapshot.baseline_modules,
            events,
            snapshot.initial_path_hooks,
            current_path_hooks,
            importer_cache.initial_entries,
            importer_cache.latest_entries,
            module_items,
            loader_inventory.entries,
            finder_contracts,
            attempts,
            report_errors,
        )
    findings = (
        *findings,
        *_namespace_displacement_findings(
            attempts,
            standard_resolutions,
            events,
            len(findings),
        ),
    )
    findings = (
        *findings,
        *_repeated_load_failure_findings(
            attempts,
            standard_resolutions,
            len(findings),
        ),
    )
    explanations = _causal_explanations(findings, attempts, standard_resolutions, route_comparisons, events)
    outcome_state = snapshot.target_outcome
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
        if snapshot.early_site_bootstrap is None
        else EarlySiteBootstrap(
            snapshot.early_site_bootstrap.path,
            snapshot.early_site_bootstrap.site_packages,
            snapshot.early_site_bootstrap.activation_source,
            snapshot.early_site_bootstrap.earlier_pth_files,
        )
    )
    frozen_bootstrap = (
        None
        if snapshot.frozen_bootstrap is None
        else FrozenBootstrap(
            snapshot.frozen_bootstrap.integration,
            snapshot.frozen_bootstrap.path,
            snapshot.frozen_bootstrap.boundary,
        )
    )
    return ReportDocument(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        attempts=attempts,
        cutoff_seq=snapshot.cutoff_seq,
        monitor_enabled=snapshot.enabled,
        baseline_module_count=len(snapshot.baseline_modules),
        initial_meta_path=snapshot.initial_meta_path,
        current_meta_path=current_meta_path,
        path_hooks_enabled=snapshot.path_hooks_enabled,
        initial_path_hooks=snapshot.initial_path_hooks,
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
        deep_diagnostics=snapshot.deep_diagnostics,
        deep_import_outcomes_status=snapshot.deep_import_outcomes_status,
        deep_import_calls_status=snapshot.deep_import_calls_status,
        skipped_finders=skipped_finders,
        standard_resolutions=standard_resolutions,
        standard_finder_status=snapshot.standard_finder_status,
        findings=findings,
        resolution_routes=resolution_routes,
        route_comparisons=route_comparisons,
        finder_contracts=tuple(finder_contracts),
        report_errors=tuple(report_errors),
        summary=summary,
        sys_path_enabled=snapshot.sys_path_enabled,
        target_outcome=target_outcome,
        cwd=cwd,
        argv=tuple(argv),
    )


def _current_meta_path_names(report_errors: list[ReportError]) -> tuple[str, ...] | None:
    """Name current meta-path entries, tolerating broken shutdown state."""
    try:
        return tuple(type_name(finder) for finder in list(sys.meta_path))
    except Exception as exc:
        report_errors.append(ReportError("snapshot.meta_path", type_name(exc)))
        return None


def _current_path_hooks(
    monitor: "Monitor",
    snapshot: "MonitorSnapshot",
    report_errors: list[ReportError],
) -> tuple[ObjectRef, ...] | None:
    """Copy current path-hook identities when path-hooks monitoring is active."""
    if not snapshot.path_hooks_enabled:
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
    baseline_modules: frozenset[str], module_items: list[tuple[object, object]] | None
) -> tuple[str, ...] | None:
    """List module names added after monitor installation."""
    if module_items is None:
        return None
    return tuple(name for name, _module in module_items if type(name) is str and name not in baseline_modules)
