"""T11 semantic spec summaries and namespace comparisons."""

import subprocess
import threading
import types
from collections.abc import Callable, Iterator
from importlib.machinery import ModuleSpec
from pathlib import Path

from metapathology import FindSpecCall, ImportObjectRef, SpecSummary
from metapathology._report_data import _compare_specs, _post_hoc_spec_summary, _spec_finding_kind
from metapathology._spec import summarize_spec

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


def _summary(locations: list[str]) -> SpecSummary:
    spec = ModuleSpec("example", loader=None, is_package=True)
    spec.submodule_search_locations = locations
    return summarize_spec(spec, iterate_foreign_locations=False)[0]


def _winner(summary: SpecSummary, origin: str | None = None) -> FindSpecCall:
    return FindSpecCall(1, "example", "Finder", 1, True, None, origin, (), "sys_path", summary, None, "MainThread")


def test_location_comparison_distinguishes_truncation_extension_and_reordering() -> None:
    truncated = _compare_specs(_summary(["a"]), _summary(["a", "b"]))
    extended = _compare_specs(_summary(["a", "b"]), _summary(["a"]))
    reordered = _compare_specs(_summary(["b", "a"]), _summary(["a", "b"]))
    duplicated = _compare_specs(_summary(["a"]), _summary(["a", "a"]))

    assert truncated.omitted_locations == ("b",)
    assert extended.additional_locations == ("b",)
    assert reordered.locations_reordered is True
    assert duplicated.omitted_locations == ("a",)


def test_cached_path_is_captured_only_from_an_exact_string_origin() -> None:
    spec = ModuleSpec("example", loader=None, origin="example.py")
    spec.has_location = True
    summary = summarize_spec(spec, iterate_foreign_locations=False)[0]

    class HostileString(str):
        def __str__(self) -> str:
            raise AssertionError("foreign string conversion")

    hostile_spec = ModuleSpec("hostile", loader=None, origin=HostileString("hostile.py"))
    hostile_spec.has_location = True
    hostile = summarize_spec(hostile_spec, iterate_foreign_locations=False)[0]

    assert type(summary.cached) is str
    assert isinstance(hostile.origin, ImportObjectRef)
    assert hostile.cached is None


def test_finding_precedence_covers_package_origin_and_generic_differences() -> None:
    module_spec = ModuleSpec("example", loader=None)
    module = summarize_spec(module_spec, iterate_foreign_locations=False)[0]
    package = _summary(["a"])
    package_comparison = _compare_specs(module, package)
    assert _spec_finding_kind(_winner(module), module, package, package_comparison) == "package_displacement"

    first_spec = ModuleSpec("example", loader=None, origin="first.py")
    second_spec = ModuleSpec("example", loader=None, origin="second.py")
    first = summarize_spec(first_spec, iterate_foreign_locations=False)[0]
    second = summarize_spec(second_spec, iterate_foreign_locations=False)[0]
    origin_comparison = _compare_specs(first, second)
    assert _spec_finding_kind(_winner(first, "first.py"), first, second, origin_comparison) == "origin_displacement"

    extended = _summary(["a", "b"])
    base = _summary(["a"])
    extension_comparison = _compare_specs(extended, base)
    assert _spec_finding_kind(_winner(extended), extended, base, extension_comparison) == "spec_difference"


def test_deferred_locations_are_attempted_only_from_the_same_post_hoc_spec() -> None:
    class Locations:
        def __iter__(self) -> Iterator[object]:
            raise RuntimeError("broken locations")

    spec = ModuleSpec("example", loader=None, is_package=True)
    spec.submodule_search_locations = Locations()  # type: ignore[assignment]
    observed = summarize_spec(spec, iterate_foreign_locations=False)[0]
    module = types.ModuleType("example")
    module.__spec__ = spec

    enriched = _post_hoc_spec_summary(module, observed)

    assert observed.locations_state == "deferred"
    assert enriched.locations_state == "failed"
    assert enriched.unavailable_fields == ("submodule_search_locations:RuntimeError",)


def test_report_time_location_copy_tolerates_coordinated_concurrent_change() -> None:
    iteration_started = threading.Event()
    mutation_finished = threading.Event()

    class Locations:
        def __init__(self) -> None:
            self.values = ["a"]

        def __iter__(self) -> Iterator[object]:
            iteration_started.set()
            assert mutation_finished.wait(timeout=2)
            yield from self.values

    locations = Locations()
    spec = ModuleSpec("example", loader=None, is_package=True)
    spec.submodule_search_locations = locations  # type: ignore[assignment]

    def mutate() -> None:
        assert iteration_started.wait(timeout=2)
        locations.values.append("b")
        mutation_finished.set()

    thread = threading.Thread(target=mutate)
    thread.start()
    summary = summarize_spec(spec, iterate_foreign_locations=True)[0]
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert summary.locations_state == "post_hoc"
    assert summary.submodule_search_locations == ("a", "b")


NAMESPACE_TRUNCATION = r"""
import json
import os
import sys
from importlib.machinery import ModuleSpec

import metapathology

class NamespaceLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        return None

class TruncatingFinder:
    def __init__(self, name, location):
        self.name = name
        self.location = location
    def find_spec(self, fullname, path=None, target=None):
        if fullname != self.name:
            return None
        spec = ModuleSpec(fullname, NamespaceLoader(), is_package=True)
        spec.submodule_search_locations = [self.location]
        return spec

name, first, second = sys.argv[1:]
sys.path[:0] = [first, second]
metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, TruncatingFinder(name, first + "/" + name))
__import__(name)

document = json.loads(metapathology.render_report(format="json"))
finding = next(item for item in document["findings"] if item["module"] == name)
assert finding["kind"] == "namespace_truncation", finding
assert finding["claim"]["search_path_kind"] == "sys_path"
assert finding["claim"]["spec"]["locations_state"] == "captured"
assert finding["path_finder_replay"]["spec"]["locations_state"] == "post_hoc"
omitted = finding["spec_comparison"]["omitted_locations"]
assert len(omitted) == 1
assert os.path.normcase(os.path.normpath(omitted[0])) == os.path.normcase(os.path.normpath(second + "/" + name))
text = metapathology.render_report()
assert "[namespace-truncation]" in text, text
print("OK")
"""


def test_namespace_truncation_is_reported_from_real_namespace_paths(
    run_python: RunPython,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    name = "semantic_namespace"
    (first / name).mkdir(parents=True)
    (second / name).mkdir(parents=True)

    proc = run_python(NAMESPACE_TRUNCATION, name, first.as_posix(), second.as_posix())

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


DEFERRED_LOCATIONS = r"""
import sys
from importlib.machinery import ModuleSpec

import metapathology
from metapathology import FindSpecCall

class HostileLocations:
    def __iter__(self):
        raise AssertionError("hot-path iteration")

class Finder:
    def find_spec(self, fullname, path=None, target=None):
        spec = ModuleSpec(fullname, loader=None, is_package=True)
        spec.submodule_search_locations = HostileLocations()
        return spec

finder = Finder()
monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, finder)
finder.find_spec("deferred_probe")
call = next(event for event in reversed(monitor.events()) if isinstance(event, FindSpecCall))
assert call.spec_summary.locations_state == "deferred"
assert call.spec_summary.submodule_search_locations is None
print("OK")
"""


def test_foreign_location_sequences_are_deferred_out_of_the_hot_path(run_python: RunPython) -> None:
    proc = run_python(DEFERRED_LOCATIONS)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
