"""Finder attribution, wrapping, restoration, and hostile-object safety."""

from support import PythonRunner


def test_meta_path_finder_calls_are_attributed_to_the_claiming_finder(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "attribution")


def test_find_spec_records_preserve_metadata_and_path_snapshots(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "find_spec_metadata_snapshots")


def test_find_spec_records_target_module_transitions(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "finder_module_transitions")


def test_setuptools_3073_finder_changed_module_cache_fixture(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "setuptools_3073")


def test_uninstall_makes_all_layers_inert(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "uninstall_stops_recording")


def test_uninstall_restores_preexisting_instance_find_spec(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "uninstall_restores_instance_find_spec")


def test_instrumentation_failure_does_not_escape_or_stringify_foreign_exception(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "hostile_finder")


def test_finder_wrapper_is_reentrancy_guarded(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "reentrant_finder")


def test_uninstall_preserves_replaced_finder_shadow(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "uninstall_preserves_finder_replacement")


# On a reinstall the audit hook is already registered and _enabled is set
# before the finder-instrumentation loop runs, so an import triggered from a
# finder's lazy find_spec attribute re-enters _reinstall on the same thread
# while install() still holds the non-reentrant reinstall lock.
def test_reinstall_with_lazy_find_spec_attribute_does_not_deadlock(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "lazy_attribute_reinstall")


def test_uninstall_restores_slotted_finder_find_spec(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "slotted_finder_restore")


# A data descriptor with a setter accepts install()'s setattr() without the
# wrapper ever reaching the instance dict, so uninstall()'s __dict__-based
# restore silently misses it.
def test_uninstall_restores_descriptor_backed_find_spec(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "descriptor_finder_restore")


# install() documents report_at_exit as registering the atexit report, but the
# idempotence early-return skips that block when the monitor is already
# enabled, silently dropping the request.
def test_hostile_class_name_lookup_cannot_break_install(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "hostile_class_name")


def test_callable_metadata_lookup_cannot_break_install(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "hostile_callable_metadata")


def test_finder_wrapper_preserves_unwrap_and_signature_introspection(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "wrapper_introspection")


def test_foreign_spec_descriptors_are_not_invoked(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/finder_instrumentation.py", "hostile_spec_metadata")
