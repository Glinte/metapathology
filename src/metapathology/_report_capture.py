"""Live interpreter capture and report-document assembly."""

import os
import sys
import time

from metapathology._config import ResolvedAnalysisConfig
from metapathology._detailed_capture import original_path_hook
from metapathology._displaced_finder_check import (
    MAX_DISPLACED_FINDER_CHECKS,
    check_displaced_finders,
)
from metapathology._module_metadata import ModuleMetadata, inspect_module
from metapathology._records import ObjectIdentity, type_name
from metapathology._report_analysis import (
    _AnalysisInputs,
    _causal_explanations,
    _IdAllocator,
    _import_searches,
    _module_failed_after_loading_findings,
    _namespace_displacement_findings,
    _report_summary,
    _standard_resolutions,
    _suspicious_findings,
)
from metapathology._report_model import (
    AnalysisResult,
    CaptureInfo,
    CheckRun,
    EarlySiteBootstrap,
    FrozenBootstrap,
    ImporterCacheSnapshot,
    LoaderInventory,
    MetaPathSnapshot,
    PathHooksSnapshot,
    ProcessInfo,
    ProgramOutcome,
    ReportDocument,
    ReportError,
    SkippedFinder,
    StandardResolution,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._monitor_model import MonitorSnapshot
    from metapathology._report_model import CausalExplanation, FinderResult, FinderResultComparison, Finding


_STANDARD_CLASS_FINDER_REASON_PREFIX = "standard CPython class finder;"


def capture_document(snapshot: "MonitorSnapshot", analysis: ResolvedAnalysisConfig) -> ReportDocument:
    """Copy evidence at one sequence cutoff before deriving any findings."""
    events = list(snapshot.events)
    finder_apis = list(snapshot.finder_apis)
    importer_cache = snapshot.importer_cache
    report_errors: list[ReportError] = []
    current_meta_path = _current_meta_path_names(report_errors)
    current_path_hooks = _current_path_hooks(snapshot, report_errors)
    module_items = _module_items(report_errors)
    loader_inventory = _loader_inventory(module_items)
    searches = _import_searches(events, module_items)
    standard_resolutions = _standard_resolutions(events, searches, loader_inventory.entries)
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
        finder_apis=finder_apis,
        searches=searches,
        report_errors=report_errors,
    )
    findings, explanations, finder_results, finder_result_comparisons, checks = _derive_findings(
        inputs, standard_resolutions, snapshot, analysis
    )
    summary = _report_summary(findings, explanations, searches)
    process = _capture_process(report_errors)
    return ReportDocument(
        process=process,
        capture=_capture_info(snapshot, modules_since_install),
        meta_path=MetaPathSnapshot(
            enabled=snapshot.meta_path_enabled,
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
            searches=searches,
            events=tuple(events),
            findings=findings,
            explanations=explanations,
            finder_results=finder_results,
            finder_result_comparisons=finder_result_comparisons,
            standard_resolutions=standard_resolutions,
            finder_apis=tuple(finder_apis),
            loader_inventory=loader_inventory,
            skipped_finders=skipped_finders,
            summary=summary,
            program_outcome=_program_outcome(snapshot),
            report_errors=tuple(report_errors),
            checks=checks,
        ),
    )


