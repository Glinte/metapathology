import subprocess
from collections.abc import Callable
from importlib.metadata import version

import metapathology

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

_EXPECTED_PUBLIC_API = frozenset(
    {
        "FindSpecCall",
        "ImportObjectRef",
        "InternalError",
        "MetaPathMutation",
        "MetaPathReassignment",
        "Monitor",
        "MonitorEvent",
        "PathHooksMutation",
        "PathHooksReassignment",
        "__version__",
        "get_monitor",
        "install",
        "render_report",
        "uninstall",
        "write_report",
    }
)


def test_runtime_version_matches_distribution_metadata() -> None:
    assert version("metapathology") == metapathology.__version__


def test_package_import_defers_monitor_and_distribution_metadata(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "assert 'importlib.metadata' not in sys.modules\n"
        "import metapathology\n"
        "deferred = ('importlib.metadata', 'metapathology._monitor', 'metapathology._records', 'typing')\n"
        "print([name for name in deferred if name in sys.modules])\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


def test_record_api_does_not_load_dataclasses_or_typing(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "from metapathology import InternalError\n"
        "deferred = ('dataclasses', 'traceback', 'typing')\n"
        "print([name for name in deferred if name in sys.modules])\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


def test_public_api_surface() -> None:
    assert frozenset(metapathology.__all__) == _EXPECTED_PUBLIC_API
    assert len(metapathology.__all__) == len(_EXPECTED_PUBLIC_API)
    assert frozenset(dir(metapathology)) >= _EXPECTED_PUBLIC_API
    assert all(hasattr(metapathology, name) for name in _EXPECTED_PUBLIC_API)
