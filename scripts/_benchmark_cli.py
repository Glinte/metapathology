"""Stdlib-only argument validation shared by the benchmark driver tests."""

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict, cast

_MINIMUM_PYTHON = (3, 10)
_PYTHON_VERSION_PATTERN = re.compile(r"(?P<major>\d+)\.(?P<minor>\d+)\Z")


class PythonMetadata(TypedDict):
    """Validated identity of the interpreter used by benchmark workers."""

    version: str
    platform: str
    implementation: str
    version_info: list[int]


def parse_counts(value: str) -> list[int]:
    """Parse a comma-separated, nonempty set of positive workload sizes."""
    try:
        counts = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("counts must be comma-separated integers") from exc
    if not counts or any(count <= 0 for count in counts):
        raise argparse.ArgumentTypeError("counts must contain only positive integers")
    return sorted(set(counts))


def parse_positive_int(value: str) -> int:
    """Parse a strictly positive integer for a repetition count."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_python_version(value: str) -> tuple[int, int]:
    """Parse an expected Python ``major.minor`` version."""
    match = _PYTHON_VERSION_PATTERN.fullmatch(value)
    if match is None:
        raise argparse.ArgumentTypeError("expected Python version must use major.minor form, such as 3.12")
    return int(match.group("major")), int(match.group("minor"))


def resolve_python(value: str) -> Path:
    """Resolve an executable path or command name from ``PATH``."""
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    discovered = shutil.which(value)
    if discovered is None:
        raise argparse.ArgumentTypeError(f"Python executable not found: {value}")
    return Path(discovered).resolve()


def parse_output_directory(value: str) -> Path:
    """Reject an output path that already exists as a non-directory."""
    output = Path(value).expanduser()
    if output.exists() and not output.is_dir():
        raise argparse.ArgumentTypeError(f"output path is not a directory: {value}")
    return output


def inspect_python(python: Path) -> PythonMetadata:
    """Read and validate the selected target interpreter's identity."""
    code = (
        "import json, platform, sys; "
        "print(json.dumps({'version': sys.version, 'platform': platform.platform(), "
        "'implementation': platform.python_implementation(), 'version_info': sys.version_info[:3]}))"
    )
    try:
        completed = subprocess.run(
            [str(python), "-c", code],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        value: object = json.loads(completed.stdout)
        if not isinstance(value, dict):
            raise ValueError("metadata response was not an object")
        version = value.get("version")
        target_platform = value.get("platform")
        implementation = value.get("implementation")
        version_info = value.get("version_info")
        if not (
            isinstance(version, str)
            and isinstance(target_platform, str)
            and isinstance(implementation, str)
            and isinstance(version_info, list)
            and len(version_info) >= 2
            and all(isinstance(item, int) for item in version_info)
        ):
            raise ValueError("metadata response had invalid fields")
        metadata = PythonMetadata(
            version=version,
            platform=target_platform,
            implementation=implementation,
            version_info=cast("list[int]", version_info),
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"could not inspect Python executable {python}: {exc}") from exc
    if metadata["implementation"] != "CPython":
        raise ValueError(f"target interpreter must be CPython, not {metadata['implementation']}")
    if tuple(metadata["version_info"][:2]) < _MINIMUM_PYTHON:
        version = ".".join(str(part) for part in metadata["version_info"][:3])
        raise ValueError(f"target interpreter must be Python 3.10 or newer, not {version}")
    return metadata


def validate_expected_python(metadata: PythonMetadata, expected: tuple[int, int] | None) -> None:
    """Ensure target metadata matches an optional expected major/minor version."""
    if expected is None:
        return
    actual = tuple(metadata["version_info"][:2])
    if actual != expected:
        actual_text = ".".join(str(part) for part in actual)
        expected_text = ".".join(str(part) for part in expected)
        raise ValueError(f"target interpreter is Python {actual_text}, expected Python {expected_text}")
