"""Opt-in ``sys.path`` mutation and reassignment observation."""

from pathlib import Path

from support import PythonRunner


def test_sys_path_monitor_is_opt_in_reversible_and_reports_mutations(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "sys_path_recovery_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    python_runner.run_scenario_ok(
        "monitoring/sys_path.py",
        "sys_path_monitor_is_opt_in_reversible_and_reports_mutations",
        str(tmp_path),
    )


def test_default_install_preserves_plain_sys_path(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/sys_path.py", "default_install_preserves_plain_sys_path")
