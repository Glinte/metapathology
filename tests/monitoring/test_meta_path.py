"""Observed ``sys.meta_path`` list and reassignment semantics."""

from types import FrameType

from hypothesis import given
from hypothesis import strategies as st
from support import PythonRunner

from metapathology._instrumented_list import _InstrumentedList

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
from metapathology import MetaPathChange

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

mutations = [e for e in monitor.events() if isinstance(e, MetaPathChange)]
ops = [m.op for m in mutations]
assert ops == ["insert", "remove", "append", "pop", "__setitem__", "__delitem__"], ops
assert mutations[0].added == ("DummyFinder",)
assert mutations[1].removed == ("DummyFinder",)
assert any(frame.filename == "<string>" for frame in mutations[0].stack), list(mutations[0].stack)
print("OK")
"""


def test_meta_path_changes_are_recorded_with_stacks(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(MUTATIONS)
    assert "OK" in proc.stdout


REASSIGNMENT = """
import sys
import metapathology
from metapathology import MetaPathReplacement

monitor = metapathology.install(report_at_exit=False)
instrumented_type = type(sys.meta_path)
sys.meta_path = list(sys.meta_path)  # blow away the instrumentation
assert type(sys.meta_path) is list

import colorsys  # any not-yet-imported module: triggers the import audit event

reassignments = [e for e in monitor.events() if isinstance(e, MetaPathReplacement)]
assert len(reassignments) == 1, reassignments
assert reassignments[0].during_import == "colorsys"
assert type(sys.meta_path) is instrumented_type  # reinstalled
print("OK")
"""


def test_meta_path_replacement_is_detected_and_reinstrumented(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(REASSIGNMENT)
    assert "OK" in proc.stdout


IN_PLACE_REPEAT = """
import sys

import metapathology
from metapathology import MetaPathChange

monitor = metapathology.install(report_at_exit=False)

sys.meta_path *= 2
ops = [event.op for event in monitor.events() if isinstance(event, MetaPathChange)]
assert "__imul__" in ops, ops
print("OK")
"""


def test_in_place_repeat_mutation_is_recorded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(IN_PLACE_REPEAT)
    assert "OK" in proc.stdout


SORT_MUTATION = """
import sys

import metapathology
from metapathology import MetaPathChange

monitor = metapathology.install(report_at_exit=False)

sys.meta_path.sort(key=id)
ops = [event.op for event in monitor.events() if isinstance(event, MetaPathChange)]
assert "sort" in ops, ops
print("OK")
"""


def test_sort_mutation_is_recorded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(SORT_MUTATION)
    assert "OK" in proc.stdout


DETAILED_COPY = """
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


def test_instrumented_meta_path_can_be_detailed_copied(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(DETAILED_COPY)
    assert "OK" in proc.stdout


class _RecordingList(_InstrumentedList[object]):
    """Exercise shared mutation semantics without installing an audit hook."""

    __slots__ = ("operations",)

    def __init__(self, contents: list[object]) -> None:
        list.__init__(self, contents)
        self.operations: list[str] = []

    def _notify(
        self,
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: FrameType,
    ) -> None:
        del added, removed, frame
        self.operations.append(op)


_RECORDED_OPERATION = {
    "del_item": "__delitem__",
    "del_slice": "__delitem__",
    "iadd": "__iadd__",
    "imul": "__imul__",
    "set_item": "__setitem__",
    "set_slice": "__setitem__",
}


def _apply_mutation(  # noqa: C901, PLR0912, PLR0915
    actual: _RecordingList,
    expected: list[object],
    operation: MutationOperation,
) -> bool:
    """Apply one generated operation; the explicit dispatch is the tested vocabulary."""
    op, first, second = operation
    if op == "append":
        item = object()
        actual.append(item)
        expected.append(item)
    elif op == "clear":
        actual.clear()
        expected.clear()
    elif op == "del_item":
        if not expected:
            return False
        index = first % len(expected)
        del actual[index]
        del expected[index]
    elif op == "del_slice":
        index = slice(first, second)
        del actual[index]
        del expected[index]
    elif op == "extend":
        items = [object() for _ in range(abs(first) % 3)]
        actual.extend(iter(items))
        expected.extend(items)
    elif op == "iadd":
        items = [object() for _ in range(abs(first) % 3)]
        assert actual.__iadd__(iter(items)) is actual
        expected += items
    elif op == "imul":
        multiplier = abs(first) % 3
        assert actual.__imul__(multiplier) is actual
        expected *= multiplier
    elif op == "insert":
        item = object()
        actual.insert(first, item)
        expected.insert(first, item)
    elif op == "pop":
        if not expected:
            return False
        index = first % len(expected)
        assert actual.pop(index) is expected.pop(index)
    elif op == "reverse":
        actual.reverse()
        expected.reverse()
    elif op == "set_item":
        if not expected:
            return False
        index = first % len(expected)
        item = object()
        actual[index] = item
        expected[index] = item
    elif op == "set_slice":
        index = slice(first, second)
        items = [object() for _ in range(abs(first - second) % 3)]
        actual[index] = iter(items)
        expected[index] = items
    elif op == "sort":
        actual.sort(key=id, reverse=bool(first % 2))
        expected.sort(key=id, reverse=bool(first % 2))
    else:
        raise AssertionError(op)
    return True


@given(operations=MUTATION_OPERATIONS)
def test_generated_mutation_sequences_match_plain_list(operations: list[MutationOperation]) -> None:
    initial = [object(), object(), object()]
    actual = _RecordingList(initial)
    expected = list(initial)
    expected_ops: list[str] = []

    for operation in operations:
        if not _apply_mutation(actual, expected, operation):
            continue
        expected_ops.append(_RECORDED_OPERATION.get(operation[0], operation[0]))
        assert len(actual) == len(expected)
        assert all(observed is wanted for observed, wanted in zip(actual, expected))

    assert actual.operations == expected_ops


UNINSTALL_PRESERVES_REPLACEMENTS = """
import sys

import metapathology

original_meta_path = sys.meta_path
original_path_hooks = sys.path_hooks
original_sys_path = sys.path
monitor = metapathology.install(
    report_at_exit=False,
    capture=metapathology.CaptureConfig(path_hooks=True, sys_path=True),
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
from metapathology import MetaPathChange

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

added = [event.added for event in monitor.events() if isinstance(event, MetaPathChange)]
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
from metapathology import MetaPathReplacement

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
mine = list(sys.meta_path)
sys.meta_path = mine

import colorsys  # first uncached import: detection swaps in an instrumented copy

assert sys.meta_path is not mine
assert [e for e in monitor.events() if isinstance(e, MetaPathReplacement)]
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
from metapathology import MetaPathChange

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
assert any(isinstance(event, MetaPathChange) and event.op == "sort" for event in new_events), new_events
print("OK")
"""


def test_sort_that_partially_mutates_before_raising_is_recorded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(FAILED_SORT_MUTATION)
    assert "OK" in proc.stdout
