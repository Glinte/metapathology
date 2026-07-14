"""Monitor behavior, exercised in fresh interpreters (see conftest)."""

import subprocess
from collections.abc import Callable

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

INSTALL_RESTORE = """
import sys
import metapathology

before = list(sys.meta_path)
monitor = metapathology.install(report_at_exit=False)
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


def test_install_and_uninstall_restore_meta_path(run_python: RunPython) -> None:
    proc = run_python(INSTALL_RESTORE)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


MUTATIONS = """
import sys
import metapathology
from metapathology import MetaPathMutation

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
finder = DummyFinder()
sys.meta_path.insert(0, finder)
sys.meta_path.remove(finder)
sys.meta_path.append(finder)
popped = sys.meta_path.pop()
assert popped is finder
sys.meta_path[0:0] = [finder]
del sys.meta_path[0]

mutations = [e for e in monitor.events() if isinstance(e, MetaPathMutation)]
ops = [m.op for m in mutations]
assert ops == ["insert", "remove", "append", "pop", "__setitem__", "__delitem__"], ops
assert mutations[0].added == ("DummyFinder",)
assert mutations[1].removed == ("DummyFinder",)
assert any(frame.filename == "<string>" for frame in mutations[0].stack), list(mutations[0].stack)
print("OK")
"""


def test_meta_path_mutations_are_recorded_with_stacks(run_python: RunPython) -> None:
    proc = run_python(MUTATIONS)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


ATTRIBUTION = """
import importlib.abc
import sys
from importlib.machinery import ModuleSpec

import metapathology
from metapathology import FindSpecCall

class DummyLoader(importlib.abc.Loader):
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        module.value = 42

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "dummy_mod":
            return ModuleSpec(fullname, DummyLoader())
        return None

monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DummyFinder())

import dummy_mod
assert dummy_mod.value == 42

calls = [e for e in monitor.events() if isinstance(e, FindSpecCall)]
wins = [c for c in calls if c.fullname == "dummy_mod" and c.found]
assert wins, calls
assert wins[-1].finder_type_name == "DummyFinder"
assert wins[-1].loader_type_name == "DummyLoader"
assert wins[-1].origin is None

# A claim without a filesystem origin must not be flagged as a bypass.
text = metapathology.render_report()
assert "[bypass]" not in text, text
assert "dummy_mod" in text
print("OK")
"""


def test_find_spec_calls_are_attributed_to_the_claiming_finder(run_python: RunPython) -> None:
    proc = run_python(ATTRIBUTION)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


REASSIGNMENT = """
import sys
import metapathology
from metapathology import MetaPathReassignment

monitor = metapathology.install(report_at_exit=False)
instrumented_type = type(sys.meta_path)
sys.meta_path = list(sys.meta_path)  # blow away the instrumentation
assert type(sys.meta_path) is list

import colorsys  # any not-yet-imported module: triggers the import audit event

reassignments = [e for e in monitor.events() if isinstance(e, MetaPathReassignment)]
assert len(reassignments) == 1, reassignments
assert reassignments[0].during_import == "colorsys"
assert type(sys.meta_path) is instrumented_type  # reinstalled
print("OK")
"""


def test_meta_path_reassignment_is_detected_and_reinstrumented(run_python: RunPython) -> None:
    proc = run_python(REASSIGNMENT)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


UNINSTALL_STOPS_RECORDING = """
import sys
import metapathology
from metapathology import MetaPathMutation, MetaPathReassignment

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
metapathology.uninstall()

sys.meta_path.insert(0, DummyFinder())
sys.meta_path = list(sys.meta_path)
import colorsys  # audit hook must stay inert after uninstall

events = monitor.events()
assert not any(isinstance(e, (MetaPathMutation, MetaPathReassignment)) for e in events), events
assert type(sys.meta_path) is list
print("OK")
"""


def test_uninstall_makes_all_layers_inert(run_python: RunPython) -> None:
    proc = run_python(UNINSTALL_STOPS_RECORDING)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


UNINSTALL_RESTORES_INSTANCE_FIND_SPEC = """
import sys

import metapathology

class InstanceFinder:
    pass

finder = InstanceFinder()
def find_spec(fullname, path=None, target=None):
    return None
finder.find_spec = find_spec
sys.meta_path.insert(0, finder)

metapathology.install(report_at_exit=False)
assert finder.find_spec is not find_spec
metapathology.uninstall()

assert finder.find_spec is find_spec
print("OK")
"""


def test_uninstall_restores_preexisting_instance_find_spec(run_python: RunPython) -> None:
    proc = run_python(UNINSTALL_RESTORES_INSTANCE_FIND_SPEC)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


IN_PLACE_REPEAT = """
import sys

import metapathology
from metapathology import MetaPathMutation

monitor = metapathology.install(report_at_exit=False)

sys.meta_path *= 2
ops = [event.op for event in monitor.events() if isinstance(event, MetaPathMutation)]
assert "__imul__" in ops, ops
print("OK")
"""


def test_in_place_repeat_mutation_is_recorded(run_python: RunPython) -> None:
    proc = run_python(IN_PLACE_REPEAT)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


SORT_MUTATION = """
import sys

import metapathology
from metapathology import MetaPathMutation

monitor = metapathology.install(report_at_exit=False)

sys.meta_path.sort(key=id)
ops = [event.op for event in monitor.events() if isinstance(event, MetaPathMutation)]
assert "sort" in ops, ops
print("OK")
"""


def test_sort_mutation_is_recorded(run_python: RunPython) -> None:
    proc = run_python(SORT_MUTATION)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


DEEP_COPY = """
import copy
import sys

import metapathology

metapathology.install(report_at_exit=False)
copied = copy.deepcopy(sys.meta_path)

assert copied == sys.meta_path
assert copied is not sys.meta_path
print("OK")
"""


def test_instrumented_meta_path_can_be_deep_copied(run_python: RunPython) -> None:
    proc = run_python(DEEP_COPY)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
