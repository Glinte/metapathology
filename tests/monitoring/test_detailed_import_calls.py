"""Detailed ``builtins.__import__`` observation, including cache-hit imports."""

from pathlib import Path

from support import PythonRunner


def test_cache_hits_are_observed_and_restored(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "cache_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    python_runner.run_scenario_ok("monitoring/detailed_import_calls.py", "cache_hit")


def test_fromlist_and_relative_level_are_captured(python_runner: PythonRunner, tmp_path: Path) -> None:
    package = tmp_path / "importcalls_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "leaf.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "driver.py").write_text("from . import leaf\n", encoding="utf-8")

    python_runner.run_scenario_ok("monitoring/detailed_import_calls.py", "fromlist_and_level")


def test_failed_import_records_the_raised_outcome(python_runner: PythonRunner, tmp_path: Path) -> None:
    python_runner.run_scenario_ok("monitoring/detailed_import_calls.py", "raised")


def test_wrapping_is_chain_safe_with_other_import_wrappers(python_runner: PythonRunner, tmp_path: Path) -> None:
    python_runner.run_scenario_ok("monitoring/detailed_import_calls.py", "chain_safe")


def test_mechanism_is_off_unless_requested(python_runner: PythonRunner, tmp_path: Path) -> None:
    python_runner.run_scenario_ok("monitoring/detailed_import_calls.py", "disabled_by_default")
