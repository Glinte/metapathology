from importlib.metadata import version

from support import PythonRunner

import metapathology

_EXPECTED_PUBLIC_API = frozenset(
    {
        "AnalysisConfig",
        "CaptureConfig",
        "DetailedCaptureConfig",
        "ImportMechanismCall",
        "ImportResult",
        "MetaPathFinderCall",
        "ObjectIdentity",
        "ImporterCacheChange",
        "ImporterCacheEntry",
        "ImporterCacheReplacement",
        "ImportSearchStarted",
        "ImportCall",
        "ImportBranchExplorationCall",
        "ImportBranchExplorationStarted",
        "MonitoringError",
        "MetaPathChange",
        "MetaPathReplacement",
        "ModuleCacheState",
        "Monitor",
        "MonitorEvent",
        "PathHooksChange",
        "PathHooksReplacement",
        "ReportJSON",
        "ReportStatus",
        "ModuleSpecSnapshot",
        "PathFinderCall",
        "SysPathChange",
        "SysPathReplacement",
        "__version__",
        "activate_frozen",
        "get_monitor",
        "get_report",
        "install",
        "monitoring",
        "render_report",
        "uninstall",
        "write_report",
    }
)


def test_runtime_version_matches_distribution_metadata() -> None:
    assert version("metapathology") == metapathology.__version__


def test_package_import_defers_monitor_and_distribution_metadata(python_runner: PythonRunner) -> None:
    proc = python_runner.run_scenario_ok(
        "runtime/package.py", "package_import_defers_monitor_and_distribution_metadata"
    )
    assert proc.stdout.strip() == "[]"


def test_record_api_does_not_load_dataclasses_or_typing(python_runner: PythonRunner) -> None:
    proc = python_runner.run_scenario_ok("runtime/package.py", "record_api_does_not_load_dataclasses_or_typing")
    assert proc.stdout.strip() == "[]"


def test_public_api_surface() -> None:
    assert frozenset(metapathology.__all__) == _EXPECTED_PUBLIC_API
    assert len(metapathology.__all__) == len(_EXPECTED_PUBLIC_API)
    assert frozenset(dir(metapathology)) >= _EXPECTED_PUBLIC_API
    assert all(hasattr(metapathology, name) for name in _EXPECTED_PUBLIC_API)
