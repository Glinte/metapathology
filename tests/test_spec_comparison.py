"""Semantic spec summaries and namespace comparisons."""

import subprocess
import threading
import types
from collections.abc import Callable, Iterator
from importlib.machinery import ModuleSpec
from pathlib import Path

from metapathology import ObjectRef, SpecSummary
from metapathology._report_analysis import _compare_specs, _post_hoc_spec_summary
from metapathology._spec import summarize_spec

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


def _summary(locations: list[str]) -> SpecSummary:
    spec = ModuleSpec("example", loader=None, is_package=True)
    spec.submodule_search_locations = locations
    return summarize_spec(spec, iterate_foreign_locations=False)[0]


def test_location_comparison_distinguishes_truncation_extension_and_reordering() -> None:
    truncated = _compare_specs(_summary(["a"]), _summary(["a", "b"]))
    extended = _compare_specs(_summary(["a", "b"]), _summary(["a"]))
    reordered = _compare_specs(_summary(["b", "a"]), _summary(["a", "b"]))
    duplicated = _compare_specs(_summary(["a"]), _summary(["a", "a"]))

    assert truncated.only_in_right_route == ("b",)
    assert extended.only_in_left_route == ("b",)
    assert reordered.locations_reordered is True
    assert duplicated.only_in_right_route == ("a",)


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
    assert isinstance(hostile.origin, ObjectRef)
    assert hostile.cached is None


def test_route_comparison_preserves_package_origin_and_namespace_differences() -> None:
    module_spec = ModuleSpec("example", loader=None)
    module = summarize_spec(module_spec, iterate_foreign_locations=False)[0]
    package = _summary(["a"])
    package_comparison = _compare_specs(module, package)
    assert package_comparison.package_status_differs is True

    first_spec = ModuleSpec("example", loader=None, origin="first.py")
    second_spec = ModuleSpec("example", loader=None, origin="second.py")
    first = summarize_spec(first_spec, iterate_foreign_locations=False)[0]
    second = summarize_spec(second_spec, iterate_foreign_locations=False)[0]
    origin_comparison = _compare_specs(first, second)
    assert origin_comparison.origin_differs is True

    extended = _summary(["a", "b"])
    base = _summary(["a"])
    extension_comparison = _compare_specs(extended, base)
    assert extension_comparison.only_in_left_route == ("b",)


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
routes = [item for item in document["resolution_routes"] if item["module"] == name]
captured = next(item for item in routes if item["kind"] == "captured_claim")
probe = next(item for item in routes if item["kind"] == "standard_path_probe")
assert captured["search_path_kind"] == "sys_path"
assert captured["spec"]["locations_state"] == "captured"
assert probe["spec"]["locations_state"] == "post_hoc"
comparison = next(
    item
    for item in document["route_comparisons"]
    if item["left_route_ref"] == captured["id"] and item["right_route_ref"] == probe["id"]
)
standard_only = comparison["only_in_right_route"]
assert len(standard_only) == 1
assert os.path.normcase(os.path.normpath(standard_only[0])) == os.path.normcase(os.path.normpath(second + "/" + name))
assert not any(item["module"] == name for item in document["findings"])
text = metapathology.render_report()
assert "modules found by a custom finder" in text, text
assert "[namespace-truncation]" not in text, text
print("OK")
"""


def test_namespace_route_difference_is_reported_neutrally_without_an_effect(
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
