"""ANSI color policy and text-rendering coverage."""

import io
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from metapathology._report import _color_enabled, validate_color
from metapathology._report_text import render_failure_lines

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


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


def test_invalid_install_color_does_not_instrument_import_state(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "import metapathology\n"
        "before = sys.meta_path\n"
        "try:\n"
        "    metapathology.install(report_color='sometimes')\n"
        "except ValueError as exception:\n"
        "    assert 'unknown color mode' in str(exception)\n"
        "else:\n"
        "    raise AssertionError('invalid color accepted')\n"
        "assert sys.meta_path is before\n"
        "assert metapathology.get_monitor() is not None\n"
        "assert not metapathology.get_monitor().enabled\n"
        "print('OK')\n"
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_explicit_render_color_preserves_plain_content_and_json(run_python: RunPython) -> None:
    proc = run_python(
        "import io, json, re, sys\n"
        "import metapathology\n"
        "metapathology.install(report_at_exit=False)\n"
        "class Finder:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        return None\n"
        "finder = Finder()\n"
        "sys.meta_path.append(finder)\n"
        "sys.meta_path.remove(finder)\n"
        "plain = metapathology.render_report()\n"
        "colored = metapathology.render_report(color=True)\n"
        "assert '\\x1b[' not in plain\n"
        "assert '\\x1b[1;36m== metapathology report ==\\x1b[0m' in colored\n"
        "assert '\\x1b[32m+' in colored\n"
        "assert '\\x1b[1;31m-' in colored\n"
        "assert re.sub(r'\\x1b\\[[0-9;]*m', '', colored) == plain\n"
        "plain_json = metapathology.render_report(format='json')\n"
        "colored_json = metapathology.render_report(format='json', color=True)\n"
        "assert json.loads(plain_json)['schema'] == json.loads(colored_json)['schema']\n"
        "assert '\\x1b[' not in colored_json\n"
        "class Terminal(io.StringIO):\n"
        "    def isatty(self):\n"
        "        return True\n"
        "stream = Terminal()\n"
        "metapathology.write_report(stream)\n"
        "assert '\\x1b[1;36m== metapathology report ==\\x1b[0m' in stream.getvalue()\n"
        "print('OK')\n"
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_no_color_empty_value_does_not_disable_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.delenv("TERM", raising=False)

    assert _color_enabled(_TTYStream(True), "auto")
