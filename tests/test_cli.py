"""python -m metapathology: script mode, module mode, exit codes."""

import subprocess
import sys
from pathlib import Path

SUBPROCESS_TIMEOUT = 25


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "metapathology", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
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


def test_no_arguments_prints_usage_and_fails(tmp_path: Path) -> None:
    proc = run_cli(cwd=tmp_path)
    assert proc.returncode == 2
    assert "usage:" in proc.stderr
    assert "https://glinte.github.io/metapathology/usage/" in proc.stderr


def test_help_links_to_usage_documentation(tmp_path: Path) -> None:
    proc = run_cli("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert "https://glinte.github.io/metapathology/usage/" in proc.stdout


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
    assert "usage:" in proc.stdout
    assert "https://glinte.github.io/metapathology/usage/" in proc.stdout
