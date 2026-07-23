"""Monitor installation, regions, and lifecycle behavior."""

from support import PythonRunner

INSTALL_RESTORE = """
import sys
import metapathology

before = list(sys.meta_path)
monitor = metapathology.install(report_at_exit=False)
assert not hasattr(monitor, "install")
assert not hasattr(monitor, "uninstall")
assert not hasattr(monitor, "_report_format")
assert not hasattr(monitor, "_report_destination")
assert type(sys.meta_path) is not list
assert isinstance(sys.meta_path, list)  # real list subclass: isinstance scans keep working
assert list(sys.meta_path) == before

again = metapathology.install(report_at_exit=False)
assert again is monitor  # idempotent, no double wrapping
inner = sys.meta_path

metapathology.uninstall()
assert type(sys.meta_path) is list
assert list(sys.meta_path) == before
metapathology.uninstall()  # idempotent
print("OK")
"""


def test_install_and_uninstall_restore_meta_path(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(INSTALL_RESTORE)
    assert "OK" in proc.stdout


def test_monitoring_region_restores_after_an_exception(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "before = list(sys.meta_path)\n"
        "try:\n"
        "    with metapathology.monitoring(monitor_path_hooks=False) as monitor:\n"
        "        assert monitor.enabled\n"
        "        assert metapathology.get_monitor() is monitor\n"
        "        assert type(sys.meta_path) is not list\n"
        "        import colorsys\n"
        "        raise LookupError('target failure')\n"
        "except LookupError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('region suppressed the target exception')\n"
        "assert not monitor.enabled\n"
        "assert type(sys.meta_path) is list and list(sys.meta_path) == before\n"
        "assert any(getattr(event, 'fullname', None) == 'colorsys' for event in monitor.events())\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_nested_monitoring_regions_share_lifecycle_and_enable_mechanisms(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import warnings\n"
        "import metapathology\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    with metapathology.monitoring(monitor_path_hooks=False) as outer:\n"
        "        assert not outer.path_hooks_enabled\n"
        "        with metapathology.monitoring(monitor_path_hooks=True) as inner:\n"
        "            assert inner is outer and inner.path_hooks_enabled\n"
        "        assert outer.enabled and outer.path_hooks_enabled\n"
        "assert not caught, caught\n"
        "assert not outer.enabled\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_monitoring_region_preserves_preexisting_installation(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import warnings\n"
        "import metapathology\n"
        "explicit = metapathology.install(report_at_exit=False, monitor_path_hooks=False)\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    with metapathology.monitoring(monitor_path_hooks=True) as regional:\n"
        "        assert regional is explicit and regional.path_hooks_enabled\n"
        "assert len(caught) == 1, caught\n"
        "assert caught[0].category is RuntimeWarning\n"
        "assert 'pre-existing installation will remain active' in str(caught[0].message)\n"
        "assert explicit.enabled and explicit.path_hooks_enabled\n"
        "metapathology.uninstall()\n"
        "assert not explicit.enabled\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_overlapping_monitoring_regions_uninstall_after_last_exit(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import threading\n"
        "import metapathology\n"
        "first_entered = threading.Event()\n"
        "second_entered = threading.Event()\n"
        "release_first = threading.Event()\n"
        "first_exited = threading.Event()\n"
        "release_second = threading.Event()\n"
        "failures = []\n"
        "def first():\n"
        "    try:\n"
        "        with metapathology.monitoring():\n"
        "            first_entered.set()\n"
        "            assert release_first.wait(10)\n"
        "        first_exited.set()\n"
        "    except BaseException as exc:\n"
        "        failures.append(exc)\n"
        "def second():\n"
        "    try:\n"
        "        assert first_entered.wait(10)\n"
        "        with metapathology.monitoring():\n"
        "            second_entered.set()\n"
        "            assert release_second.wait(10)\n"
        "    except BaseException as exc:\n"
        "        failures.append(exc)\n"
        "threads = [threading.Thread(target=first), threading.Thread(target=second)]\n"
        "for thread in threads: thread.start()\n"
        "assert second_entered.wait(10)\n"
        "release_first.set()\n"
        "assert first_exited.wait(10)\n"
        "assert metapathology.get_monitor().enabled\n"
        "release_second.set()\n"
        "for thread in threads: thread.join(10)\n"
        "assert not failures, failures\n"
        "assert all(not thread.is_alive() for thread in threads)\n"
        "assert not metapathology.get_monitor().enabled\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_install_warns_once_when_starting_on_unsupported_implementation(python_runner: PythonRunner) -> None:
    python_runner.run_code_ok(
        "import warnings\n"
        "import metapathology\n"
        "import metapathology._monitor as monitor_module\n"
        "monitor_module._IMPLEMENTATION_NAME = 'pypy'\n"
        "monitor_module._UNSUPPORTED_IMPLEMENTATION_WARNING = (\n"
        "    \"metapathology supports CPython only; monitoring on 'pypy' may be incomplete or inaccurate\"\n"
        ")\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    monitor = metapathology.install(report_at_exit=False)\n"
        "    assert metapathology.install(report_at_exit=False) is monitor\n"
        "metapathology.uninstall()\n"
        "assert len(caught) == 1, caught\n"
        "warning = caught[0]\n"
        "assert warning.category is RuntimeWarning, warning.category\n"
        "assert str(warning.message) == monitor_module._UNSUPPORTED_IMPLEMENTATION_WARNING\n"
    )