def _derive_findings(
    inputs: _AnalysisInputs,
    standard_resolutions: tuple[StandardResolution, ...],
    snapshot: "MonitorSnapshot",
    analysis: ResolvedAnalysisConfig,
) -> "tuple[tuple[Finding, ...], tuple[CausalExplanation, ...], tuple[FinderResult, ...], tuple[FinderResultComparison, ...], tuple[CheckRun, ...]]":
    """Run the finding/result/explanation pipeline from one captured snapshot.

    One allocator per element kind numbers ids in creation order; the shared
    ``finding`` allocator carries across the corroborated-result, namespace, and
    repeated-load passes so the numbering stays contiguous.
    """
    finding_ids = _IdAllocator("finding")
    result_ids = _IdAllocator("result")
    standard_available = snapshot.finder_attribution_enabled
    findings, finder_results, finder_result_comparisons = _suspicious_findings(
        inputs,
        finding_ids,
        result_ids,
        _IdAllocator("comparison"),
        analysis.standard_path_check and standard_available,
    )
    findings = (
        *findings,
        *_namespace_displacement_findings(inputs.searches, standard_resolutions, inputs.events, finding_ids),
    )
    findings = (*findings, *_module_failed_after_loading_findings(inputs.searches, standard_resolutions, finding_ids))
    explanations = _causal_explanations(
        findings,
        inputs.searches,
        standard_resolutions,
        finder_result_comparisons,
        _IdAllocator("explanation"),
        inputs.events,
    )
    standard_results = tuple(result for result in finder_results if result.kind == "standard_path_check")
    if not analysis.standard_path_check:
        standard_run = CheckRun("standard_path", "disabled", (), 0, 0, 0, None, 0)
    elif not standard_available:
        standard_run = CheckRun("standard_path", "unavailable", ("finder_attribution_disabled",), 0, 0, 0, None, 0)
    else:
        standard_run = CheckRun(
            "standard_path",
            "active",
            (),
            len(standard_results),
            len(standard_results),
            sum(result.status != "target_unavailable" for result in standard_results),
            None,
            0,
        )
    displaced_available = snapshot.importer_cache.enabled and "path_entry_finders" in snapshot.detailed_capture
    if not analysis.displaced_finder_check:
        displaced_results: tuple[FinderResult, ...] = ()
        displaced_run = CheckRun("displaced_finder", "disabled", (), 0, 0, 0, MAX_DISPLACED_FINDER_CHECKS, 0)
    elif not displaced_available:
        reasons = []
        if not snapshot.importer_cache.enabled:
            reasons.append("importer_cache_disabled")
        if "path_entry_finders" not in snapshot.detailed_capture:
            reasons.append("detailed_path_entry_finders_disabled")
        displaced_results = ()
        displaced_run = CheckRun(
            "displaced_finder",
            "unavailable",
            tuple(reasons),
            0,
            0,
            0,
            MAX_DISPLACED_FINDER_CHECKS,
            0,
        )
    else:
        displaced_results, displaced_run = check_displaced_finders(
            inputs.events, snapshot.retained_cache_finders, result_ids.next_id
        )
    return (
        findings,
        explanations,
        (*finder_results, *displaced_results),
        finder_result_comparisons,
        (standard_run, displaced_run),
    )


def _program_outcome(snapshot: "MonitorSnapshot") -> ProgramOutcome | None:
    """Reduce how the monitored target finished to report-safe plain data."""
    outcome_state = snapshot.program_outcome
    if outcome_state is None:
        return None
    return ProgramOutcome(
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
        import_audit_enabled=snapshot.import_audit_enabled,
        meta_path_enabled=snapshot.meta_path_enabled,
        finder_attribution_enabled=snapshot.finder_attribution_enabled,
        baseline_module_count=len(snapshot.baseline_modules),
        modules_since_install=modules_since_install,
        early_site_bootstrap=early_site_bootstrap,
        frozen_bootstrap=frozen_bootstrap,
        detailed_capture=snapshot.detailed_capture,
        import_results_capture_status=snapshot.import_results_capture_status,
        import_calls_capture_status=snapshot.import_calls_capture_status,
        path_finder_capture_status=snapshot.path_finder_capture_status,
    )


def _current_meta_path_names(report_errors: list[ReportError]) -> tuple[str, ...] | None:
    """Name current meta-path entries, tolerating broken shutdown state."""
    try:
        return tuple(type_name(finder) for finder in list(sys.meta_path))
    except Exception as exc:
        report_errors.append(ReportError("snapshot.meta_path", type_name(exc)))
        return None


def _current_path_hooks(
    snapshot: "MonitorSnapshot",
    report_errors: list[ReportError],
) -> tuple[ObjectIdentity, ...] | None:
    """Copy current path-hook identities when path-hooks monitoring is active."""
    if not snapshot.path_hooks_enabled:
        return None
    try:
        return tuple(ObjectIdentity.of(original_path_hook(hook)) for hook in list(sys.path_hooks))
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
