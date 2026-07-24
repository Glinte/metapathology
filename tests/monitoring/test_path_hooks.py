"""Subprocess coverage for ``sys.path_hooks`` observation."""

from support import PythonRunner


def test_install_toggle_and_uninstall_preserve_live_path_hooks(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/path_hooks.py", "install_toggle_and_uninstall_preserve_live_path_hooks")


def test_path_hook_mutators_record_plain_identity_data_without_calling_hooks(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/path_hooks.py", "path_hook_mutators_record_plain_identity_data_without_calling_hooks"
    )


def test_path_hooks_replacement_is_recovered_on_next_import(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/path_hooks.py", "path_hooks_replacement_is_recovered_on_next_import")


def test_simultaneous_import_list_reassignments_use_meta_then_path_hook_sequence(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/path_hooks.py", "simultaneous_import_list_reassignments_use_meta_then_path_hook_sequence"
    )


def test_hostile_callable_metadata_is_not_inspected(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/path_hooks.py", "hostile_callable_metadata_is_not_inspected")


def test_path_hook_callback_is_inert_during_reentrant_finder_instrumentation(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/path_hooks.py", "path_hook_callback_is_inert_during_reentrant_finder_instrumentation"
    )


def test_concurrent_path_hooks_change_reads_reports_and_cleanup_are_safe(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/path_hooks.py", "concurrent_path_hooks_change_reads_reports_and_cleanup_are_safe"
    )
