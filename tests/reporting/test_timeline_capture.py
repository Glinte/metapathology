"""Unified capture ordering and import-audit evidence."""

from pathlib import Path

from support import PythonRunner


def test_timeline_interleaves_all_capture_mechanisms(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "timeline_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    python_runner.run_scenario_ok("reporting/timeline_capture.py", "timeline")


def test_monitor_identities_do_not_join_threads_by_reused_name(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "thread_identity_first.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "thread_identity_second.py").write_text("VALUE = 2\n", encoding="utf-8")

    python_runner.run_scenario_ok("reporting/timeline_capture.py", "thread_identities")


def test_audit_starts_exclude_cache_hits_native_load_events_and_malformed_events(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/timeline_capture.py", "audit_shapes")


def test_timeline_details_meta_path_identity_only_on_deviation(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/timeline_capture.py", "deviation")


def test_reentrant_imports_remain_unobserved_inside_finder_wrapper(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "outer_timeline_module.py").write_text("VALUE = 1\n", encoding="utf-8")

    python_runner.run_scenario_ok("reporting/timeline_capture.py", "reentrant")


def test_audit_start_precedes_reassignment_recovery_and_uninstall_is_inert(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "reassignment_timeline_module.py").write_text("VALUE = 1\n", encoding="utf-8")

    python_runner.run_scenario_ok("reporting/timeline_capture.py", "reassignment")
