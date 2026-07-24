"""Explicitly unsafe import-time exploration of skipped resolution branches."""

from support import PythonRunner


def test_meta_path_exploration_calls_all_later_candidates_without_loading(
    python_runner: PythonRunner,
) -> None:
    python_runner.run_scenario_ok("monitoring/unsafe_import_branch_exploration.py", "meta_path_exploration")


def test_path_hook_exploration_calls_returned_finder_without_loader(
    python_runner: PythonRunner,
) -> None:
    python_runner.run_scenario_ok("monitoring/unsafe_import_branch_exploration.py", "path_hook_exploration")


def test_detailed_capture_does_not_enable_unsafe_exploration(
    python_runner: PythonRunner,
) -> None:
    python_runner.run_scenario_ok("monitoring/unsafe_import_branch_exploration.py", "default_off")


def test_unsafe_exploration_preserves_profiler_and_reports_partial_coverage(
    python_runner: PythonRunner,
) -> None:
    python_runner.run_scenario_ok("monitoring/unsafe_import_branch_exploration.py", "partial_coverage")
