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


def test_meta_path_changes_are_recorded_with_stacks(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "mutations")


def test_meta_path_replacement_is_detected_and_reinstrumented(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "reassignment")


def test_instrumented_meta_path_preserves_ordinary_list_operations(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "ordinary_list_operations")


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


def test_uninstall_preserves_replaced_import_lists(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "uninstall_preserves_replacements")


def test_extend_with_failing_iterator_matches_plain_list_semantics(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "extend_partial_iteration")


# uninstall() can run to completion while a mutation thread is still inside
# _instrument_finder for a newly added finder (the foreign find_spec attribute
# lookup can block arbitrarily long); the late wrapper must not survive.
def test_reassignment_recovery_replaces_the_foreign_list_with_a_copy(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "reassigned_list_goes_stale")


def test_item_mutations_evaluate_stateful_indices_once(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "stateful_index")


def test_sort_that_partially_mutates_before_raising_is_recorded(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/meta_path.py", "failed_sort_mutation")
