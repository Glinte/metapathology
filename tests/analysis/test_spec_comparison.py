"""Semantic spec summaries and namespace comparisons."""

import threading
import types
from collections.abc import Iterator
from importlib.machinery import ModuleSpec
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from support import PythonRunner

from metapathology import ModuleSpecSnapshot, ObjectIdentity
from metapathology._report_analysis import _compare_specs, _current_state_spec_summary
from metapathology._report_model import FinderResult, StructuralComparison
from metapathology._spec import summarize_spec


def _summary(locations: list[str]) -> ModuleSpecSnapshot:
    spec = ModuleSpec("example", loader=None, is_package=True)
    spec.submodule_search_locations = locations
    return summarize_spec(spec, iterate_foreign_locations=False)[0]


@given(
    left=st.lists(st.sampled_from(("a", "b", "c", "d")), max_size=8),
    right=st.lists(st.sampled_from(("a", "b", "c", "d")), max_size=8),
)
def test_location_comparison_is_reflexive_and_reversible(left: list[str], right: list[str]) -> None:
    same = _compare_specs(_summary(left), _summary(left))
    forward = _compare_specs(_summary(left), _summary(right))
    reverse = _compare_specs(_summary(right), _summary(left))

    assert not same.has_differences()
    assert forward.only_in_left_result == reverse.only_in_right_result
    assert forward.only_in_right_result == reverse.only_in_left_result
    assert forward.locations_reordered is reverse.locations_reordered
    assert forward.package_status_differs is reverse.package_status_differs
    assert forward.origin_differs is reverse.origin_differs
    assert forward.loader_type_differs is reverse.loader_type_differs
    assert forward.cached_differs is reverse.cached_differs
    assert forward.has_differences() is reverse.has_differences()


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
    assert isinstance(hostile.origin, ObjectIdentity)
    assert hostile.cached is None


def test_result_comparison_preserves_package_origin_and_namespace_differences() -> None:
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
    assert extension_comparison.only_in_left_result == ("b",)


def test_result_value_comparison_reports_status_partial_evidence_and_differences() -> None:
    left = FinderResult._for_comparison("result:left", _summary(["a"]))
    right = FinderResult._for_comparison("result:right", None, status="not_found")

    comparison = left.compare(
        right,
        comparison_id="comparison:status",
        structural_comparison=StructuralComparison(False, False, (), ()),
    )

    assert comparison.status_differs is True
    assert comparison.complete is False
    assert comparison.right_locations_state == "unavailable"
    assert comparison.has_differences()


def test_deferred_locations_are_attempted_only_from_the_same_current_state_spec() -> None:
    class Locations:
        def __iter__(self) -> Iterator[object]:
            raise RuntimeError("broken locations")

    spec = ModuleSpec("example", loader=None, is_package=True)
    spec.submodule_search_locations = Locations()  # type: ignore[assignment]
    observed = summarize_spec(spec, iterate_foreign_locations=False)[0]
    module = types.ModuleType("example")
    module.__spec__ = spec

    enriched = _current_state_spec_summary(module, observed)

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
    assert summary.locations_state == "current_state"
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
results = [item for item in document["finder_results"] if item["module"] == name]
captured = next(item for item in results if item["kind"] == "observed_finder_result")
check = next(item for item in results if item["kind"] == "standard_path_check")
assert captured["search_path_kind"] == "sys_path"
assert captured["spec"]["locations_state"] == "captured"
assert check["spec"]["locations_state"] == "current_state"
comparison = next(
    item
    for item in document["finder_result_comparisons"]
    if item["left_result_ref"] == captured["id"] and item["right_result_ref"] == check["id"]
)
standard_only = comparison["only_in_right_result"]
assert len(standard_only) == 1
assert os.path.normcase(os.path.normpath(standard_only[0])) == os.path.normcase(os.path.normpath(second + "/" + name))
assert not any(item["module"] == name for item in document["findings"])
text = metapathology.render_report()
assert "modules found by a custom finder" in text, text
assert "[missing-namespace-locations]" not in text, text
print("OK")
"""


def test_namespace_result_difference_is_reported_neutrally_without_an_effect(
    python_runner: PythonRunner,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    name = "semantic_namespace"
    (first / name).mkdir(parents=True)
    (second / name).mkdir(parents=True)

    proc = python_runner.run_code_ok(NAMESPACE_TRUNCATION, name, first.as_posix(), second.as_posix())

    assert proc.stdout.strip() == "OK"


DEFERRED_LOCATIONS = r"""
import sys
from importlib.machinery import ModuleSpec

import metapathology
from metapathology import MetaPathFinderCall

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
finder.find_spec("deferred_check")
call = next(event for event in reversed(monitor.events()) if isinstance(event, MetaPathFinderCall))
assert call.spec_summary.locations_state == "deferred"
assert call.spec_summary.submodule_search_locations is None
print("OK")
"""


def test_foreign_location_sequences_are_deferred_out_of_the_hot_path(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(DEFERRED_LOCATIONS)
    assert proc.stdout.strip() == "OK"
