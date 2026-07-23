import argparse
import json
import os
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_PROJECT_ROOT = Path(__file__).parents[2]
_HELPERS = runpy.run_path(str(_PROJECT_ROOT / "scripts" / "_benchmark_cli.py"))
inspect_python = cast("Callable[[Path], dict[str, object]]", _HELPERS["inspect_python"])
parse_counts = cast("Callable[[str], list[int]]", _HELPERS["parse_counts"])
parse_output_directory = cast("Callable[[str], Path]", _HELPERS["parse_output_directory"])
parse_positive_int = cast("Callable[[str], int]", _HELPERS["parse_positive_int"])
parse_python_version = cast("Callable[[str], tuple[int, int]]", _HELPERS["parse_python_version"])
resolve_python = cast("Callable[[str], Path]", _HELPERS["resolve_python"])
validate_expected_python = cast(
    "Callable[[dict[str, object], tuple[int, int] | None], None]", _HELPERS["validate_expected_python"]
)
_WORKER_TIMEOUT_SECONDS = 25


def test_parse_counts_sorts_and_deduplicates() -> None:
    assert parse_counts("100,25,100") == [25, 100]


@pytest.mark.parametrize("value", ["", "1,two", "0", "-1", "1,"])
def test_parse_counts_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_counts(value)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "many"])
def test_parse_positive_int_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_positive_int(value)


@pytest.mark.parametrize("value", ["3", "3.10.1", "v3.10", "three.ten"])
def test_parse_python_version_rejects_invalid_values(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_python_version(value)


def test_parse_python_version_accepts_major_minor() -> None:
    assert parse_python_version("3.10") == (3, 10)


def test_resolve_python_accepts_executable_path() -> None:
    assert resolve_python(sys.executable) == Path(sys.executable).resolve()


def test_resolve_python_rejects_unknown_command() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="Python executable not found"):
        resolve_python("metapathology-python-that-does-not-exist")


def test_output_directory_rejects_file(tmp_path: Path) -> None:
    output = tmp_path / "results"
    output.write_text("not a directory", encoding="utf-8")
    with pytest.raises(argparse.ArgumentTypeError, match="output path is not a directory"):
        parse_output_directory(str(output))


def test_inspect_python_accepts_supported_cpython() -> None:
    metadata = inspect_python(Path(sys.executable))
    assert metadata["implementation"] == "CPython"
    version_info = metadata["version_info"]
    assert isinstance(version_info, list)
    assert tuple(version_info[:2]) >= (3, 10)


def test_expected_python_rejects_mismatched_interpreter() -> None:
    metadata = inspect_python(Path(sys.executable))
    with pytest.raises(ValueError, match="target interpreter is Python"):
        validate_expected_python(metadata, (99, 99))


def test_import_workloads_cross_the_audit_boundary(tmp_path: Path) -> None:
    package = "benchmark_audit_fixture"
    package_dir = tmp_path / package
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    for index in range(2):
        (package_dir / f"module_{index:05d}.py").write_text("VALUE = 1\n", encoding="utf-8")
    worker = _PROJECT_ROOT / "scripts" / "_benchmark_worker.py"
    environment = os.environ.copy()
    source = str(_PROJECT_ROOT / "src")
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        source if existing_pythonpath is None else os.pathsep.join((source, existing_pythonpath))
    )

    def run(scenario: str) -> dict[str, object]:
        proc = subprocess.run(
            [
                sys.executable,
                "-S",
                str(worker),
                "--scenario",
                scenario,
                "--metric",
                "time",
                "--count",
                "2",
                "--package",
                package,
                "--fixture",
                str(tmp_path),
                "--monitored",
            ],
            check=False,
            capture_output=True,
            env=environment,
            text=True,
            timeout=_WORKER_TIMEOUT_SECONDS,
        )
        assert proc.returncode == 0, proc.stderr
        return cast("dict[str, object]", json.loads(proc.stdout))

    # The package itself adds one resolution in addition to the two modules.
    native = run("native")
    attributed = run("attributed")
    detailed = run("detailed")
    native_event_count = native["event_count"]
    assert native_event_count == 3
    assert isinstance(native_event_count, int)
    assert attributed["event_count"] == 6
    detailed_event_count = detailed["event_count"]
    assert isinstance(detailed_event_count, int)
    assert detailed_event_count > native_event_count
    assert isinstance(detailed["report_seconds"], float)
    assert detailed["report_seconds"] > 0
    assert isinstance(detailed["report_bytes"], int)
    assert detailed["report_bytes"] > 0
