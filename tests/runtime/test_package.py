from importlib.metadata import version

from support import PythonRunner

import metapathology

_EXPECTED_PUBLIC_API = frozenset(
    {
        "DeepDiagnosticCall",
        "DeepImportEvent",
        "FindSpecCall",
        "ObjectRef",
        "ImporterCacheDiff",
        "ImporterCacheEntry",
        "ImporterCacheReplacement",
        "ImportAuditStart",
        "ImportCall",
        "InternalError",
        "MetaPathMutation",
        "MetaPathReassignment",
        "ModuleCacheState",
        "Monitor",
        "MonitorEvent",
        "PathHooksMutation",
        "PathHooksReassignment",
        "ReportJSON",
        "ReportStatus",
        "SpecSummary",
        "SpeculativeReplay",
        "StandardFinderCall",
        "SysPathMutation",
        "SysPathReassignment",
        "__version__",
        "activate_frozen",
        "get_monitor",
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
    proc = python_runner.run_code_ok(
        "import sys\n"
        "assert 'importlib.metadata' not in sys.modules\n"
        "import metapathology\n"
        "deferred = ('importlib.metadata', 'metapathology._monitor', 'metapathology._records', 'typing')\n"
        "print([name for name in deferred if name in sys.modules])\n"
    )
    assert proc.stdout.strip() == "[]"


def test_record_api_does_not_load_dataclasses_or_typing(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "from metapathology import InternalError\n"
        "deferred = ('dataclasses', 'traceback', 'typing')\n"
        "print([name for name in deferred if name in sys.modules])\n"
    )
    assert proc.stdout.strip() == "[]"


def test_public_api_surface() -> None:
    assert frozenset(metapathology.__all__) == _EXPECTED_PUBLIC_API
    assert len(metapathology.__all__) == len(_EXPECTED_PUBLIC_API)
    assert frozenset(dir(metapathology)) >= _EXPECTED_PUBLIC_API
    assert all(hasattr(metapathology, name) for name in _EXPECTED_PUBLIC_API)
