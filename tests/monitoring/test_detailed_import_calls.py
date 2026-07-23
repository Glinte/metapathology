"""Detailed ``builtins.__import__`` observation, including cache-hit imports."""

from pathlib import Path

from support import PythonRunner

CACHE_HIT = r"""
import builtins
import json

import metapathology
from metapathology import ImportCall

before = builtins.__import__
monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_calls=True)))

# cache_target is fresh here, so this is a real resolution...
import cache_target
# ...and this second statement is a pure sys.modules cache hit: no audit event
# and no finder call would record it.
import cache_target

calls = [event for event in monitor.events() if isinstance(event, ImportCall) and event.name == "cache_target"]
assert len(calls) == 2, calls
first, second = calls
assert first.importing_module == "__main__"
assert first.module_state_before.state == "missing", first.module_state_before
assert first.outcome == "returned"
# The cache hit is the whole point: the module was already present on entry.
assert second.module_state_before.state == "object", second.module_state_before
assert second.outcome == "returned"

metapathology.uninstall()
assert builtins.__import__ is before, "wrapper not restored on uninstall"

# The JSON projection carries the same evidence.
document = json.loads(metapathology.render_report(format="json"))
hits = [
    event
    for event in document["timeline"]
    if event["kind"] == "import_call" and event["data"]["name"] == "cache_target"
]
assert len(hits) == 2, hits
assert hits[1]["data"]["module_state_before"]["state"] == "object"
assert hits[1]["data"]["importing_module"] == "__main__"
mechanisms = {item["name"]: item for item in document["capture"]["mechanisms"]}
assert mechanisms["import_calls"]["retained"] >= 2
print("OK")
"""


def test_cache_hits_are_observed_and_restored(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "cache_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    proc = python_runner.run_code_ok(CACHE_HIT)

    assert proc.stdout.strip() == "OK"


FROMLIST_AND_LEVEL = r"""
import metapathology
from metapathology import ImportCall

monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_calls=True)))

from importcalls_pkg import leaf  # absolute import with a fromlist
import importcalls_pkg.driver  # runs a relative "from . import leaf" inside the package

calls = [event for event in monitor.events() if isinstance(event, ImportCall)]

fromlist_calls = [event for event in calls if event.name == "importcalls_pkg" and event.fromlist]
assert fromlist_calls, calls
assert "leaf" in fromlist_calls[0].fromlist

relative = [event for event in calls if event.level > 0]
assert relative, [(event.name, event.level) for event in calls]
assert relative[0].importing_module == "importcalls_pkg.driver"
print("OK")
"""


def test_fromlist_and_relative_level_are_captured(python_runner: PythonRunner, tmp_path: Path) -> None:
    package = tmp_path / "importcalls_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "leaf.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "driver.py").write_text("from . import leaf\n", encoding="utf-8")

    proc = python_runner.run_code_ok(FROMLIST_AND_LEVEL)

    assert proc.stdout.strip() == "OK"


RAISED = r"""
import metapathology
from metapathology import ImportCall

monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_calls=True)))

try:
    import definitely_not_a_real_module_xyz
except ImportError:
    pass

calls = [
    event
    for event in monitor.events()
    if isinstance(event, ImportCall) and event.name == "definitely_not_a_real_module_xyz"
]
assert calls, "the failing import statement was not observed"
assert calls[0].outcome == "raised"
assert calls[0].exception_type_name in {"ImportError", "ModuleNotFoundError"}
print("OK")
"""


def test_failed_import_records_the_raised_outcome(python_runner: PythonRunner, tmp_path: Path) -> None:
    proc = python_runner.run_code_ok(RAISED)

    assert proc.stdout.strip() == "OK"


CHAIN_SAFE = r"""
import builtins

import metapathology

# A third-party tool wraps __import__ before us.
original = builtins.__import__
calls = []

def other_tool_import(name, *args, **kwargs):
    calls.append(name)
    return original(name, *args, **kwargs)

builtins.__import__ = other_tool_import

with metapathology.monitoring(capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_calls=True))):
    import json  # noqa: F401

# Our wrapper must delegate to the pre-existing one, and restore must leave the
# other tool's wrapper in place rather than clobbering it.
assert builtins.__import__ is other_tool_import, builtins.__import__
assert "json" in calls
builtins.__import__ = original
print("OK")
"""


def test_wrapping_is_chain_safe_with_other_import_wrappers(python_runner: PythonRunner, tmp_path: Path) -> None:
    proc = python_runner.run_code_ok(CHAIN_SAFE)

    assert proc.stdout.strip() == "OK"


DISABLED_BY_DEFAULT = r"""
import metapathology
from metapathology import ImportCall

monitor = metapathology.install(report_at_exit=False)
import json  # noqa: F401
assert not any(isinstance(event, ImportCall) for event in monitor.events())
assert monitor.import_calls_capture_status == "disabled"
print("OK")
"""


def test_mechanism_is_off_unless_requested(python_runner: PythonRunner, tmp_path: Path) -> None:
    proc = python_runner.run_code_ok(DISABLED_BY_DEFAULT)

    assert proc.stdout.strip() == "OK"
