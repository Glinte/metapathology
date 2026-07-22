"""python -m metapathology: script mode, module mode, exit codes."""

import json
import os
import subprocess
import sys
from pathlib import Path

SUBPROCESS_TIMEOUT = 25


def run_cli(
    *args: str, cwd: Path, env: dict[str, str] | None = None, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "metapathology", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        input=input_text,
        check=False,
        timeout=SUBPROCESS_TIMEOUT,
    )


def run_console_script(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    executable = Path(sys.executable).with_name("metapathology.exe" if sys.platform == "win32" else "metapathology")
    return subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=SUBPROCESS_TIMEOUT,
    )


def test_script_mode_runs_target_with_its_argv_and_reports(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nimport colorsys\nprint('argv:', sys.argv[1:])\n")
    proc = run_cli(str(script), "alpha", "beta", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "argv: ['alpha', 'beta']" in proc.stdout
    assert "== metapathology report ==" in proc.stderr


def test_module_mode_runs_target_from_cwd(tmp_path: Path) -> None:
    (tmp_path / "target_mod.py").write_text("print('hello from module')\n")
    proc = run_cli("-m", "target_mod", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "hello from module" in proc.stdout
    assert "== metapathology report ==" in proc.stderr


def test_target_exit_code_is_propagated_and_report_still_written(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nsys.exit(3)\n")
    proc = run_cli(str(script), cwd=tmp_path)
    assert proc.returncode == 3
    assert "== metapathology report ==" in proc.stderr


def test_target_exception_reports_traceback_and_exits_nonzero(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("raise ValueError('boom')\n")
    proc = run_cli(str(script), cwd=tmp_path)
    assert proc.returncode == 1
    assert "ValueError: boom" in proc.stderr
    assert "== metapathology report ==" in proc.stderr


def test_no_arguments_starts_monitored_interactive_interpreter(tmp_path: Path) -> None:
    (tmp_path / "repl_probe_module.py").write_text("VALUE = 5\n")
    proc = run_cli(cwd=tmp_path, input_text="import repl_probe_module\nprint('value:', repl_probe_module.VALUE)\n")
    assert proc.returncode == 0, proc.stderr
    # code.interact writes the banner to stderr, like the bare interpreter.
    assert "import monitoring is active" in proc.stderr
    assert "value: 5" in proc.stdout
    assert "== metapathology report ==" in proc.stderr
    assert "repl_probe_module" in proc.stderr


def test_interactive_interpreter_preloads_metapathology_and_survives_exit(tmp_path: Path) -> None:
    proc = run_cli(cwd=tmp_path, input_text="print('has api:', callable(metapathology.render_report))\nexit()\n")
    assert proc.returncode == 0, proc.stderr
    assert "has api: True" in proc.stdout
    assert "== metapathology report ==" in proc.stderr


def test_module_flag_without_target_fails(tmp_path: Path) -> None:
    proc = run_cli("-m", cwd=tmp_path)
    assert proc.returncode == 2
    assert "error: -m requires a module name" in proc.stderr
    assert "Documentation: https://glinte.github.io/metapathology/usage/" in proc.stderr
    assert "== metapathology report ==" not in proc.stderr


def test_help_links_to_usage_documentation(tmp_path: Path) -> None:
    proc = run_cli("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.startswith("usage: python -m metapathology")
    assert "--color {auto,always,never}" in proc.stdout
    assert "--sys-path-monitoring" in proc.stdout
    assert "\nDocumentation:\n  https://glinte.github.io/metapathology/usage/\n" in proc.stdout


def test_double_dash_allows_script_name_beginning_with_dash(tmp_path: Path) -> None:
    script = tmp_path / "-prog.py"
    script.write_text("print('target ran')\n")

    proc = run_cli("--", script.name, cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "target ran" in proc.stdout


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


def test_cli_color_modes_control_captured_text_output(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")

    automatic = run_cli(str(script), cwd=tmp_path)
    forced = run_cli("--color", "always", str(script), cwd=tmp_path)
    disabled = run_cli("--color", "never", str(script), cwd=tmp_path)

    assert "\x1b[" not in automatic.stderr
    assert "\x1b[1;36m== metapathology report ==\x1b[0m" in forced.stderr
    assert "\x1b[32mverdict:\x1b[0m" in forced.stderr
    assert "\x1b[" not in disabled.stderr


def test_failed_warning_report_colors_outcome_and_finding_separately(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import sys\n"
        "class LegacyFinder:\n"
        "    def find_module(self, fullname, path=None):\n"
        "        return None\n"
        "sys.meta_path.insert(0, LegacyFinder())\n"
        "import missing_color_dependency\n"
    )

    proc = run_cli("--color", "always", str(script), cwd=tmp_path)

    assert proc.returncode == 1
    assert "\x1b[1;31mtarget outcome:\x1b[0m" in proc.stderr
    assert "\x1b[33mverdict:\x1b[0m" in proc.stderr
    assert "\x1b[33m[legacy-finder-contract]\x1b[0m" in proc.stderr


def test_actionable_report_uses_red_compact_markers(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import importlib.machinery, sys, types\n"
        "class ReplacingLoader:\n"
        "    def create_module(self, spec):\n"
        "        return None\n"
        "    def exec_module(self, module):\n"
        "        module.LOADED = True\n"
        "class Finder:\n"
        "    def __init__(self):\n"
        "        self.loader = ReplacingLoader()\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname != 'color_replacement':\n"
        "            return None\n"
        "        return importlib.machinery.ModuleSpec(fullname, self.loader, origin='color.ext')\n"
        "finder = Finder()\n"
        "sys.meta_path.insert(0, finder)\n"
        "import color_replacement\n"
        "first = color_replacement\n"
        "spec = first.__spec__\n"
        "second = types.ModuleType('color_replacement')\n"
        "second.__spec__ = spec\n"
        "second.__loader__ = spec.loader\n"
        "sys.modules['color_replacement'] = second\n"
        "spec.loader.exec_module(second)\n"
        "sys.modules['color_replacement'] = first\n"
    )

    proc = run_cli("--deep-loaders", "--color", "always", str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "\x1b[32mtarget outcome:\x1b[0m" in proc.stderr
    assert "\x1b[1;31mverdict:\x1b[0m" in proc.stderr
    assert "\x1b[1;31m[repeated-loader-execution]\x1b[0m" in proc.stderr


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


def test_text_report_files_are_plain_in_auto_and_colored_when_forced(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    automatic_path = tmp_path / "automatic.txt"
    forced_path = tmp_path / "forced.txt"

    automatic = run_cli(
        "--report",
        str(automatic_path),
        str(script),
        cwd=tmp_path,
    )
    forced = run_cli(
        "--report",
        str(forced_path),
        "--color",
        "always",
        str(script),
        cwd=tmp_path,
    )

    assert automatic.returncode == 0, automatic.stderr
    assert forced.returncode == 0, forced.stderr
    automatic_report = next(tmp_path.glob("automatic.*.txt")).read_text(encoding="utf-8")
    forced_report = next(tmp_path.glob("forced.*.txt")).read_text(encoding="utf-8")
    assert "\x1b[" not in automatic_report
    assert "\x1b[1;36m== metapathology report ==\x1b[0m" in forced_report


def test_invalid_color_environment_falls_back_to_auto_and_is_reported(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    env = dict(os.environ)
    env["METAPATHOLOGY_COLOR"] = "sometimes"

    proc = run_cli(str(script), cwd=tmp_path, env=env)

    assert proc.returncode == 0, proc.stderr
    assert "\x1b[" not in proc.stderr
    assert "environment_configuration.METAPATHOLOGY_COLOR" in proc.stderr


def test_missing_script_fails_without_report(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"

    proc = run_cli("--report", str(destination), "missing.py", cwd=tmp_path)

    assert proc.returncode == 2
    assert "script target does not exist: 'missing.py'" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert "== metapathology report ==" not in proc.stderr
    assert list(tmp_path.glob("report.*.json")) == []


def test_help_defers_target_execution_imports(tmp_path: Path) -> None:
    code = (
        "import sys\n"
        "from metapathology.__main__ import main\n"
        "assert main(['--help']) == 0\n"
        "deferred = ('runpy', 'traceback')\n"
        "print('deferred:', [name for name in deferred if name in sys.modules])\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
        timeout=SUBPROCESS_TIMEOUT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "deferred: []" in proc.stdout


def test_installed_console_script_runs_cli(tmp_path: Path) -> None:
    proc = run_console_script("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.startswith("usage: metapathology")
    assert "https://glinte.github.io/metapathology/usage/" in proc.stdout


def test_closed_stderr_during_report_does_not_replace_target_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "closes_stderr.py"
    script.write_text("import sys\nsys.stderr.close()\nsys.exit(7)\n")

    proc = run_cli(str(script), cwd=tmp_path)
    assert proc.returncode == 7


def test_keyboard_interrupt_propagates_like_direct_python(tmp_path: Path) -> None:
    script = tmp_path / "interrupts.py"
    script.write_text("raise KeyboardInterrupt\n")

    direct = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
        timeout=SUBPROCESS_TIMEOUT,
    )
    monitored = run_cli(str(script), cwd=tmp_path)

    assert monitored.returncode == direct.returncode
    assert "KeyboardInterrupt" in monitored.stderr


def test_automatic_json_filename_includes_pid(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    destination = tmp_path / "worker.json"

    proc = run_cli("--report", str(destination), str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "target ran" in proc.stdout
    assert proc.stderr == ""
    reports = list(tmp_path.glob("worker.*.json"))
    assert len(reports) == 1
    document = json.loads(reports[0].read_text(encoding="utf-8"))
    assert reports[0].name == f"worker.{document['process']['pid']}.json"


def test_text_file_report_and_target_option_passthrough(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nprint(sys.argv[1:])\n")
    destination = tmp_path / "worker.txt"

    proc = run_cli(
        "--report",
        str(destination),
        str(script),
        "--report",
        "target-value",
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "['--report', 'target-value']" in proc.stdout
    reports = list(tmp_path.glob("worker.*.txt"))
    assert len(reports) == 1
    assert "== metapathology report ==" in reports[0].read_text(encoding="utf-8")


def test_automatic_write_failure_does_not_replace_target_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("raise SystemExit(6)\n")
    destination = tmp_path / "missing" / "worker.json"

    proc = run_cli("--report", str(destination), str(script), cwd=tmp_path)

    assert proc.returncode == 6


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


def test_cli_writes_multiple_inferred_report_formats(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import json\nprint('target ran')\n")
    text_path = tmp_path / "diagnostic.txt"
    json_path = tmp_path / "diagnostic.json"

    proc = run_cli(
        "--report",
        str(text_path),
        "--report",
        str(json_path),
        str(script),
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    text_report = next(tmp_path.glob("diagnostic.*.txt")).read_text(encoding="utf-8")
    json_report = json.loads(next(tmp_path.glob("diagnostic.*.json")).read_text(encoding="utf-8"))
    assert "== metapathology report ==" in text_report
    assert json_report["process"]["pid"] > 0


def test_forced_stderr_text_and_json_file_are_both_written(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    destination = tmp_path / "machine.anything"

    proc = run_cli(
        "--report-text",
        "-",
        "--report-json",
        str(destination),
        str(script),
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "== metapathology report ==" in proc.stderr
    report = next(tmp_path.glob("machine.*.anything"))
    assert json.loads(report.read_text(encoding="utf-8"))["process"]["pid"] > 0


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


def test_concurrent_workers_do_not_overwrite_reports(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import time\ntime.sleep(0.2)\n")
    destination = tmp_path / "shared.json"
    commands = [
        sys.executable,
        "-m",
        "metapathology",
        "--report",
        str(destination),
        str(script),
    ]
    workers = [
        subprocess.Popen(
            commands,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
        )
        for _ in range(2)
    ]

    completed = [worker.communicate(timeout=SUBPROCESS_TIMEOUT) for worker in workers]

    assert [worker.returncode for worker in workers] == [0, 0], completed
    reports = list(tmp_path.glob("shared.*.json"))
    assert len(reports) == 2
    pids = {json.loads(report.read_text(encoding="utf-8"))["process"]["pid"] for report in reports}
    assert len(pids) == 2


def test_target_failure_is_correlated_with_unresolved_imports(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import sys\n"
        "class LazyImporter:\n"
        "    def find_module(self, fullname, path=None):\n"
        "        return None\n"
        "sys.meta_path.insert(0, LazyImporter())\n"
        "import missing_lazy_dependency\n"
    )
    proc = run_cli(str(script), cwd=tmp_path)

    assert proc.returncode == 1
    assert "target outcome: raised ModuleNotFoundError for 'missing_lazy_dependency'" in proc.stderr
    assert "the failed module appears under unresolved imports below" in proc.stderr
    assert "most severe is [legacy-finder-contract] 'LazyImporter'" in proc.stderr
    assert "-- imports that started but produced no module" in proc.stderr
    assert "'missing_lazy_dependency': import started at event #" in proc.stderr
    assert "(the failed module)" in proc.stderr
    assert "LazyImporter was on sys.meta_path but accepts only the legacy find_module protocol" in proc.stderr

    destination = tmp_path / "report.json"
    proc = run_cli("--report", str(destination), str(script), cwd=tmp_path)
    assert proc.returncode == 1
    document = json.loads(next(tmp_path.glob("report.*.json")).read_text(encoding="utf-8"))
    assert document["target_outcome"] == {
        "kind": "raised",
        "exception_type_name": "ModuleNotFoundError",
        "missing_module": "missing_lazy_dependency",
        "exit_code": 1,
    }
    assert document["summary"]["unresolved_import_count"] >= 1
    assert document["summary"]["top_finding_ref"] == "finding:1"


def test_clean_target_reports_a_clean_verdict_in_the_first_two_lines(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")

    proc = run_cli(str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    lines = proc.stderr.splitlines()
    start = lines.index("== metapathology report ==")
    assert lines[start + 1] == "target outcome: completed (exit status 0)"
    assert lines[start + 2].startswith("verdict: no import-hook interference detected across")
