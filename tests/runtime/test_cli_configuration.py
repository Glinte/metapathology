"""CLI flag and environment configuration behavior."""

import os
from pathlib import Path

from runtime.cli_support import run_cli


def test_cli_can_disable_path_hook_monitoring_without_consuming_target_options(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nprint(type(sys.path_hooks) is list, sys.argv[1:])\n")

    proc = run_cli("--no-path-hook-monitoring", str(script), "--no-path-hook-monitoring", cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "True ['--no-path-hook-monitoring']" in proc.stdout
    assert "sys.path_hooks off" in proc.stderr


def test_cli_can_disable_importer_cache_monitoring_without_consuming_target_options(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nprint(sys.argv[1:])\n")

    proc = run_cli(
        "--no-importer-cache-monitoring",
        str(script),
        "--no-importer-cache-monitoring",
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "['--no-importer-cache-monitoring']" in proc.stdout
    assert "sys.path_importer_cache off" in proc.stderr


def test_removed_report_format_flag_fails_before_running_target(tmp_path: Path) -> None:
    marker = tmp_path / "target-ran"
    script = tmp_path / "prog.py"
    script.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    proc = run_cli("--report-format", "yaml", str(script), cwd=tmp_path)

    assert proc.returncode == 2
    assert "unrecognized arguments: --report-format" in proc.stderr
    assert not marker.exists()


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


def test_deep_umbrella_enables_every_mechanism(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import metapathology\n"
        "monitor = metapathology.get_monitor()\n"
        "print(monitor.deep_diagnostics, monitor.sys_path_enabled)\n"
    )

    proc = run_cli("--deep", str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "('path_hooks', 'path_entry_finders', 'loaders', 'import_outcomes', 'import_calls') True" in proc.stdout


def test_capture_environment_and_explicit_cli_values_have_consistent_precedence(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import metapathology\n"
        "monitor = metapathology.get_monitor()\n"
        "print(monitor.path_hooks_enabled, monitor.importer_cache_enabled, monitor.deep_diagnostics)\n"
    )
    env = dict(os.environ)
    env.update(
        {
            "METAPATHOLOGY_MONITOR_PATH_HOOKS": "off",
            "METAPATHOLOGY_MONITOR_IMPORTER_CACHE": "false",
            "METAPATHOLOGY_DEEP": "yes",
            "METAPATHOLOGY_DEEP_LOADERS": "0",
            "METAPATHOLOGY_DEEP_IMPORT_OUTCOMES": "1",
        }
    )

    proc = run_cli(
        "--path-hook-monitoring",
        "--no-deep-import-outcomes",
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
    env["METAPATHOLOGY_MONITOR_PATH_HOOKS"] = "sometimes"

    proc = run_cli(str(script), cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert "monitoring: sys.meta_path, sys.path_hooks," in proc.stderr
    assert "environment_configuration.METAPATHOLOGY_MONITOR_PATH_HOOKS" in proc.stderr
