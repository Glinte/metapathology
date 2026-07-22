"""Live interpreter capture and report-document assembly."""

import os
import sys
import time

from metapathology._module_metadata import ModuleMetadata, inspect_module
from metapathology._records import ObjectRef, SpeculativeReplay, type_name
from metapathology._report_analysis import (
    _AnalysisInputs,
    _causal_explanations,
    _IdAllocator,
    _import_attempts,
    _namespace_displacement_findings,
    _repeated_load_failure_findings,
    _report_summary,
    _standard_resolutions,
    _suspicious_findings,
)
from metapathology._report_model import (
    AnalysisResult,
    CaptureInfo,
    EarlySiteBootstrap,
    FrozenBootstrap,
    ImporterCacheSnapshot,
    LoaderInventory,
    MetaPathSnapshot,
    PathHooksSnapshot,
    ProcessInfo,
    ReportDocument,
    ReportError,
    SkippedFinder,
    StandardResolution,
    TargetOutcome,
)
from metapathology._speculative_replay import replay_displaced_finders

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._monitor import Monitor
    from metapathology._monitor_model import MonitorSnapshot
    from metapathology._records import MonitorEvent
    from metapathology._report_model import CausalExplanation, Finding, ResolutionRoute, RouteComparison


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
    inputs = _AnalysisInputs(
        baseline_modules=snapshot.baseline_modules,
        events=events,
        initial_path_hooks=snapshot.initial_path_hooks,
        current_path_hooks=current_path_hooks,
        initial_importer_cache=importer_cache.initial_entries,
        current_importer_cache=importer_cache.latest_entries,
        module_items=module_items,
        module_metadata=loader_inventory.entries,
        finder_contracts=finder_contracts,
        attempts=attempts,
        report_errors=report_errors,
    )
    findings, explanations, resolution_routes, route_comparisons = _derive_findings(
        monitor, inputs, standard_resolutions
    )
    # Speculative replay and the process snapshot run after finding derivation
    # so their report_errors keep their historical position in the log.
    speculative_replays, speculative_replays_omitted = _speculative_replays(monitor, snapshot, events)
    summary = _report_summary(findings, explanations, attempts)
    process = _capture_process(report_errors)
    return ReportDocument(
        process=process,
        capture=_capture_info(snapshot, modules_since_install),
        meta_path=MetaPathSnapshot(
            initial=snapshot.initial_meta_path,
            current=current_meta_path,
        ),
        path_hooks=PathHooksSnapshot(
            enabled=snapshot.path_hooks_enabled,
            initial=snapshot.initial_path_hooks,
            current=current_path_hooks,
        ),
        importer_cache=ImporterCacheSnapshot(
            enabled=importer_cache.enabled,
            initial=importer_cache.initial_entries,
            initial_non_string_keys=importer_cache.initial_non_string_keys,
            current=importer_cache.latest_entries,
            current_non_string_keys=importer_cache.latest_non_string_keys,
            observations=importer_cache.observations,
            coalesced=importer_cache.coalesced,
        ),
        sys_path_enabled=snapshot.sys_path_enabled,
        analysis=AnalysisResult(
            attempts=attempts,
            events=tuple(events),
            findings=findings,
            explanations=explanations,
            resolution_routes=resolution_routes,
            route_comparisons=route_comparisons,
            standard_resolutions=standard_resolutions,
            finder_contracts=tuple(finder_contracts),
            loader_inventory=loader_inventory,
            skipped_finders=skipped_finders,
            summary=summary,
            target_outcome=_target_outcome(snapshot),
            report_errors=tuple(report_errors),
            speculative_replay_enabled=snapshot.speculative_replay_enabled,
            speculative_replays=speculative_replays,
            speculative_replays_omitted=speculative_replays_omitted,
        ),
    )


def _derive_findings(
    monitor: "Monitor",
    inputs: _AnalysisInputs,
    standard_resolutions: tuple[StandardResolution, ...],
) -> "tuple[tuple[Finding, ...], tuple[CausalExplanation, ...], tuple[ResolutionRoute, ...], tuple[RouteComparison, ...]]":
    """Run the finding/route/explanation pipeline under the report-analysis guard.

    One allocator per element kind numbers ids in creation order; the shared
    ``finding`` allocator carries across the corroborated-route, namespace, and
    repeated-load passes so the numbering stays contiguous.
    """
    finding_ids = _IdAllocator("finding")
    with monitor._report_analysis():
        findings, resolution_routes, route_comparisons = _suspicious_findings(
            inputs, finding_ids, _IdAllocator("route"), _IdAllocator("comparison")
        )
    findings = (
        *findings,
        *_namespace_displacement_findings(inputs.attempts, standard_resolutions, inputs.events, finding_ids),
    )
    findings = (*findings, *_repeated_load_failure_findings(inputs.attempts, standard_resolutions, finding_ids))
    explanations = _causal_explanations(
        findings, inputs.attempts, standard_resolutions, route_comparisons, _IdAllocator("explanation"), inputs.events
    )
    return findings, explanations, resolution_routes, route_comparisons


def _speculative_replays(
    monitor: "Monitor", snapshot: "MonitorSnapshot", events: list["MonitorEvent"]
) -> tuple[tuple[SpeculativeReplay, ...], int]:
    """Replay displaced importer-cache finders when the mechanism is enabled.

    The single foreign ``find_spec()`` per candidate runs under the
    report-analysis guard so it records no monitor events and cannot re-enter
    the tool's own instrumentation.
    """
    if not snapshot.speculative_replay_enabled:
        return (), 0
    with monitor._report_analysis():
        return replay_displaced_finders(monitor, events)


def _target_outcome(snapshot: "MonitorSnapshot") -> TargetOutcome | None:
    """Reduce how the monitored target finished to report-safe plain data."""
    outcome_state = snapshot.target_outcome
    if outcome_state is None:
        return None
    return TargetOutcome(
        outcome_state.kind,
        outcome_state.exception_type_name,
        outcome_state.missing_module,
        outcome_state.exit_code,
    )


def _capture_process(report_errors: list[ReportError]) -> ProcessInfo:
    """Snapshot the host process identity, recording any inaccessible field."""
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
    return ProcessInfo(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        cwd=cwd,
        argv=tuple(argv),
    )


def _capture_info(snapshot: "MonitorSnapshot", modules_since_install: tuple[str, ...] | None) -> CaptureInfo:
    """Reduce monitor-lifetime facts and bootstrap provenance to report data."""
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
    return CaptureInfo(
        cutoff_seq=snapshot.cutoff_seq,
        monitor_enabled=snapshot.enabled,
        baseline_module_count=len(snapshot.baseline_modules),
        modules_since_install=modules_since_install,
        early_site_bootstrap=early_site_bootstrap,
        frozen_bootstrap=frozen_bootstrap,
        deep_diagnostics=snapshot.deep_diagnostics,
        deep_import_outcomes_status=snapshot.deep_import_outcomes_status,
        deep_import_calls_status=snapshot.deep_import_calls_status,
        standard_finder_status=snapshot.standard_finder_status,
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
