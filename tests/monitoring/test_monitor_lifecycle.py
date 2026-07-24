"""Monitor installation, regions, and lifecycle behavior."""

from support import PythonRunner


def test_install_and_uninstall_restore_meta_path(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/monitor_lifecycle.py", "install_restore")


def test_monitoring_region_restores_after_an_exception(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/monitor_lifecycle.py", "monitoring_region_restores_after_an_exception")


def test_nested_monitoring_regions_share_lifecycle_with_fixed_configuration(
    python_runner: PythonRunner,
) -> None:
    python_runner.run_scenario_ok(
        "monitoring/monitor_lifecycle.py", "nested_monitoring_regions_share_lifecycle_with_fixed_configuration"
    )


def test_monitoring_region_preserves_preexisting_installation(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/monitor_lifecycle.py", "monitoring_region_preserves_preexisting_installation"
    )


def test_overlapping_monitoring_regions_uninstall_after_last_exit(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/monitor_lifecycle.py", "overlapping_monitoring_regions_uninstall_after_last_exit"
    )


def test_install_warns_once_when_starting_on_unsupported_implementation(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/monitor_lifecycle.py", "install_warns_once_when_starting_on_unsupported_implementation"
    )
