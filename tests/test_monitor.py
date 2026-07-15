"""Monitor behavior, exercised in fresh interpreters (see conftest)."""

import subprocess
from collections.abc import Callable

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

MutationOperation = tuple[str, int, int]
MUTATION_OPERATIONS = st.lists(
    st.tuples(
        st.sampled_from(
            [
                "append",
                "clear",
                "del_item",
                "del_slice",
                "extend",
                "iadd",
                "imul",
                "insert",
                "pop",
                "reverse",
                "set_item",
                "set_slice",
                "sort",
            ]
        ),
        st.integers(min_value=-8, max_value=8),
        st.integers(min_value=-8, max_value=8),
    ),
    min_size=1,
    max_size=30,
)

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


FIND_SPEC_METADATA_SNAPSHOTS = """
import sys

import metapathology
from metapathology import FindSpecCall

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

finder = DummyFinder()
sys.meta_path.insert(0, finder)
monitor = metapathology.install(report_at_exit=False)

shared_path = ["first", "second"]
finder.find_spec("first_call", shared_path)
finder.find_spec("second_call", shared_path)
calls = [event for event in monitor.events() if isinstance(event, FindSpecCall)]
assert calls[-2].search_path == ("first", "second")
assert calls[-1].search_path == ("first", "second")
assert calls[-2].finder_id == id(finder)
assert calls[-1].finder_id == id(finder)
assert calls[-2].finder_type_name == "DummyFinder"
assert calls[-1].finder_type_name == "DummyFinder"

shared_path.append("third")
finder.find_spec("changed_path", shared_path)
calls = [event for event in monitor.events() if isinstance(event, FindSpecCall)]
assert calls[-3].search_path == ("first", "second")
assert calls[-1].search_path == ("first", "second", "third")

for index in range(10):
    finder.find_spec(f"distinct_{index}", [f"path_{index}"])
calls = [event for event in monitor.events() if isinstance(event, FindSpecCall)]
assert [call.search_path for call in calls[-10:]] == [(f"path_{index}",) for index in range(10)]
assert all(call.finder_id == id(finder) for call in calls)
print("OK")
"""


def test_find_spec_records_preserve_metadata_and_path_snapshots(run_python: RunPython) -> None:
    proc = run_python(FIND_SPEC_METADATA_SNAPSHOTS)
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

assert type(copied) is list
assert len(copied) == len(sys.meta_path)
assert copied is not sys.meta_path
print("OK")
"""


def test_instrumented_meta_path_can_be_deep_copied(run_python: RunPython) -> None:
    proc = run_python(DEEP_COPY)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


MUTATION_SEQUENCE = """
import sys

import metapathology
from metapathology import MetaPathMutation

operations = __OPERATIONS__

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

def new_finders(count):
    return [DummyFinder() for _ in range(count)]

monitor = metapathology.install(report_at_exit=False)
expected = list(sys.meta_path)
expected_ops = []

for op, first, second in operations:
    if op == "append":
        item = DummyFinder()
        sys.meta_path.append(item)
        expected.append(item)
    elif op == "clear":
        sys.meta_path.clear()
        expected.clear()
    elif op == "del_item":
        if not expected:
            continue
        index = first % len(expected)
        del sys.meta_path[index]
        del expected[index]
    elif op == "del_slice":
        index = slice(first, second)
        del sys.meta_path[index]
        del expected[index]
    elif op == "extend":
        items = new_finders(abs(first) % 3)
        sys.meta_path.extend(iter(items))
        expected.extend(items)
    elif op == "iadd":
        items = new_finders(abs(first) % 3)
        sys.meta_path += iter(items)
        expected += items
    elif op == "imul":
        multiplier = abs(first) % 3
        sys.meta_path *= multiplier
        expected *= multiplier
    elif op == "insert":
        item = DummyFinder()
        sys.meta_path.insert(first, item)
        expected.insert(first, item)
    elif op == "pop":
        if not expected:
            continue
        index = first % len(expected)
        assert sys.meta_path.pop(index) is expected.pop(index)
    elif op == "reverse":
        sys.meta_path.reverse()
        expected.reverse()
    elif op == "set_item":
        if not expected:
            continue
        index = first % len(expected)
        item = DummyFinder()
        sys.meta_path[index] = item
        expected[index] = item
    elif op == "set_slice":
        index = slice(first, second)
        items = new_finders(abs(first - second) % 3)
        sys.meta_path[index] = iter(items)
        expected[index] = items
    elif op == "sort":
        sys.meta_path.sort(key=id, reverse=bool(first % 2))
        expected.sort(key=id, reverse=bool(first % 2))
    else:
        raise AssertionError(op)

    expected_ops.append({
        "del_item": "__delitem__",
        "del_slice": "__delitem__",
        "iadd": "__iadd__",
        "imul": "__imul__",
        "set_item": "__setitem__",
        "set_slice": "__setitem__",
    }.get(op, op))
    assert len(sys.meta_path) == len(expected)
    assert all(actual is wanted for actual, wanted in zip(sys.meta_path, expected))

