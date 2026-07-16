"""Opt-in real executable smoke tests for supported freezer adapters."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from metapathology.frozen_bootstrap import generate

_SUBPROCESS_TIMEOUT = 300
_FIXTURES = Path(__file__).parent / "freezers"
_RUN_FREEZERS = os.environ.get("METAPATHOLOGY_TEST_FREEZERS") == "1"

pytestmark = pytest.mark.timeout(_SUBPROCESS_TIMEOUT)


def _require_module(name: str) -> None:
    if not _RUN_FREEZERS:
        pytest.skip("set METAPATHOLOGY_TEST_FREEZERS=1 to build frozen executables")
    if importlib.util.find_spec(name) is None:
        pytest.skip(f"optional freezer is not installed: {name}")


def _run(command: list[str], *, cwd: Path, timeout: int = _SUBPROCESS_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=timeout,
    )


def _assert_frozen_run(executable: Path, build: Path, integration: str) -> None:
    environment = dict(os.environ)
    environment["METAPATHOLOGY_REPORT"] = str(build / "diagnostic.txt")
    environment["METAPATHOLOGY_REPORT_FORMAT"] = "text"
    process = subprocess.run(
        [str(executable)],
        capture_output=True,
        text=True,
        cwd=build,
        env=environment,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert process.returncode == 0, process.stderr
    assert "fixture-app: 1/2" in process.stdout
    reports = list(build.glob("diagnostic.*.txt"))
    assert len(reports) == 1
    report = reports[0].read_text(encoding="utf-8")
    assert f"frozen integration: {integration}" in report
    assert "frozen observation boundary: after freezer initialization" in report
    assert "fractions" in report


@pytest.mark.freezer
@pytest.mark.parametrize("mode", ["onedir", "onefile"])
def test_pyinstaller_runtime_hook_smoke(tmp_path: Path, mode: str) -> None:
    _require_module("PyInstaller")
    bootstrap = tmp_path / "metapathology_rthook.py"
    generate("pyinstaller", bootstrap)
    process = _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            f"--{mode}",
            "--name",
            "fixture-app",
            "--distpath",
            str(tmp_path / "dist"),
            "--workpath",
            str(tmp_path / "work"),
            "--specpath",
            str(tmp_path),
            "--runtime-hook",
            str(bootstrap),
            str(_FIXTURES / "fixture_app.py"),
        ],
        cwd=tmp_path,
    )
    assert process.returncode == 0, process.stderr
    suffix = ".exe" if sys.platform == "win32" else ""
    executable = (
        tmp_path / "dist" / "fixture-app" / f"fixture-app{suffix}"
        if mode == "onedir"
        else tmp_path / "dist" / f"fixture-app{suffix}"
    )
    _assert_frozen_run(executable, tmp_path, "pyinstaller")


@pytest.mark.freezer
def test_cx_freeze_init_script_smoke(tmp_path: Path) -> None:
    _require_module("cx_Freeze")
    shutil.copy2(_FIXTURES / "fixture_app.py", tmp_path / "fixture_app.py")
    bootstrap = tmp_path / "metapathology_init.py"
    generate("cx-freeze", bootstrap)
    target = tmp_path / "dist"
    cxfreeze = Path(sys.executable).with_name("cxfreeze.exe" if sys.platform == "win32" else "cxfreeze")
    process = _run(
        [
            str(cxfreeze),
            "--script",
            str(tmp_path / "fixture_app.py"),
            "--init-script",
            str(bootstrap),
            "--target-dir",
            str(target),
            "--includes=metapathology",
        ],
        cwd=tmp_path,
    )
    assert process.returncode == 0, process.stderr
    suffix = ".exe" if sys.platform == "win32" else ""
    _assert_frozen_run(target / f"fixture_app{suffix}", tmp_path, "cx-freeze")


@pytest.mark.freezer
@pytest.mark.parametrize("mode", ["standalone", "onefile"])
def test_nuitka_launcher_smoke(tmp_path: Path, mode: str) -> None:
    _require_module("nuitka")
    shutil.copy2(_FIXTURES / "fixture_app.py", tmp_path / "fixture_app.py")
    launcher = tmp_path / "metapathology_launcher.py"
    generate("nuitka", launcher, module="fixture_app")
    process = _run(
        [
            sys.executable,
            "-m",
            "nuitka",
            f"--mode={mode}",
            "--assume-yes-for-downloads",
            "--include-module=fixture_app",
            str(launcher),
        ],
        cwd=tmp_path,
    )
    assert process.returncode == 0, process.stderr
    suffix = ".exe" if sys.platform == "win32" else ".bin"
    executable = (
        tmp_path / "metapathology_launcher.dist" / f"metapathology_launcher{suffix}"
        if mode == "standalone"
        else tmp_path / f"metapathology_launcher{suffix}"
    )
    _assert_frozen_run(executable, tmp_path, "nuitka")
