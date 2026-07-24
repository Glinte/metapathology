"""Detailed import-result capture and profiler interaction."""

import json
from pathlib import Path

from support import PythonRunner


def test_detailed_monitoring_preserves_import_results(python_runner: PythonRunner) -> None:
    outcomes = []
    for mode in ("disabled", "default", "detailed"):
        proc = python_runner.run_scenario_ok("monitoring/detailed_import_results.py", "equivalent_outcomes", mode)
        assert proc.returncode == 0, proc.stderr
        outcomes.append(json.loads(proc.stdout))
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_shared_loader_uses_each_call_actual_module_name(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_import_results.py", "shared_loader_uses_each_call_actual_module_name"
    )


def test_partial_detailed_install_and_hostile_cleanup_are_isolated(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_import_results.py", "partial_detailed_install_and_hostile_cleanup_are_isolated"
    )


def test_exact_import_results_and_runtime_coverage(python_runner: PythonRunner, tmp_path: Path) -> None:
    for name in ("detailed_outcome_nested", "detailed_outcome_cached", "detailed_outcome_threaded"):
        (tmp_path / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "detailed_outcome_loaded.py").write_text(
        "import detailed_outcome_nested\nVALUE = 1\n", encoding="utf-8"
    )
    python_runner.run_scenario_ok("monitoring/detailed_import_results.py", "import_results")


def test_import_failed_after_state_change_requires_mutation_inside_failed_attempt(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "mutated_during_resolution.py").write_text(
        "import sys\nsys.meta_path.append(object())\nraise RuntimeError('broken')\n",
        encoding="utf-8",
    )
    python_runner.run_scenario_ok(
        "monitoring/detailed_import_results.py",
        "import_failed_after_state_change_requires_mutation_inside_failed_attempt",
        str(tmp_path),
    )


def test_detailed_outcomes_capture_path_finder_aggregate(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "standard_aggregate_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    python_runner.run_scenario_ok(
        "monitoring/detailed_import_results.py",
        "detailed_outcomes_capture_path_finder_aggregate",
        str(tmp_path),
    )


def test_exact_outcomes_refuse_an_existing_profiler(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/detailed_import_results.py", "exact_outcomes_refuse_an_existing_profiler")


def test_exact_outcomes_preserve_later_profiler_replacements(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_import_results.py", "exact_outcomes_preserve_later_profiler_replacements"
    )
