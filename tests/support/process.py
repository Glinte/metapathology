"""Fresh-interpreter process helpers used throughout the test suite."""

import json
import os
import subprocess
import sys
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TypeAlias, cast

DEFAULT_SUBPROCESS_TIMEOUT = 25

ProcessResult: TypeAlias = subprocess.CompletedProcess[str]
JsonScalar: TypeAlias = None | bool | int | float | str
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


def source_with_literal(source: str, placeholder: str, value: object) -> str:
    """Replace one explicit source placeholder with a Python literal."""
    if source.count(placeholder) != 1:
        raise ValueError(f"expected exactly one {placeholder!r} placeholder")
    return source.replace(placeholder, repr(value))


def assert_success(result: ProcessResult) -> ProcessResult:
    """Assert that a child process succeeded and return it for further checks."""
    assert result.returncode == 0, (
        f"command failed with exit code {result.returncode}: {result.args!r}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    return result


def json_stdout(result: ProcessResult) -> JsonValue:
    """Decode one successful child process's standard output as JSON."""
    return cast("JsonValue", json.loads(assert_success(result).stdout))


def json_object_stdout(result: ProcessResult) -> dict[str, JsonValue]:
    """Decode a successful child's standard output as one JSON object."""
    value = json_stdout(result)
    if not isinstance(value, dict):
        raise AssertionError(f"expected child JSON object, got {type(value).__name__}: {value!r}")
    return value


@dataclass(frozen=True, slots=True)
class PythonRunner:
    """Run isolated Python commands from one temporary working directory."""

    cwd: Path
    executable: str = sys.executable
    timeout: float = DEFAULT_SUBPROCESS_TIMEOUT

    def with_cwd(self, cwd: Path) -> "PythonRunner":
        """Return a runner using a different working directory."""
        return replace(self, cwd=cwd)

    def run_command(
        self,
        arguments: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run an arbitrary command with captured text streams."""
        return subprocess.run(
            list(arguments),
            capture_output=True,
            text=True,
            cwd=cwd or self.cwd,
            env=dict(env) if env is not None else None,
            input=input_text,
            check=False,
            timeout=self.timeout if timeout is None else timeout,
        )

    def run_code(
        self,
        code: str,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run source code in a fresh interpreter and return its raw result."""
        source = textwrap.dedent(code).lstrip("\n")
        return self.run_command(
            [self.executable, "-c", source, *argv],
            cwd=cwd,
            env=env,
            input_text=input_text,
            timeout=timeout,
        )

    def run_code_ok(
        self,
        code: str,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run source code and require a successful exit."""
        return assert_success(self.run_code(code, *argv, cwd=cwd, env=env, input_text=input_text, timeout=timeout))

    def run_code_json(
        self,
        code: str,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, JsonValue]:
        """Run source code and decode its successful stdout as a JSON object."""
        return json_object_stdout(self.run_code(code, *argv, cwd=cwd, env=env, input_text=input_text, timeout=timeout))

    def run_module(
        self,
        module: str,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run a module in a fresh interpreter and return its raw result."""
        return self.run_command(
            [self.executable, "-m", module, *argv],
            cwd=cwd,
            env=env,
            input_text=input_text,
            timeout=timeout,
        )

    def run_script(
        self,
        script: Path,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run a script in a fresh interpreter and return its raw result."""
        return self.run_command(
            [self.executable, str(script), *argv],
            cwd=cwd,
            env=env,
            input_text=input_text,
            timeout=timeout,
        )

    def run_script_ok(
        self,
        script: Path,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> ProcessResult:
        """Run a script and require a successful exit."""
        return assert_success(self.run_script(script, *argv, cwd=cwd, env=env, input_text=input_text, timeout=timeout))

    def run_script_json(
        self,
        script: Path,
        *argv: str,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, JsonValue]:
        """Run a script and decode its successful stdout as a JSON object."""
        return json_object_stdout(
            self.run_script(script, *argv, cwd=cwd, env=env, input_text=input_text, timeout=timeout)
        )

    def environment(self, **overrides: str | None) -> dict[str, str]:
        """Copy the current environment and apply string overrides or removals."""
        environment = os.environ.copy()
        for name, value in overrides.items():
            if value is None:
                environment.pop(name, None)
            else:
                environment[name] = value
        return environment
