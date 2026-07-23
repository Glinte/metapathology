"""Bounded report-time probes of displaced importer-cache finders."""

import importlib.machinery

from support import PythonRunner

from metapathology import (
    DeepDiagnosticCall,
    ImporterCacheDiff,
    ImporterCacheReplacement,
    ModuleCacheState,
    MonitorEvent,
    ObjectRef,
)
from metapathology._displaced_finder_probe import probe_displaced_finders
from metapathology._report_model import ProbeRun, ResolutionRoute

PATH = "<virtual-entry>"


class _Finder:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls = 0

    def find_spec(
        self,
        fullname: str,
        target: object | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        self.calls += 1
        if self.result == "spec":
            return importlib.machinery.ModuleSpec(fullname, loader=None)
        if self.result == "raise":
            raise RuntimeError("boom")
        return None


def _evidence(
    finder: _Finder,
    names: list[str],
    *,
    targeted: bool = False,
) -> tuple[list[MonitorEvent], tuple[tuple[int, object], ...]]:
    """Build the smallest captured provenance chain accepted by the probe."""
    reference = ObjectRef.of(finder)
    replacement = ImporterCacheReplacement(path=PATH, before=reference, after=None)
    diff = ImporterCacheDiff(
        seq=1,
        observation="path_hooks.append",
        added=(),
        removed=(),
        replaced=(replacement,),
        non_string_keys_before=0,
        non_string_keys_after=0,
        thread_name="MainThread",
    )
    target_state = ModuleCacheState(state="missing") if targeted else None
    calls = [
        DeepDiagnosticCall(
            seq=index,
            boundary="path_entry_finder",
            object_id=99,
            object_type_name="CurrentFinder",
            fullname=name,
            path=PATH,
            outcome="not_found",
            exception_type_name=None,
            thread_name="MainThread",
            thread_id=1,
            target_state=target_state,
        )
        for index, name in enumerate(names, start=2)
    ]
    return [diff, *calls], ((id(finder), finder),)


def _probe(
    finder: _Finder,
    names: list[str],
    *,
    targeted: bool = False,
) -> tuple[tuple[ResolutionRoute, ...], ProbeRun]:
    events, retained = _evidence(finder, names, targeted=targeted)
    sequence = iter(range(1, 100))
    return probe_displaced_finders(
        events,
        retained,
        lambda: f"route:{next(sequence)}",
    )


def test_displaced_finder_outcomes_are_isolated_and_reported() -> None:
    cases = (
        ("spec", False, "found", None, 1),
        ("none", False, "not_found", None, 1),
        ("raise", False, "failed", "RuntimeError", 1),
        ("spec", True, "target_unavailable", None, 0),
    )

    for result, targeted, status, exception, expected_calls in cases:
        finder = _Finder(result)
        routes, run = _probe(finder, ["ghostmod"], targeted=targeted)

        assert len(routes) == run.results == 1
        route = routes[0]
        assert route.module == "ghostmod"
        assert route.search_path == (PATH,)
        assert route.status == status
        assert route.exception_type_name == exception
        assert route.finder_type_name == "_Finder"
        assert route.state_phase == "report"
        assert (route.spec_summary is not None) is (status == "found")
        assert finder.calls == run.foreign_calls == expected_calls


def test_probe_is_capped_and_reports_overflow() -> None:
    finder = _Finder("spec")
    routes, run = _probe(finder, [f"ghostmod_{index}" for index in range(20)])

    assert len(routes) == run.results == run.capacity == 16
    assert run.candidates == 20
    assert run.omitted == 4
    assert run.foreign_calls == finder.calls == 16


def test_repeated_probes_do_not_mutate_captured_evidence() -> None:
    finder = _Finder("none")
    events, retained = _evidence(finder, ["ghostmod"])
    before = tuple(events)

    first, _first_run = probe_displaced_finders(events, retained, lambda: "route:first")
    second, _second_run = probe_displaced_finders(events, retained, lambda: "route:second")

    assert tuple(events) == before
    assert len(first) == len(second) == 1


OFF_BY_DEFAULT = r"""
import json

import metapathology


class Finder:
    def find_spec(self, fullname, target=None):
        raise AssertionError("disabled probes must not call foreign finders")


metapathology.install(
    report_at_exit=False,
    capture=metapathology.CaptureConfig(
        importer_cache=True,
        deep=metapathology.DeepConfig(path_entry_finders=True),
    ),
)
document = json.loads(metapathology.render_report(format="json"))
run = next(probe for probe in document["probes"] if probe["kind"] == "displaced_finder")
assert run["status"] == "disabled"
assert run["foreign_calls"] == 0
assert not [
    route
    for route in document["resolution_routes"]
    if route["kind"] == "displaced_finder_probe"
]
print("OK")
"""


def test_probe_performs_no_foreign_calls_unless_requested(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(OFF_BY_DEFAULT)

    assert proc.stdout.strip() == "OK"
