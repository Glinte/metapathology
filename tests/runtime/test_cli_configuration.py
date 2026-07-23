"""CLI flag and environment configuration behavior."""

import os
from pathlib import Path

from runtime.cli_support import run_cli


def test_capture_flags_are_forwarded_without_consuming_target_options(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nprint(type(sys.path_hooks) is list, sys.argv[1:])\n")

    cases = (
        ("--no-path-hooks", "True ['--no-path-hooks']", "sys.path_hooks off"),
        ("--no-importer-cache", "False ['--no-importer-cache']", "sys.path_importer_cache off"),
    )
    for option, expected_stdout, expected_stderr in cases:
        proc = run_cli(option, str(script), option, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert expected_stdout in proc.stdout
        assert expected_stderr in proc.stderr


def test_removed_report_format_flag_fails_before_running_target(tmp_path: Path) -> None:
    marker = tmp_path / "target-ran"
    script = tmp_path / "prog.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    proc = run_cli("--report-format", "yaml", str(script), cwd=tmp_path)

    assert proc.returncode == 2
    assert "unrecognized arguments: --report-format" in proc.stderr
    assert not marker.exists()


def test_removed_capture_and_probe_flags_are_not_accepted_as_abbreviations(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("raise AssertionError('target ran')\n")

    for option in ("--deep", "--deep-loaders", "--probes", "--standard-path-probe"):
        proc = run_cli(option, str(script), cwd=tmp_path)
        assert proc.returncode == 2
        assert "unrecognized arguments" in proc.stderr


def test_invalid_color_mode_fails_before_running_target(tmp_path: Path) -> None:
    marker = tmp_path / "target-ran"
    script = tmp_path / "prog.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    proc = run_cli("--color", "sometimes", str(script), cwd=tmp_path)

    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr
    assert not marker.exists()


def test_explicit_cli_color_overrides_environment_and_no_color(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    env = dict(os.environ)
    env["METAPATHOLOGY_COLOR"] = "always"
    env["NO_COLOR"] = "1"

    configured = run_cli(str(script), cwd=tmp_path, env=env)
    disabled = run_cli("--color", "never", str(script), cwd=tmp_path, env=env)

    assert "\x1b[" in configured.stderr
    assert "\x1b[" not in disabled.stderr


def test_invalid_color_environment_falls_back_to_auto_and_is_reported(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    env = dict(os.environ)
    env["METAPATHOLOGY_COLOR"] = "sometimes"

    proc = run_cli(str(script), cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert "\x1b[" not in proc.stderr
    assert "environment_configuration.METAPATHOLOGY_COLOR" in proc.stderr


def test_environment_configures_automatic_report(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    destination = tmp_path / "environment-{pid}.txt"
    env = dict(os.environ)
    env["METAPATHOLOGY_REPORT"] = str(destination)

    proc = run_cli(str(script), cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    reports = list(tmp_path.glob("environment-*.txt"))
    assert len(reports) == 1
    assert "== metapathology report ==" in reports[0].read_text(encoding="utf-8")


def test_unknown_inferred_report_extension_fails_before_running_target(tmp_path: Path) -> None:
    marker = tmp_path / "target-ran"
    script = tmp_path / "prog.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    proc = run_cli("--report", "out.log", str(script), cwd=tmp_path)

    assert proc.returncode == 2
    assert "use --report-text or --report-json" in proc.stderr
    assert not marker.exists()


def test_environment_writes_multiple_inferred_reports(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    env = dict(os.environ)
    env["METAPATHOLOGY_REPORT"] = os.pathsep.join((str(tmp_path / "env.txt"), str(tmp_path / "env.json")))

    proc = run_cli(str(script), cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert len(list(tmp_path.glob("env.*.txt"))) == 1
    assert len(list(tmp_path.glob("env.*.json"))) == 1


def test_detailed_group_option_enables_every_mechanism(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import metapathology\n"
        "monitor = metapathology.get_monitor()\n"
        "print(monitor.detailed_capture, monitor.sys_path_enabled)\n"
    )

    proc = run_cli("--detailed-capture", str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "('path_hooks', 'path_entry_finders', 'loaders', 'import_results', 'import_calls') True" in proc.stdout


def test_unsafe_branch_option_is_forwarded_and_enables_only_its_prerequisites(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import metapathology\n"
        "monitor = metapathology.get_monitor()\n"
        "print(monitor.unsafe_import_branch_exploration_status, monitor.detailed_capture)\n"
    )

    proc = run_cli("--unsafe-explore-import-branches", str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "active_exhaustive_direct_siblings ('path_hooks', 'path_entry_finders', 'import_results')" in proc.stdout
    assert "UNSAFE: skipped import branches were invoked" in proc.stderr


def test_capture_environment_and_explicit_cli_values_have_consistent_precedence(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import metapathology\n"
        "monitor = metapathology.get_monitor()\n"
        "print(monitor.path_hooks_enabled, monitor.importer_cache_enabled, monitor.detailed_capture)\n"
    )
    env = dict(os.environ)
    env.update(
        {
            "METAPATHOLOGY_PATH_HOOKS": "off",
            "METAPATHOLOGY_IMPORTER_CACHE": "false",
            "METAPATHOLOGY_DETAILED_CAPTURE": "yes",
            "METAPATHOLOGY_CAPTURE_LOADER_CALLS": "0",
            "METAPATHOLOGY_CAPTURE_IMPORT_RESULTS": "1",
        }
    )

    proc = run_cli(
        "--path-hooks",
        "--no-capture-import-results",
        str(script),
        cwd=tmp_path,
        env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "True False ('path_hooks', 'path_entry_finders', 'import_calls')" in proc.stdout


def test_invalid_capture_environment_value_falls_back_and_is_reported(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    env = dict(os.environ)
    env["METAPATHOLOGY_PATH_HOOKS"] = "sometimes"

    proc = run_cli(str(script), cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert "monitoring: import audit, sys.meta_path, finder attribution, sys.path_hooks," in proc.stderr
    assert "environment_configuration.METAPATHOLOGY_PATH_HOOKS" in proc.stderr
