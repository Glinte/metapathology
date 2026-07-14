import argparse
import runpy
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_HELPERS = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "_benchmark_cli.py"))
inspect_python = cast("Callable[[Path], dict[str, object]]", _HELPERS["inspect_python"])
parse_counts = cast("Callable[[str], list[int]]", _HELPERS["parse_counts"])
parse_output_directory = cast("Callable[[str], Path]", _HELPERS["parse_output_directory"])
parse_positive_int = cast("Callable[[str], int]", _HELPERS["parse_positive_int"])
parse_python_version = cast("Callable[[str], tuple[int, int]]", _HELPERS["parse_python_version"])
resolve_python = cast("Callable[[str], Path]", _HELPERS["resolve_python"])
validate_expected_python = cast(
    "Callable[[dict[str, object], tuple[int, int] | None], None]", _HELPERS["validate_expected_python"]
)


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
