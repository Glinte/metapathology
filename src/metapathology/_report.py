"""Report rendering and output boundaries.

Live-state capture and resolution-route analysis happen once before rendering.
The renderers only project that document, while this module contains the I/O
behavior shared by explicit, CLI, and atexit reporting.
"""

import json
import os
import sys
import tempfile
from contextlib import suppress

from metapathology._config import validate_report_color as validate_color
from metapathology._config import validate_report_format as validate_format
from metapathology._record import _Record
from metapathology._records import type_name
from metapathology._report_capture import capture_document
from metapathology._report_json import failed_json_document, json_document
from metapathology._report_text import render_failure_lines, render_lines

# Supported type checkers treat this conventional name as true without making
# report generation import typing solely for annotations.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from os import PathLike
    from typing import Literal, TextIO

    from metapathology._monitor_model import MonitorSnapshot
    from metapathology._report_model import ReportDocument

    _ColorMode = Literal["auto", "always", "never"]
    _ReportFormat = Literal["text", "json"]


class ReportFailure(_Record):
    """Format-neutral reduction of an ordinary report-generation failure."""

    error_name: str


def capture_report(snapshot: "MonitorSnapshot") -> "ReportDocument | ReportFailure":
    """Build one reusable report artifact from an immutable monitor snapshot."""
    try:
        return capture_document(snapshot)
    except Exception as exc:  # Reporting must never break the host program.
        return ReportFailure(type_name(exc))


def write_report(
    artifact: "ReportDocument | ReportFailure",
    destination: "TextIO | str | PathLike[str] | None" = None,
    *,
    format: "_ReportFormat" = "text",
    color: "_ColorMode" = "auto",
) -> None:
    """Render a report and write it to stderr, a stream, or an atomic file.

    Args:
        artifact: Captured report document or generation failure.
        destination: Output stream or exact file path; None selects stderr.
        format: Text or stable JSON output.
        color: ANSI color policy for text output.

    I/O errors are re-raised here. The runtime records explicit failures and
    provides the suppression boundary for automatic output.
    """
    report_format = validate_format(format)
    color_mode = validate_color(color)
    use_color = report_format == "text" and _color_enabled(destination, color_mode)
    rendered = render_report(artifact, format=report_format, color=use_color)
    if destination is None:
        sys.stderr.write(rendered)
    elif isinstance(destination, (str, os.PathLike)):
        _write_atomic(destination, rendered)
    else:
        destination.write(rendered)


def render_report(
    artifact: "ReportDocument | ReportFailure", *, format: "_ReportFormat" = "text", color: bool = False
) -> str:
    """Render the full diagnostic report as text or stable JSON.

    Args:
        artifact: Captured report document or generation failure.
        format: Text or stable JSON output.
        color: Include ANSI styling in text output. JSON is never styled.

    Capture has already reduced ordinary failures into a reusable artifact.
    """
    report_format = validate_format(format)
    try:
        return _render_artifact(artifact, report_format, color)
    except Exception as exc:
        return _render_failure(type_name(exc), report_format, color)


def _render_artifact(artifact: "ReportDocument | ReportFailure", report_format: "_ReportFormat", color: bool) -> str:
    """Project one captured artifact without consulting live monitor state."""
    if isinstance(artifact, ReportFailure):
        return _render_failure(artifact.error_name, report_format, color)
    if report_format == "json":
        json_report = json_document(artifact)
        return json.dumps(json_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return "\n".join(render_lines(artifact, color=color)) + "\n"


def _render_failure(error_name: str, report_format: "_ReportFormat", color: bool) -> str:
    """Render a format-appropriate failure artifact."""
    if report_format == "json":
        return json.dumps(failed_json_document(error_name), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return "\n".join(render_failure_lines(error_name, color=color)) + "\n"


def _color_enabled(destination: "TextIO | str | PathLike[str] | None", mode: "_ColorMode") -> bool:
    """Resolve whether ANSI styling is appropriate for one output destination."""
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("NO_COLOR") or os.environ.get("TERM", "").lower() == "dumb":
        return False
    if destination is None:
        stream = sys.stderr
    elif isinstance(destination, (str, os.PathLike)):
        return False
    else:
        stream = destination
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _write_atomic(path: "str | PathLike[str]", rendered: str) -> None:
    """Write through a same-directory temporary file, then atomically replace."""
    raw_path = os.fspath(path)
    if not isinstance(raw_path, str):
        raise TypeError("report paths must resolve to str, not bytes")
    absolute = os.path.abspath(raw_path)
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{os.path.basename(absolute)}.",
            suffix=".tmp",
            dir=os.path.dirname(absolute),
            delete=False,
        ) as temporary:
            temporary_path = temporary.name
            temporary.write(rendered)
        os.replace(temporary_path, absolute)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                os.unlink(temporary_path)
