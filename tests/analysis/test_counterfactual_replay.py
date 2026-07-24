"""Resolution-result check evidence and failure isolation."""

from pathlib import Path

from support import PythonRunner


def test_hook_reorder_and_cache_clear_change_current_state_check_loader(
    python_runner: PythonRunner,
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "counterfactual"
    module_dir.mkdir()
    (module_dir / "counterfactual_target.py").write_text("VALUE = 7\n")

    python_runner.run_scenario_ok("analysis/counterfactual_replay.py", "changed_loader", str(module_dir))


def test_current_state_check_failure_is_reported_without_blocking_cleanup(
    python_runner: PythonRunner,
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "failed_replay"
    module_dir.mkdir()
    source = module_dir / "failed_replay_target.py"
    source.write_text("VALUE = 7\n")

    python_runner.run_scenario_ok("analysis/counterfactual_replay.py", "failed_replay", str(module_dir), str(source))


def test_standard_path_check_does_not_record_detailed_evidence_or_wrapper_mutation(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "isolated_check_target.py").write_text("VALUE = 7\n", encoding="utf-8")

    python_runner.run_scenario_ok("analysis/counterfactual_replay.py", "report_check_isolation", str(tmp_path))


def test_standard_path_check_preserves_an_exact_reload_target(python_runner: PythonRunner, tmp_path: Path) -> None:
    source = tmp_path / "reload_check_target.py"
    source.write_text("VALUE = 7\n", encoding="utf-8")

    python_runner.run_scenario_ok("analysis/counterfactual_replay.py", "reload_target", str(tmp_path), str(source))
