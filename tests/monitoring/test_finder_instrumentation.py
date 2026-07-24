"""Finder attribution, wrapping, restoration, and hostile-object safety."""

from support import PythonRunner

ATTRIBUTION = """
import importlib.abc
import sys
from importlib.machinery import ModuleSpec

import metapathology
from metapathology import MetaPathFinderCall

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

calls = [e for e in monitor.events() if isinstance(e, MetaPathFinderCall)]
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


def test_meta_path_finder_calls_are_attributed_to_the_claiming_finder(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(ATTRIBUTION)
    assert "OK" in proc.stdout


FIND_SPEC_METADATA_SNAPSHOTS = """
import sys

import metapathology
from metapathology import MetaPathFinderCall

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

finder = DummyFinder()
sys.meta_path.insert(0, finder)
monitor = metapathology.install(report_at_exit=False)

shared_path = ["first", "second"]
finder.find_spec("first_call", shared_path)
finder.find_spec("second_call", shared_path)
calls = [event for event in monitor.events() if isinstance(event, MetaPathFinderCall)]
assert calls[-2].search_path == ("first", "second")
assert calls[-1].search_path == ("first", "second")
assert calls[-2].finder_id == id(finder)
assert calls[-1].finder_id == id(finder)
assert calls[-2].finder_type_name == "DummyFinder"
assert calls[-1].finder_type_name == "DummyFinder"

shared_path.append("third")
finder.find_spec("changed_path", shared_path)
calls = [event for event in monitor.events() if isinstance(event, MetaPathFinderCall)]
assert calls[-3].search_path == ("first", "second")
assert calls[-1].search_path == ("first", "second", "third")

for index in range(10):
    finder.find_spec(f"distinct_{index}", [f"path_{index}"])
calls = [event for event in monitor.events() if isinstance(event, MetaPathFinderCall)]
assert [call.search_path for call in calls[-10:]] == [(f"path_{index}",) for index in range(10)]
assert all(call.finder_id == id(finder) for call in calls)
print("OK")
"""


def test_find_spec_records_preserve_metadata_and_path_snapshots(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(FIND_SPEC_METADATA_SNAPSHOTS)
    assert "OK" in proc.stdout


FINDER_MODULE_TRANSITIONS = """
import json
import sys

import metapathology
from metapathology import MetaPathFinderCall

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
    if isinstance(event, MetaPathFinderCall) and event.fullname.startswith("identity_")
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

document = metapathology.get_report()
event = next(
    item["data"]
    for item in document["timeline"]
    if item["kind"] == "meta_path_finder_call" and item["data"]["fullname"] == "identity_replaced"
)
assert event["module_state_before"]["object_id"] == hex(id(original))
assert event["module_state_after"]["object_id"] == hex(id(replacement))
findings = [item for item in document["findings"] if item["kind"] == "finder_changed_module_cache"]
assert {item["module"] for item in findings} == set(calls)
raised = next(item for item in findings if item["module"] == "identity_raised")
assert raised["evidence"]["level"] == "observed"
assert raised["evidence"]["outcome"] == "raised:RuntimeError"
explanation = next(
    item for item in document["explanations"]
    if item["kind"] == "finder_changed_module_cache" and item["subject"] == "identity_replaced"
)
assert explanation["confidence"] == "observed"
assert explanation["state_before"]["object_id"] == hex(id(original))
assert explanation["state_after"]["object_id"] == hex(id(replacement))
text = metapathology.render_report()
assert "[finder-changed-module-cache] 'identity_replaced'" in text, text
assert "nested activity was not observed" in text, text
assert "sys.modules object at" in text, text
assert "[observed] SideEffectFinder returned None after changing sys.modules['identity_replaced']" in text, text

for name in tuple(calls):
    sys.modules.pop(name, None)
print("OK")
"""


def test_find_spec_records_target_module_transitions(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(FINDER_MODULE_TRANSITIONS)
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
assert "[finder-changed-module-cache] 'distutils'" in text, text
assert "DistutilsMetaFinder" in text, text
assert "returning None" in text, text

if previous is sentinel:
    del sys.modules["distutils"]
else:
    sys.modules["distutils"] = previous
print("OK")
"""


