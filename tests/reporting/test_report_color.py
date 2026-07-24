"""ANSI color policy and text-rendering coverage."""

import io
from pathlib import Path

import pytest
from support import PythonRunner

from metapathology._report import _color_enabled, validate_color
from metapathology._report_text import render_failure_lines


class _TTYStream(io.StringIO):
    """In-memory text destination with a configurable terminal response."""

    def __init__(self, is_tty: bool, *, failure: bool = False) -> None:
        super().__init__()
        self._is_tty = is_tty
        self._failure = failure

    def isatty(self) -> bool:
        if self._failure:
            raise RuntimeError
        return self._is_tty


def test_auto_color_requires_a_capable_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)

    assert _color_enabled(_TTYStream(True), "auto")
    assert not _color_enabled(_TTYStream(False), "auto")
    assert not _color_enabled(_TTYStream(True, failure=True), "auto")
    assert not _color_enabled(tmp_path / "report.txt", "auto")

    monkeypatch.setenv("TERM", "dumb")
    assert not _color_enabled(_TTYStream(True), "auto")
    monkeypatch.delenv("TERM")
    monkeypatch.setenv("NO_COLOR", "1")
    assert not _color_enabled(_TTYStream(True), "auto")

    assert _color_enabled(tmp_path / "report.txt", "always")
    assert not _color_enabled(_TTYStream(True), "never")


def test_color_mode_validation() -> None:
    assert validate_color("auto") == "auto"
    assert validate_color("always") == "always"
    assert validate_color("never") == "never"
    with pytest.raises(ValueError, match="unknown color mode"):
        validate_color("sometimes")


def test_failure_report_styling_is_bounded() -> None:
    plain = render_failure_lines("RuntimeError")
    colored = render_failure_lines("RuntimeError", color=True)

    assert plain == ["== metapathology report ==", "report generation failed: RuntimeError"]
    assert colored == [
        "\x1b[1;36m== metapathology report ==\x1b[0m",
        "\x1b[1;31mreport generation failed:\x1b[0m RuntimeError",
    ]


def test_invalid_install_color_does_not_instrument_import_state(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_color.py", "invalid_install_color_does_not_instrument_import_state")


def test_explicit_render_color_preserves_plain_content(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/report_color.py", "explicit_render_color_preserves_plain_content")


def test_no_color_empty_value_does_not_disable_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.delenv("TERM", raising=False)

    assert _color_enabled(_TTYStream(True), "auto")
