"""Monitor behavior, exercised in fresh interpreters (see conftest)."""

import subprocess
from collections.abc import Callable
from pathlib import Path

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


def test_monitoring_region_restores_after_an_exception(run_python: RunPython) -> None:
    proc = run_python(
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
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_nested_monitoring_regions_share_lifecycle_and_enable_mechanisms(run_python: RunPython) -> None:
    proc = run_python(
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
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_monitoring_region_preserves_preexisting_installation(run_python: RunPython) -> None:
    proc = run_python(
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
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_overlapping_monitoring_regions_uninstall_after_last_exit(run_python: RunPython) -> None:
    proc = run_python(
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
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_install_warns_once_when_starting_on_unsupported_implementation(run_python: RunPython) -> None:
    proc = run_python(
        "import warnings\n"
        "import metapathology._monitor as monitor_module\n"
        "monitor_module._IMPLEMENTATION_NAME = 'pypy'\n"
        "monitor_module._UNSUPPORTED_IMPLEMENTATION_WARNING = (\n"
        "    \"metapathology supports CPython only; monitoring on 'pypy' may be incomplete or inaccurate\"\n"
        ")\n"
        "with warnings.catch_warnings(record=True) as caught:\n"
        "    warnings.simplefilter('always')\n"
        "    monitor = monitor_module.install(report_at_exit=False)\n"
        "    assert monitor_module.install(report_at_exit=False) is monitor\n"
        "monitor_module.uninstall()\n"
        "assert len(caught) == 1, caught\n"
        "warning = caught[0]\n"
        "assert warning.category is RuntimeWarning, warning.category\n"
        "assert str(warning.message) == monitor_module._UNSUPPORTED_IMPLEMENTATION_WARNING\n"
    )
    assert proc.returncode == 0, proc.stderr


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
assert wins[-1].module_state_before is not None
assert wins[-1].module_state_after is not None
assert wins[-1].module_state_before.state == "missing"
assert wins[-1].module_state_after.state == "missing"

# A claim without a filesystem origin must not be flagged as contention.
text = metapathology.render_report()
assert "[loader-displacement]" not in text, text
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


FINDER_MODULE_TRANSITIONS = """
import json
import sys

import metapathology
from metapathology import FindSpecCall

class SideEffectFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "identity_added":
            sys.modules[fullname] = object()
        elif fullname == "identity_none":
            sys.modules[fullname] = None
        elif fullname == "identity_removed":
            del sys.modules[fullname]
        elif fullname == "identity_replaced":
            sys.modules[fullname] = replacement
        elif fullname == "identity_raised":
            sys.modules[fullname] = object()
            raise RuntimeError("expected")
        return None

finder = SideEffectFinder()
sys.meta_path.insert(0, finder)
monitor = metapathology.install(report_at_exit=False)
original = object()
replacement = object()
sys.modules["identity_removed"] = original
sys.modules["identity_replaced"] = original

for name in ("identity_added", "identity_none", "identity_removed", "identity_replaced"):
    finder.find_spec(name)
try:
    finder.find_spec("identity_raised")
except RuntimeError:
    pass

calls = {
    event.fullname: event
    for event in monitor.events()
    if isinstance(event, FindSpecCall) and event.fullname.startswith("identity_")
}
assert calls["identity_added"].module_state_before.state == "missing"
assert calls["identity_added"].module_state_after.state == "object"
assert calls["identity_none"].module_state_after.state == "none"
assert calls["identity_removed"].module_state_before.object_id == id(original)
assert calls["identity_removed"].module_state_after.state == "missing"
assert calls["identity_replaced"].module_state_before.object_id == id(original)
assert calls["identity_replaced"].module_state_after.object_id == id(replacement)
assert calls["identity_raised"].exception_type_name == "RuntimeError"
assert calls["identity_raised"].module_state_after.state == "object"

document = json.loads(metapathology.render_report(format="json"))
event = next(
    item["data"]
    for item in document["timeline"]
    if item["kind"] == "find_spec_call" and item["data"]["fullname"] == "identity_replaced"
)
assert event["module_state_before"]["object_id"] == hex(id(original))
assert event["module_state_after"]["object_id"] == hex(id(replacement))
findings = [item for item in document["findings"] if item["kind"] == "finder_side_effect"]
assert {item["module"] for item in findings} == set(calls)
raised = next(item for item in findings if item["module"] == "identity_raised")
assert raised["evidence"]["level"] == "captured"
assert raised["evidence"]["outcome"] == "raised:RuntimeError"
explanation = next(
    item for item in document["explanations"]
    if item["kind"] == "finder_side_effect" and item["subject"] == "identity_replaced"
)
assert explanation["confidence"] == "captured"
assert explanation["state_before"]["object_id"] == hex(id(original))
assert explanation["state_after"]["object_id"] == hex(id(replacement))
text = metapathology.render_report()
assert "[finder-side-effect] 'identity_replaced'" in text, text
assert "nested activity was not observed" in text, text
assert "sys.modules object at" in text, text
assert "[captured] SideEffectFinder returned None after changing sys.modules['identity_replaced']" in text, text

for name in tuple(calls):
    sys.modules.pop(name, None)
print("OK")
"""


def test_find_spec_records_target_module_transitions(run_python: RunPython) -> None:
    proc = run_python(FINDER_MODULE_TRANSITIONS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


SETUPTOOLS_3073 = """
import sys

import metapathology

class DistutilsMetaFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "distutils":
            sys.modules[fullname] = replacement
        return None

previous = sys.modules.get("distutils", sentinel := object())
original = object()
replacement = object()
sys.modules["distutils"] = original
finder = DistutilsMetaFinder()
sys.meta_path.insert(0, finder)
metapathology.install(report_at_exit=False)
finder.find_spec("distutils")

text = metapathology.render_report()
assert "[finder-side-effect] 'distutils'" in text, text
assert "DistutilsMetaFinder" in text, text
assert "returning None" in text, text

if previous is sentinel:
    del sys.modules["distutils"]
else:
    sys.modules["distutils"] = previous
print("OK")
"""


def test_setuptools_3073_finder_side_effect_fixture(run_python: RunPython) -> None:
    proc = run_python(SETUPTOOLS_3073)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


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
from metapathology import ImportAuditStart

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
starts = {event.fullname for event in monitor.events() if isinstance(event, ImportAuditStart)}
assert starts >= set(module_names), (starts, module_names)
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


def test_uninstall_preserves_replaced_import_lists(run_python: RunPython) -> None:
    proc = run_python(UNINSTALL_PRESERVES_REPLACEMENTS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


UNINSTALL_PRESERVES_FINDER_REPLACEMENT = """
import sys

import metapathology

class Finder:
    def find_spec(self, fullname, path=None, target=None):
        return None

finder = Finder()
sys.meta_path.insert(0, finder)
metapathology.install(report_at_exit=False)

def replacement(fullname, path=None, target=None):
    return None

finder.find_spec = replacement
metapathology.uninstall()

assert finder.__dict__["find_spec"] is replacement
sys.meta_path.remove(finder)
print("OK")
"""


def test_uninstall_preserves_replaced_finder_shadow(run_python: RunPython) -> None:
    proc = run_python(UNINSTALL_PRESERVES_FINDER_REPLACEMENT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


# On a reinstall the audit hook is already registered and _enabled is set
# before the finder-instrumentation loop runs, so an import triggered from a
# finder's lazy find_spec attribute re-enters _reinstall on the same thread
# while install() still holds the non-reentrant reinstall lock.
LAZY_ATTRIBUTE_REINSTALL = """
import sys
import threading

import metapathology

class LazyAttributeFinder:
    # Lazy-import style: the first find_spec attribute access itself imports.
    triggered = False

    @property
    def find_spec(self):
        if not LazyAttributeFinder.triggered:
            LazyAttributeFinder.triggered = True
            import colorsys  # any not-yet-imported module: raises the import audit event
        return None  # not callable, so the finder is merely skipped

metapathology.install(report_at_exit=False)
metapathology.uninstall()
sys.meta_path.append(LazyAttributeFinder())

worker = threading.Thread(target=metapathology.install, kwargs={"report_at_exit": False}, daemon=True)
worker.start()
worker.join(timeout=5)
assert not worker.is_alive(), "install() deadlocked on its own reinstall lock"
assert LazyAttributeFinder.triggered
monitor = metapathology.get_monitor()
assert monitor is not None and monitor.enabled
print("OK")
"""


def test_reinstall_with_lazy_find_spec_attribute_does_not_deadlock(run_python: RunPython) -> None:
    proc = run_python(LAZY_ATTRIBUTE_REINSTALL)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


CROSS_THREAD_LIFECYCLE_LOCK_ORDER = """
import importlib._bootstrap as bootstrap
import sys
import threading

import metapathology

sys.path.insert(0, sys.argv[1])
metapathology.install(report_at_exit=False)
metapathology.uninstall()

finder_started = threading.Event()
module_lock_held = threading.Event()
failures = []

class LazyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

    def __getattribute__(self, name):
        if name == "find_spec" and threading.current_thread().name == "installer":
            finder_started.set()
            import blocked_dependency
        return object.__getattribute__(self, name)

sys.meta_path.insert(0, LazyFinder())

def import_while_holding_module_lock():
    lock = bootstrap._get_module_lock("blocked_dependency")
    lock.acquire()
    module_lock_held.set()
    try:
        assert finder_started.wait(5)
        import audit_probe
    except BaseException as exc:
        failures.append(exc)
    finally:
        lock.release()

holder = threading.Thread(target=import_while_holding_module_lock, name="module-lock-holder", daemon=True)
holder.start()
assert module_lock_held.wait(5)
installer = threading.Thread(
    target=metapathology.install,
    kwargs={"report_at_exit": False},
    name="installer",
    daemon=True,
)
installer.start()
installer.join(5)
holder.join(5)
assert not installer.is_alive(), "installer waited for a module lock while holding the lifecycle lock"
assert not holder.is_alive(), "audit recovery waited for the lifecycle lock while holding a module lock"
assert not failures, failures
print("OK")
"""


def test_install_does_not_hold_lifecycle_lock_across_finder_access(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "blocked_dependency.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "audit_probe.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = run_python(CROSS_THREAD_LIFECYCLE_LOCK_ORDER, str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


# install() shadows find_spec with setattr(), which lands in the slot, but
# uninstall() restores through finder.__dict__, which a __slots__ instance
# does not have; the suppressed AttributeError must not strand the wrapper.
SLOTTED_FINDER_RESTORE = """
import sys

import metapathology
from metapathology import FindSpecCall

class SlottedFinder:
    __slots__ = ("find_spec",)

def original_find_spec(fullname, path=None, target=None):
    return None

finder = SlottedFinder()
finder.find_spec = original_find_spec
sys.meta_path.insert(0, finder)

monitor = metapathology.install(report_at_exit=False)
metapathology.uninstall()

assert finder.find_spec is original_find_spec, finder.find_spec
finder.find_spec("after_uninstall_probe")
calls = [e for e in monitor.events() if isinstance(e, FindSpecCall) and e.fullname == "after_uninstall_probe"]
assert not calls, calls  # a stranded wrapper keeps recording after uninstall
print("OK")
"""


def test_uninstall_restores_slotted_finder_find_spec(run_python: RunPython) -> None:
    proc = run_python(SLOTTED_FINDER_RESTORE)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


# A data descriptor with a setter accepts install()'s setattr() without the
# wrapper ever reaching the instance dict, so uninstall()'s __dict__-based
# restore silently misses it.
DESCRIPTOR_FINDER_RESTORE = """
import sys

import metapathology

def original_find_spec(fullname, path=None, target=None):
    return None

class DescriptorFinder:
    def __init__(self):
        self._find_spec = original_find_spec

    @property
    def find_spec(self):
        return self._find_spec

    @find_spec.setter
    def find_spec(self, value):
        self._find_spec = value

finder = DescriptorFinder()
sys.meta_path.insert(0, finder)

metapathology.install(report_at_exit=False)
metapathology.uninstall()

assert finder.find_spec is original_find_spec, finder.find_spec
print("OK")
"""


def test_uninstall_restores_descriptor_backed_find_spec(run_python: RunPython) -> None:
    proc = run_python(DESCRIPTOR_FINDER_RESTORE)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


# install() documents report_at_exit as registering the atexit report, but the
# idempotence early-return skips that block when the monitor is already
# enabled, silently dropping the request.
REPORT_AT_EXIT_UPGRADE = """
import metapathology

metapathology.install(report_at_exit=False)
metapathology.install(report_at_exit=True)
print("OK")
"""


def test_install_can_enable_report_at_exit_after_first_install(run_python: RunPython) -> None:
    proc = run_python(REPORT_AT_EXIT_UPGRADE)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
    assert "== metapathology report ==" in proc.stderr


# list.extend() through an iterator that raises mid-iteration keeps the
# already-yielded prefix; the instrumented list materializes the iterable
# first and silently appends nothing, diverging from plain-list semantics.
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


def test_extend_with_failing_iterator_matches_plain_list_semantics(run_python: RunPython) -> None:
    proc = run_python(EXTEND_PARTIAL_ITERATION)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


# uninstall() can run to completion while a mutation thread is still inside
# _instrument_finder for a newly added finder (the foreign find_spec attribute
# lookup can block arbitrarily long); the late wrapper must not survive.
UNINSTALL_MUTATION_RACE = """
import sys
import threading

import metapathology

def original_find_spec(fullname, path=None, target=None):
    return None

in_lookup = threading.Event()
uninstall_done = threading.Event()

class SlowAttributeFinder:
    def __getattr__(self, name):
        if name == "find_spec":
            in_lookup.set()
            assert uninstall_done.wait(timeout=10)
            return original_find_spec
        raise AttributeError(name)

metapathology.install(report_at_exit=False)
finder = SlowAttributeFinder()

appender = threading.Thread(target=sys.meta_path.append, args=(finder,))
appender.start()
assert in_lookup.wait(timeout=10)
metapathology.uninstall()
uninstall_done.set()
appender.join(timeout=10)

assert finder.find_spec is original_find_spec, finder.__dict__.get("find_spec")
print("OK")
"""


def test_uninstall_concurrent_with_mutation_leaves_no_wrapper(run_python: RunPython) -> None:
    proc = run_python(UNINSTALL_MUTATION_RACE)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


MONITOR_SNAPSHOT = """
import metapathology
from metapathology._monitor_model import MonitorSnapshot

monitor = metapathology.install(report_at_exit=False)
import colorsys
snapshot = monitor._report_state()
assert isinstance(snapshot, MonitorSnapshot)
assert snapshot.enabled
assert snapshot.events
assert max(event.seq for event in snapshot.events) <= snapshot.cutoff_seq
assert snapshot.baseline_modules == monitor.baseline_modules
try:
    snapshot.cutoff_seq = 0
except AttributeError as exc:
    assert "read-only" in str(exc)
else:
    raise AssertionError("monitor snapshots must be immutable")
metapathology.uninstall()
print("OK")
"""


def test_report_state_is_one_named_immutable_snapshot(run_python: RunPython) -> None:
    proc = run_python(MONITOR_SNAPSHOT)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


# Characterization, not a bug: recovery from a reassignment can only
# copy-and-swap, because a plain list cannot be instrumented in place. The
# reassigner's own list object therefore goes stale once detection replaces it.
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


def test_reassignment_recovery_replaces_the_foreign_list_with_a_copy(run_python: RunPython) -> None:
    proc = run_python(REASSIGNED_LIST_GOES_STALE)
    assert proc.returncode == 0, proc.stderr
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


def test_item_mutations_evaluate_stateful_indices_once(run_python: RunPython) -> None:
    proc = run_python(STATEFUL_INDEX)
    assert proc.returncode == 0, proc.stderr
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


def test_sort_that_partially_mutates_before_raising_is_recorded(run_python: RunPython) -> None:
    proc = run_python(FAILED_SORT_MUTATION)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


HOSTILE_CLASS_NAME = """
import sys

import metapathology

class HostileMeta(type):
    def __getattribute__(cls, name):
        if name == "__name__":
            raise SystemExit(91)
        return super().__getattribute__(name)

class HostileClassFinder(metaclass=HostileMeta):
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        return None

sys.meta_path.insert(0, HostileClassFinder)
monitor = metapathology.install(report_at_exit=False)
assert monitor.enabled
assert any(name == "HostileClassFinder" for name, _reason in monitor.skipped_finders())
metapathology.uninstall()
print("OK")
"""


def test_hostile_class_name_lookup_cannot_break_install(run_python: RunPython) -> None:
    proc = run_python(HOSTILE_CLASS_NAME)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


HOSTILE_CALLABLE_METADATA = """
import sys

import metapathology

class HostileCallable:
    def __call__(self, fullname, path=None, target=None):
        return None

    def __getattribute__(self, name):
        if name in {"__module__", "__name__", "__qualname__", "__doc__", "__annotations__"}:
            raise SystemExit(92)
        return super().__getattribute__(name)

class DummyFinder:
    pass

finder = DummyFinder()
original = HostileCallable()
finder.find_spec = original
sys.meta_path.insert(0, finder)

monitor = metapathology.install(report_at_exit=False)
assert monitor.enabled
assert finder.find_spec("probe") is None
assert finder.find_spec.__wrapped__ is original
metapathology.uninstall()
assert isinstance(finder.find_spec, HostileCallable)
print("OK")
"""


def test_callable_metadata_lookup_cannot_break_install(run_python: RunPython) -> None:
    proc = run_python(HOSTILE_CALLABLE_METADATA)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


WRAPPER_INTROSPECTION = """
import inspect
import sys

import metapathology

class DummyFinder:
    def find_spec(self, fullname: str, path=None, target=None) -> None:
        '''Finder documentation.'''
        return None

finder = DummyFinder()
original = finder.find_spec
sys.meta_path.insert(0, finder)
metapathology.install(report_at_exit=False)

wrapped = finder.find_spec
unwrapped = inspect.unwrap(wrapped)
assert unwrapped.__self__ is finder
assert unwrapped.__func__ is DummyFinder.find_spec
assert inspect.signature(wrapped) == inspect.signature(unwrapped)
for attribute in ("__module__", "__name__", "__qualname__", "__doc__", "__annotations__"):
    assert getattr(wrapped, attribute) == getattr(original, attribute), attribute
print("OK")
"""


def test_finder_wrapper_preserves_unwrap_and_signature_introspection(run_python: RunPython) -> None:
    proc = run_python(WRAPPER_INTROSPECTION)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


HOSTILE_SPEC_METADATA = """
import sys

import metapathology

install = metapathology.install

class HostileSpec:
    accessed = False
    @property
    def loader(self):
        self.accessed = True
        raise SystemExit(93)

class DummyFinder:
    def __init__(self, spec):
        self.spec = spec

    def find_spec(self, fullname, path=None, target=None):
        return self.spec

spec = HostileSpec()
finder = DummyFinder(spec)
sys.meta_path.insert(0, finder)
monitor = install(report_at_exit=False)

result = finder.find_spec("probe")
assert result is spec
assert not spec.accessed
call = monitor.events()[-1]
assert call.spec_summary.unavailable_fields
print("OK")
"""


def test_foreign_spec_descriptors_are_not_invoked(run_python: RunPython) -> None:
    proc = run_python(HOSTILE_SPEC_METADATA)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
