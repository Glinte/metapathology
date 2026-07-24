"""Detailed path-hook, path-entry-finder, and loader capture."""

import json
from pathlib import Path

from support import PythonRunner


def test_detailed_boundaries_delegate_record_and_restore(python_runner: PythonRunner) -> None:
    proc = python_runner.run_scenario_ok("monitoring/detailed_path_machinery.py", "detailed_capture")
    result = json.loads(proc.stdout)
    assert result["mechanisms"] == []


def test_distinct_hooks_accepting_one_path_report_structural_shadow(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_path_machinery.py",
        "distinct_hooks_accepting_one_path_report_structural_shadow",
        str(tmp_path),
    )


def test_detailed_path_hook_wrapper_survives_equality_scan(python_runner: PythonRunner, tmp_path: Path) -> None:
    """A third party locating its own hook by ``==`` must still find it.

    PyInstaller's ``PyiFrozenFinder.fallback_finder`` walks ``sys.path_hooks``
    testing ``hook == self.path_hook`` to find the hooks after its own and
    build a fallback ``FileFinder``. A detailed wrapper that did not compare equal
    to the shadowed hook broke that scan, silently disabling on-disk extension
    imports in frozen apps. The wrapper delegates equality, so the scan works.
    """
    python_runner.run_scenario_ok(
        "monitoring/detailed_path_machinery.py",
        "detailed_path_hook_wrapper_survives_equality_scan",
        str(tmp_path),
    )


def test_discord_10017_valid_spec_module_replacement_fixture(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/detailed_path_machinery.py", "discord_10017_module_replacement")


def test_detailed_mechanisms_are_independently_toggleable(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_path_machinery.py", "detailed_mechanisms_are_independently_toggleable"
    )


def test_detailed_path_entry_finder_preserves_later_shadow(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_path_machinery.py", "detailed_path_entry_finder_preserves_later_shadow"
    )


def test_detailed_loader_preserves_later_method_shadows(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_path_machinery.py", "detailed_loader_preserves_later_method_shadows"
    )


def test_detailed_path_hooks_restore_only_owned_wrappers(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/detailed_path_machinery.py", "detailed_path_hooks_restore_only_owned_wrappers"
    )
