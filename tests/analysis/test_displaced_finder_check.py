"""Bounded report-time checks of displaced importer-cache finders."""

import importlib.machinery

from support import PythonRunner

from metapathology import (
    ImporterCacheChange,
    ImporterCacheReplacement,
    ImportMechanismCall,
    ModuleCacheState,
    MonitorEvent,
    ObjectIdentity,
)
from metapathology._displaced_finder_check import check_displaced_finders
from metapathology._report_model import CheckRun, FinderResult

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
    """Build the smallest captured provenance chain accepted by the check."""
    reference = ObjectIdentity.of(finder)
    replacement = ImporterCacheReplacement(path=PATH, before=reference, after=None)
    diff = ImporterCacheChange(
        sequence=1,
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
        ImportMechanismCall(
            sequence=index,
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


def _check(
    finder: _Finder,
    names: list[str],
    *,
    targeted: bool = False,
) -> tuple[tuple[FinderResult, ...], CheckRun]:
    events, retained = _evidence(finder, names, targeted=targeted)
    sequence = iter(range(1, 100))
    return check_displaced_finders(
        events,
        retained,
        lambda: f"result:{next(sequence)}",
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
        results, run = _check(finder, ["ghostmod"], targeted=targeted)

        assert len(results) == run.results == 1
        result = results[0]
        assert result.module == "ghostmod"
        assert result.search_path == (PATH,)
        assert result.status == status
        assert result.exception_type_name == exception
        assert result.finder_type_name == "_Finder"
        assert result.state_phase == "report"
        assert (result.spec_summary is not None) is (status == "found")
        assert finder.calls == run.foreign_calls == expected_calls


def test_check_is_capped_and_reports_overflow() -> None:
    finder = _Finder("spec")
    results, run = _check(finder, [f"ghostmod_{index}" for index in range(20)])

    assert len(results) == run.results == run.capacity == 16
    assert run.candidates == 20
    assert run.omitted == 4
    assert run.foreign_calls == finder.calls == 16


def test_repeated_checks_do_not_mutate_captured_evidence() -> None:
    finder = _Finder("none")
    events, retained = _evidence(finder, ["ghostmod"])
    before = tuple(events)

    first, _first_run = check_displaced_finders(events, retained, lambda: "result:first")
    second, _second_run = check_displaced_finders(events, retained, lambda: "result:second")

    assert tuple(events) == before
    assert len(first) == len(second) == 1


def test_check_performs_no_foreign_calls_unless_requested(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("analysis/displaced_finder_check.py", "off_by_default")