recorded_ops = [event.op for event in monitor.events() if isinstance(event, MetaPathMutation)]
assert recorded_ops == expected_ops, (recorded_ops, expected_ops)
print("OK")
"""


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=MUTATION_OPERATIONS)
def test_generated_mutation_sequences_match_plain_list(
    run_python: RunPython, operations: list[MutationOperation]
) -> None:
    proc = run_python(MUTATION_SEQUENCE.replace("__OPERATIONS__", repr(operations)))
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


HOSTILE_FINDER = """
import sys

import metapathology
from metapathology import InternalError

class HostileError(Exception):
    stringify_count = 0

    def __str__(self):
        type(self).stringify_count += 1
        raise AssertionError("instrumentation must not stringify foreign exceptions")

class HostileFinder:
    def __getattribute__(self, name):
        if name == "find_spec":
            raise HostileError()
        return object.__getattribute__(self, name)

monitor = metapathology.install(report_at_exit=False)
finder = HostileFinder()
sys.meta_path.append(finder)

assert finder in sys.meta_path
assert HostileError.stringify_count == 0
errors = [event for event in monitor.events() if isinstance(event, InternalError)]
assert any(event.where == "instrument_finder" for event in errors), errors
assert HostileError.stringify_count == 0
print("OK")
"""


def test_instrumentation_failure_does_not_escape_or_stringify_foreign_exception(run_python: RunPython) -> None:
    proc = run_python(HOSTILE_FINDER)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


REENTRANT_FINDER = """
import sys

import metapathology
from metapathology import FindSpecCall

class ReentrantFinder:
    def __init__(self):
        self.nested = False

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fractions" and not self.nested:
            self.nested = True
            import colorsys
            assert colorsys.rgb_to_hsv(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)
        return None

monitor = metapathology.install(report_at_exit=False)
finder = ReentrantFinder()
sys.meta_path.insert(0, finder)

import fractions
assert fractions.Fraction(1, 2).numerator == 1

calls = [
    event
    for event in monitor.events()
    if isinstance(event, FindSpecCall) and event.finder_id == id(finder)
]
names = [event.fullname for event in calls]
assert "fractions" in names, calls
assert "colorsys" not in names, calls
print("OK")
"""


def test_finder_wrapper_is_reentrancy_guarded(run_python: RunPython) -> None:
    proc = run_python(REENTRANT_FINDER)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


CONCURRENT_ACTIVITY = """
import sys
import threading

import metapathology

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

module_names = [f"concurrent_module_{index}" for index in range(40)]
for index, module_name in enumerate(module_names):
    with open(f"{module_name}.py", "w", encoding="utf-8") as module_file:
        module_file.write(f"value = {index}\\n")

monitor = metapathology.install(report_at_exit=False)
failures = []
barrier = threading.Barrier(7)

def mutate():
    try:
        barrier.wait()
        for _ in range(100):
            finder = DummyFinder()
            sys.meta_path.append(finder)
            sys.meta_path.remove(finder)
    except BaseException as exc:
        failures.append(exc)

def read_events():
    try:
        barrier.wait()
        for _ in range(200):
            events = monitor.events()
            assert all(left.seq < right.seq for left, right in zip(events, events[1:]))
    except BaseException as exc:
        failures.append(exc)

def render():
    try:
        barrier.wait()
        for _ in range(20):
            assert "metapathology" in metapathology.render_report()
    except BaseException as exc:
        failures.append(exc)

def import_modules():
    try:
        barrier.wait()
        for index, module_name in enumerate(module_names):
            module = __import__(module_name)
            assert module.value == index
    except BaseException as exc:
        failures.append(exc)

threads = [threading.Thread(target=mutate) for _ in range(3)]
threads.extend(
    [threading.Thread(target=read_events), threading.Thread(target=render), threading.Thread(target=import_modules)]
)
for thread in threads:
    thread.start()
barrier.wait()
for thread in threads:
    thread.join()

assert not failures, failures
assert all(module_name in sys.modules for module_name in module_names)
assert monitor.enabled
metapathology.uninstall()
assert type(sys.meta_path) is list
print("OK")
"""


def test_concurrent_mutation_event_reads_and_reports_are_safe(run_python: RunPython) -> None:
    proc = run_python(CONCURRENT_ACTIVITY)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


CONCURRENT_UNINSTALL = """
import sys
import threading

import metapathology

monitor = metapathology.install(report_at_exit=False)
barrier = threading.Barrier(9)
failures = []

def uninstall():
    try:
        barrier.wait()
        metapathology.uninstall()
    except BaseException as exc:
        failures.append(exc)

threads = [threading.Thread(target=uninstall) for _ in range(8)]
for thread in threads:
    thread.start()
barrier.wait()
for thread in threads:
    thread.join()

assert not failures, failures
assert not monitor.enabled
assert type(sys.meta_path) is list
print("OK")
"""


def test_concurrent_uninstall_is_idempotent(run_python: RunPython) -> None:
    proc = run_python(CONCURRENT_UNINSTALL)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
