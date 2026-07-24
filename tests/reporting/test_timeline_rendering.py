"""Timeline text rendering, collapsing, and expansion behavior."""

from pathlib import Path

from support import PythonRunner


def test_run_of_four_or_more_collapsible_events_collapses(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/timeline_rendering.py", "collapse_run")


def test_detailed_events_render_in_plain_words_and_routine_runs_collapse(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/timeline_rendering.py", "collapse_detailed_run")


def test_finder_call_in_middle_of_collapsible_run_splits_the_collapse(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "finder_call_split_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    python_runner.run_scenario_ok("reporting/timeline_rendering.py", "claim_splits_run")


def test_event_referenced_by_explanation_renders_expanded_inside_a_run(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    omitted_root = tmp_path / "installed"
    child = omitted_root / "ref_ns" / "child"
    child.mkdir(parents=True)
    (child / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    finder_called = tmp_path / "editable" / "ref_ns"
    finder_called.mkdir(parents=True)

    python_runner.run_scenario_ok(
        "reporting/timeline_rendering.py", "finding_referenced_event", str(omitted_root), str(finder_called)
    )


def test_timeline_full_env_var_disables_collapsing(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/timeline_rendering.py", "timeline_full_escape_hatch")
