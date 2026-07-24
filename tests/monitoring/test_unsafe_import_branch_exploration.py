"""Explicitly unsafe import-time exploration of skipped resolution branches."""

from support import PythonRunner

META_PATH_EXPLORATION = r"""
import importlib.machinery
import json
import sys

import metapathology

calls = []
executed = []

class ActualLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        executed.append("actual")
        module.VALUE = 7

class AlternativeLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        executed.append("alternative")

class ActualFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unsafe_meta_target":
            return importlib.machinery.ModuleSpec(fullname, ActualLoader())
        return None

class AlternativeFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unsafe_meta_target":
            calls.append(("found", fullname, path, target))
            return importlib.machinery.ModuleSpec(fullname, AlternativeLoader())
        return None

class RaisingFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unsafe_meta_target":
            calls.append(("raised", fullname, path, target))
            raise RuntimeError("explored only")
        return None

monitor = metapathology.install(
    report_at_exit=False,
    unsafe_explore_import_branches=True,
)
actual = ActualFinder()
alternative = AlternativeFinder()
raising = RaisingFinder()
sys.meta_path[0:0] = [actual, alternative, raising]

import unsafe_meta_target
assert unsafe_meta_target.VALUE == 7
assert executed == ["actual"], executed
assert [item[0] for item in calls] == ["found", "raised"], calls
assert all(item[2] is None and item[3] is None for item in calls)

document = metapathology.get_report()
mechanism = next(
    item for item in document["capture"]["mechanisms"]
    if item["name"] == "unsafe_import_branch_exploration"
)
assert mechanism["enabled"] is True
events = [
    item for item in document["timeline"]
    if item["kind"] == "import_branch_exploration_call"
    and item["data"]["fullname"] == "unsafe_meta_target"
]
by_type = {item["data"]["candidate"]["type_name"]: item["data"] for item in events}
assert by_type["AlternativeFinder"]["outcome"] == "found"
assert by_type["RaisingFinder"]["outcome"] == "raised"
assert by_type["RaisingFinder"]["exception_type_name"] == "RuntimeError"
results = [
    item for item in document["finder_results"]
    if item["kind"] == "import_branch_exploration_result"
    and item["module"] == "unsafe_meta_target"
]
assert {item["finder_type_name"] for item in results} >= {"AlternativeFinder", "RaisingFinder"}
assert all(item["evidence_level"] == "explored" for item in results)
assert all(item["predicts_alternative_winner"] is False for item in results)
explored_result_ids = {item["id"] for item in results if item["status"] == "found"}
assert any(
    comparison["left_result_ref"] in explored_result_ids
    or comparison["right_result_ref"] in explored_result_ids
    for comparison in document["finder_result_comparisons"]
)
assert "UNSAFE: skipped import branches were invoked" in metapathology.render_report()

metapathology.uninstall()
assert type(sys.meta_path) is list
assert type(sys.path_hooks) is list
assert "find_spec" not in actual.__dict__
assert "find_spec" not in alternative.__dict__
assert "find_spec" not in raising.__dict__
assert not monitor.enabled
print("OK")
"""


def test_meta_path_exploration_calls_all_later_candidates_without_loading(
    python_runner: PythonRunner,
) -> None:
    proc = python_runner.run_code_ok(META_PATH_EXPLORATION)
    assert proc.stdout.strip() == "OK"


PATH_HOOK_EXPLORATION = r"""
import importlib.machinery
import json
import sys

import metapathology

path_entry = "<unsafe-branch-entry>"
calls = []
executed = []

class ActualLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        executed.append("actual")

class AlternativeLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        executed.append("alternative")

class ActualPathFinder:
    def find_spec(self, fullname, target=None):
        if fullname == "unsafe_path_target":
            return importlib.machinery.ModuleSpec(fullname, ActualLoader())
        return None

class AlternativePathFinder:
    def find_spec(self, fullname, target=None):
        if fullname == "unsafe_path_target":
            calls.append((fullname, target))
            return importlib.machinery.ModuleSpec(fullname, AlternativeLoader())
        return None

def actual_hook(path):
    if path == path_entry:
        return ActualPathFinder()
    raise ImportError

def alternative_hook(path):
    if path == path_entry:
        return AlternativePathFinder()
    raise ImportError

metapathology.install(
    report_at_exit=False,
    unsafe_explore_import_branches=True,
)
sys.path.insert(0, path_entry)
sys.path_hooks[0:0] = [actual_hook, alternative_hook]
sys.path_importer_cache.pop(path_entry, None)

import unsafe_path_target
assert executed == ["actual"], executed
assert calls == [("unsafe_path_target", None)], calls

document = metapathology.get_report()
events = [
    item["data"] for item in document["timeline"]
    if item["kind"] == "import_branch_exploration_call"
    and item["data"]["fullname"] == "unsafe_path_target"
]
assert any(
    item["boundary"] == "path_hook"
    and item["candidate"]["type_name"] == "function"
    and item["outcome"] == "returned_finder"
    for item in events
), events
assert any(
    item["boundary"] == "path_entry"
    and item["candidate"]["type_name"] == "AlternativePathFinder"
    and item["outcome"] == "found"
    for item in events
), events
print("OK")
"""


def test_path_hook_exploration_calls_returned_finder_without_loader(
    python_runner: PythonRunner,
) -> None:
    proc = python_runner.run_code_ok(PATH_HOOK_EXPLORATION)
    assert proc.stdout.strip() == "OK"


DEFAULT_OFF = r"""
import importlib.machinery
import json
import sys

import metapathology

calls = []

class Loader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        pass

class Actual:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unsafe_default_target":
            return importlib.machinery.ModuleSpec(fullname, Loader())
        return None

class Later:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "unsafe_default_target":
            calls.append(fullname)
        return None

metapathology.install(
    report_at_exit=False,
    capture=metapathology.CaptureConfig(detailed=True),
)
sys.meta_path[0:0] = [Actual(), Later()]
import unsafe_default_target
assert calls == []
document = metapathology.get_report()
mechanism = next(
    item for item in document["capture"]["mechanisms"]
    if item["name"] == "unsafe_import_branch_exploration"
)
assert mechanism["enabled"] is False
assert not any("exploration" in item["kind"] for item in document["timeline"])
print("OK")
"""


def test_detailed_capture_does_not_enable_unsafe_exploration(
    python_runner: PythonRunner,
) -> None:
    proc = python_runner.run_code_ok(DEFAULT_OFF)
    assert proc.stdout.strip() == "OK"


PARTIAL_COVERAGE = r"""
import sys
import threading

import metapathology

def existing_profiler(frame, event, arg):
    return None

sys.setprofile(existing_profiler)
threading.setprofile(existing_profiler)
monitor = metapathology.install(
    report_at_exit=False,
    unsafe_explore_import_branches=True,
)
assert sys.getprofile() is existing_profiler
assert monitor.unsafe_import_branch_exploration_status == "partial_profiler_unavailable"
metapathology.uninstall()
assert sys.getprofile() is existing_profiler

sys.setprofile(None)
threading.setprofile(None)
monitor = metapathology.install(
    report_at_exit=False,
    capture=metapathology.CaptureConfig(finder_attribution=False),
    unsafe_explore_import_branches=True,
)
assert (
    monitor.unsafe_import_branch_exploration_status
    == "partial_capture_prerequisites_disabled"
)
metapathology.uninstall()
print("OK")
"""


def test_unsafe_exploration_preserves_profiler_and_reports_partial_coverage(
    python_runner: PythonRunner,
) -> None:
    proc = python_runner.run_code_ok(PARTIAL_COVERAGE)
    assert proc.stdout.strip() == "OK"
