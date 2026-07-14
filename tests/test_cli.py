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
