"""Human report content: bypass detection and the module-without-spec bucket."""

import zipfile
from pathlib import Path

from support import PythonRunner


def test_finder_shadowing_path_hooks_reports_neutral_result_difference(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    # The module file is in cwd, so the standard path machinery *would* find it
    # with a plain SourceFileLoader; the sneaky finder finder_calls it first.
    module_file = tmp_path / "real_mod.py"
    module_file.write_text("VALUE = 7\n")
    proc = python_runner.run_scenario_ok("reporting/report_text.py", "bypass", "real_mod", str(module_file))
    assert "[loader-displacement]" not in proc.stdout
    assert "-- modules found by a custom finder" in proc.stdout
    assert "note: this finder ran before PathFinder, so the standard path search never saw the module" in proc.stdout
    assert "real_mod" in proc.stdout
    assert "SneakyLoader" in proc.stdout
    assert "SourceFileLoader" in proc.stdout
    assert "further investigation:" in proc.stdout
    assert "--unsafe-explore-import-branches" in proc.stdout
    # Result comparisons lead the raw event timeline, and paths under the
    # reported cwd are relativized.
    assert proc.stdout.index("-- modules found by a custom finder") < proc.stdout.index("-- event timeline")
    assert "origin 'real_mod.py'" in proc.stdout
    assert f"paths shown relative to: {tmp_path}" in proc.stdout


def test_source_and_archive_results_are_compared_without_classifying_a_defect(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    archive = tmp_path / "frozen-like.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("archive_conflict.py", "VALUE = 'archive'\n")
    custom = tmp_path / "custom" / "archive_conflict.py"
    custom.parent.mkdir()
    custom.write_text("VALUE = 7\n", encoding="utf-8")
    python_runner.run_scenario_ok(
        "reporting/report_text.py",
        "source_and_archive_results_are_compared_without_classifying_a_defect",
        str(archive),
        str(custom),
    )


def test_module_invisible_to_path_machinery_has_a_not_found_standard_result(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    # The module file lives in a directory that is not on sys.path, so the
    # standard path machinery cannot find it at all.
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    module_file = hidden / "hidden_mod.py"
    module_file.write_text("VALUE = 7\n")
    proc = python_runner.run_scenario_ok("reporting/report_text.py", "bypass_2", "hidden_mod", str(module_file))
    assert "[unfindable]" not in proc.stdout
    assert "PathFinder status not_found" in proc.stdout
    assert "hidden_mod" in proc.stdout


def test_manually_registered_module_is_flagged_as_module_without_spec(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_text.py", "no_spec")


def test_standard_unwrapped_finders_are_explained(python_runner: PythonRunner) -> None:
    proc = python_runner.run_scenario_ok("reporting/report_text.py", "default_finders")
    assert "report guide: https://glinte.github.io/metapathology/report/" in proc.stdout
    assert "standard CPython finders left unwrapped (expected)" in proc.stdout
    assert "BuiltinImporter: class entry" not in proc.stdout
    assert "FrozenImporter: class entry" not in proc.stdout
    assert "PathFinder: class entry" not in proc.stdout
    assert (
        "monitoring: import audit, sys.meta_path, finder attribution, sys.path_hooks, sys.path_importer_cache"
    ) in proc.stdout
    assert "unobservable" not in proc.stdout
    assert "sys.meta_path (unchanged since install):" in proc.stdout
    assert "Nothing was recorded for:" not in proc.stdout
    assert "-- sys.path_hooks changes (0) --" not in proc.stdout


def test_path_removal_after_import_does_not_create_bypass_finding(python_runner: PythonRunner, tmp_path: Path) -> None:
    module_dir = tmp_path / "temporary_path"
    module_dir.mkdir()
    (module_dir / "transient_path_mod.py").write_text("VALUE = 7\n")

    python_runner.run_scenario_ok("reporting/report_text.py", "path_removed_after_import", str(module_dir))


def test_cwd_change_after_import_does_not_create_false_bypass_finding(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "cwd_sensitive_mod.py").write_text("VALUE = 7\n")
    new_cwd = tmp_path / "elsewhere"
    new_cwd.mkdir()

    python_runner.run_scenario_ok("reporting/report_text.py", "cwd_changed_after_import", str(new_cwd))


def test_report_does_not_dispatch_to_foreign_module_like_objects(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_text.py", "hostile_module_spec")
