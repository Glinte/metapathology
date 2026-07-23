"""Observed ``sys.meta_path`` list and reassignment semantics."""

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from support import PythonRunner

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


def test_meta_path_mutations_are_recorded_with_stacks(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(MUTATIONS)
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


def test_meta_path_reassignment_is_detected_and_reinstrumented(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(REASSIGNMENT)
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


def test_in_place_repeat_mutation_is_recorded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(IN_PLACE_REPEAT)
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


def test_sort_mutation_is_recorded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(SORT_MUTATION)
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


def test_instrumented_meta_path_can_be_deep_copied(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(DEEP_COPY)
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
    python_runner: PythonRunner, operations: list[MutationOperation]
) -> None:
    proc = python_runner.run_code_ok(MUTATION_SEQUENCE.replace("__OPERATIONS__", repr(operations)))
    assert "OK" in proc.stdout


UNINSTALL_PRESERVES_REPLACEMENTS = """
import sys

import metapathology

original_meta_path = sys.meta_path
original_path_hooks = sys.path_hooks
original_sys_path = sys.path
monitor = metapathology.install(
    report_at_exit=False,
    monitor_path_hooks=True,
    monitor_sys_path=True,
)
replacement_meta_path = []
replacement_path_hooks = []
replacement_sys_path = []
sys.meta_path = replacement_meta_path
sys.path_hooks = replacement_path_hooks
sys.path = replacement_sys_path

metapathology.uninstall()

assert sys.meta_path is replacement_meta_path
assert sys.path_hooks is replacement_path_hooks
assert sys.path is replacement_sys_path
sys.meta_path = original_meta_path
sys.path_hooks = original_path_hooks
sys.path = original_sys_path
print("OK")
"""


def test_uninstall_preserves_replaced_import_lists(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(UNINSTALL_PRESERVES_REPLACEMENTS)
    assert proc.stdout.strip() == "OK"


EXTEND_PARTIAL_ITERATION = """
import sys

import metapathology
from metapathology import MetaPathMutation

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

def failing_iterator(item):
    yield item
    raise RuntimeError("boom")

monitor = metapathology.install(report_at_exit=False)

first = DummyFinder()
before = list(sys.meta_path)
try:
    sys.meta_path.extend(failing_iterator(first))
except RuntimeError:
    pass
assert list(sys.meta_path) == before + [first], (list(sys.meta_path), before)

second = DummyFinder()
before = list(sys.meta_path)
try:
    sys.meta_path += failing_iterator(second)
except RuntimeError:
    pass
assert list(sys.meta_path) == before + [second], (list(sys.meta_path), before)

added = [event.added for event in monitor.events() if isinstance(event, MetaPathMutation)]
assert ("DummyFinder",) in added, added  # partial additions must still be recorded
print("OK")
"""


def test_extend_with_failing_iterator_matches_plain_list_semantics(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(EXTEND_PARTIAL_ITERATION)
    assert "OK" in proc.stdout


# uninstall() can run to completion while a mutation thread is still inside
# _instrument_finder for a newly added finder (the foreign find_spec attribute
# lookup can block arbitrarily long); the late wrapper must not survive.
REASSIGNED_LIST_GOES_STALE = """
import sys

import metapathology
from metapathology import MetaPathReassignment

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
mine = list(sys.meta_path)
sys.meta_path = mine

import colorsys  # first uncached import: detection swaps in an instrumented copy

assert sys.meta_path is not mine
assert [e for e in monitor.events() if isinstance(e, MetaPathReassignment)]
mine.append(DummyFinder())  # mutates the stale list, not the live sys.meta_path
assert not any(isinstance(f, DummyFinder) for f in sys.meta_path)
print("OK")
"""


def test_reassignment_recovery_replaces_the_foreign_list_with_a_copy(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(REASSIGNED_LIST_GOES_STALE)
    assert "OK" in proc.stdout


STATEFUL_INDEX = """
import sys

import metapathology

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

class StatefulIndex:
    def __init__(self):
        self.calls = 0

    def __index__(self):
        result = self.calls
        self.calls += 1
        return result

metapathology.install(report_at_exit=False)

replacement = DummyFinder()
set_index = StatefulIndex()
sys.meta_path[set_index] = replacement
assert set_index.calls == 1, set_index.calls
assert sys.meta_path[0] is replacement

first = sys.meta_path[0]
delete_index = StatefulIndex()
del sys.meta_path[delete_index]
assert delete_index.calls == 1, delete_index.calls
assert first not in sys.meta_path

slice_replacement = DummyFinder()
set_slice_index = StatefulIndex()
sys.meta_path[slice(set_slice_index, 1)] = [slice_replacement]
assert set_slice_index.calls == 1, set_slice_index.calls
assert sys.meta_path[0] is slice_replacement

delete_slice_index = StatefulIndex()
del sys.meta_path[slice(delete_slice_index, 1)]
assert delete_slice_index.calls == 1, delete_slice_index.calls
assert slice_replacement not in sys.meta_path
print("OK")
"""


def test_item_mutations_evaluate_stateful_indices_once(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(STATEFUL_INDEX)
    assert "OK" in proc.stdout


FAILED_SORT_MUTATION = """
import sys

import metapathology
from metapathology import MetaPathMutation

class ComparableFinder:
    comparisons = 0

    def __init__(self, value):
        self.value = value

    def __lt__(self, other):
        type(self).comparisons += 1
        if type(self).comparisons == 4:
            raise RuntimeError("comparison failed")
        return self.value < other.value

    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
finders = [ComparableFinder(value) for value in [5, 1, 4, 2, 3]]
sys.meta_path[:] = finders
event_count = len(monitor.events())

try:
    sys.meta_path.sort()
except RuntimeError:
    pass
else:
    raise AssertionError("sort unexpectedly completed")

assert [finder.value for finder in sys.meta_path] != [5, 1, 4, 2, 3]
new_events = monitor.events()[event_count:]
assert any(isinstance(event, MetaPathMutation) and event.op == "sort" for event in new_events), new_events
print("OK")
"""


def test_sort_that_partially_mutates_before_raising_is_recorded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(FAILED_SORT_MUTATION)
    assert "OK" in proc.stdout
