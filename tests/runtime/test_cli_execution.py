"""CLI target execution, interpreter modes, and exit behavior."""

import subprocess
import sys
from pathlib import Path

from runtime.cli_support import run_cli, run_console_script
from support import TempProject
from support.process import DEFAULT_SUBPROCESS_TIMEOUT


def test_script_mode_runs_target_with_its_argv_and_reports(tmp_path: Path, temp_project: TempProject) -> None:
    script = temp_project.write("prog.py", "import sys\nimport colorsys\nprint('argv:', sys.argv[1:])\n")
    proc = run_cli(str(script), "alpha", "beta", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "argv: ['alpha', 'beta']" in proc.stdout
    assert "== metapathology report ==" in proc.stderr


def test_module_mode_runs_target_from_cwd(tmp_path: Path, temp_project: TempProject) -> None:
    temp_project.module("target_mod", "print('hello from module')\n")
    proc = run_cli("-m", "target_mod", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "hello from module" in proc.stdout
    assert "== metapathology report ==" in proc.stderr


def test_target_exit_code_is_propagated_and_report_still_written(tmp_path: Path, temp_project: TempProject) -> None:
    script = temp_project.write("prog.py", "import sys\nsys.exit(3)\n")
    proc = run_cli(str(script), cwd=tmp_path)
    assert proc.returncode == 3
    assert "== metapathology report ==" in proc.stderr


def test_target_exception_reports_traceback_and_exits_nonzero(tmp_path: Path, temp_project: TempProject) -> None:
    script = temp_project.write("prog.py", "raise ValueError('boom')\n")
    proc = run_cli(str(script), cwd=tmp_path)
    assert proc.returncode == 1
    assert "ValueError: boom" in proc.stderr
    assert "== metapathology report ==" in proc.stderr


def test_no_arguments_starts_monitored_interactive_interpreter(tmp_path: Path, temp_project: TempProject) -> None:
    temp_project.module("repl_probe_module", "VALUE = 5\n")
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
    assert "--sys-path" in proc.stdout
    assert "\nDocumentation:\n  https://glinte.github.io/metapathology/usage/\n" in proc.stdout


def test_double_dash_allows_script_name_beginning_with_dash(tmp_path: Path, temp_project: TempProject) -> None:
    script = temp_project.write("-prog.py", "print('target ran')\n")

    proc = run_cli("--", script.name, cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "target ran" in proc.stdout


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
        timeout=DEFAULT_SUBPROCESS_TIMEOUT,
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
        timeout=DEFAULT_SUBPROCESS_TIMEOUT,
    )
    monitored = run_cli(str(script), cwd=tmp_path)

    assert monitored.returncode == direct.returncode
    assert "KeyboardInterrupt" in monitored.stderr