def test_setuptools_3073_finder_changed_module_cache_fixture(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(SETUPTOOLS_3073)
    assert proc.stdout.strip() == "OK"


UNINSTALL_STOPS_RECORDING = """
import sys
import metapathology
from metapathology import MetaPathChange, MetaPathReplacement

class DummyFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
metapathology.uninstall()

sys.meta_path.insert(0, DummyFinder())
sys.meta_path = list(sys.meta_path)
import colorsys  # audit hook must stay inert after uninstall

events = monitor.events()
assert not any(isinstance(e, (MetaPathChange, MetaPathReplacement)) for e in events), events
assert type(sys.meta_path) is list
print("OK")
"""


def test_uninstall_makes_all_layers_inert(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(UNINSTALL_STOPS_RECORDING)
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


def test_uninstall_restores_preexisting_instance_find_spec(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(UNINSTALL_RESTORES_INSTANCE_FIND_SPEC)
    assert "OK" in proc.stdout


HOSTILE_FINDER = """
import sys

import metapathology
from metapathology import MonitoringError

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
errors = [event for event in monitor.events() if isinstance(event, MonitoringError)]
assert any(event.where == "instrument_finder" for event in errors), errors
assert HostileError.stringify_count == 0
print("OK")
"""


def test_instrumentation_failure_does_not_escape_or_stringify_foreign_exception(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(HOSTILE_FINDER)
    assert "OK" in proc.stdout


REENTRANT_FINDER = """
import sys

import metapathology
from metapathology import MetaPathFinderCall

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
    if isinstance(event, MetaPathFinderCall) and event.finder_id == id(finder)
]
names = [event.fullname for event in calls]
assert "fractions" in names, calls
assert "colorsys" not in names, calls
print("OK")
"""


def test_finder_wrapper_is_reentrancy_guarded(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(REENTRANT_FINDER)
    assert "OK" in proc.stdout


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


def test_uninstall_preserves_replaced_finder_shadow(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(UNINSTALL_PRESERVES_FINDER_REPLACEMENT)
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


def test_reinstall_with_lazy_find_spec_attribute_does_not_deadlock(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(LAZY_ATTRIBUTE_REINSTALL)
    assert "OK" in proc.stdout


SLOTTED_FINDER_RESTORE = """
import sys

import metapathology
from metapathology import MetaPathFinderCall

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
finder.find_spec("after_uninstall_check")
calls = [e for e in monitor.events() if isinstance(e, MetaPathFinderCall) and e.fullname == "after_uninstall_check"]
assert not calls, calls  # a stranded wrapper keeps recording after uninstall
print("OK")
"""


def test_uninstall_restores_slotted_finder_find_spec(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(SLOTTED_FINDER_RESTORE)
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


def test_uninstall_restores_descriptor_backed_find_spec(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(DESCRIPTOR_FINDER_RESTORE)
    assert "OK" in proc.stdout


# install() documents report_at_exit as registering the atexit report, but the
# idempotence early-return skips that block when the monitor is already
# enabled, silently dropping the request.
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


def test_hostile_class_name_lookup_cannot_break_install(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(HOSTILE_CLASS_NAME)
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
assert finder.find_spec("check") is None
assert finder.find_spec.__wrapped__ is original
metapathology.uninstall()
assert isinstance(finder.find_spec, HostileCallable)
print("OK")
"""


def test_callable_metadata_lookup_cannot_break_install(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(HOSTILE_CALLABLE_METADATA)
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


def test_finder_wrapper_preserves_unwrap_and_signature_introspection(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(WRAPPER_INTROSPECTION)
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

result = finder.find_spec("check")
assert result is spec
assert not spec.accessed
call = monitor.events()[-1]
assert call.spec_summary.unavailable_fields
print("OK")
"""


def test_foreign_spec_descriptors_are_not_invoked(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(HOSTILE_SPEC_METADATA)
    assert "OK" in proc.stdout
