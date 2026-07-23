"""Shared runner for ``python -m metapathology`` integration tests."""

import sys
from collections.abc import Mapping
from pathlib import Path

from support import ProcessResult, PythonRunner


def run_cli(
    *args: str,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
) -> ProcessResult:
    """Run the primary module CLI and return its raw result."""
    return PythonRunner(cwd).run_module("metapathology", *args, env=env, input_text=input_text)


def run_console_script(*args: str, cwd: Path) -> ProcessResult:
    """Run the installed console-script entry point."""
    executable = Path(sys.executable).with_name("metapathology.exe" if sys.platform == "win32" else "metapathology")
    return PythonRunner(cwd).run_command([str(executable), *args])
