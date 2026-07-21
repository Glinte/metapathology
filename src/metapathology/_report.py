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

    from metapathology._monitor import Monitor

    _ColorMode = Literal["auto", "always", "never"]
    _ReportFormat = Literal["text", "json"]


def write_report(
    monitor: "Monitor",
    destination: "TextIO | str | PathLike[str] | None" = None,
    *,
    format: "_ReportFormat" = "text",
    color: "_ColorMode" = "auto",
) -> None:
    """Render a report and write it to stderr, a stream, or an atomic file.

    Args:
        monitor: Installed process monitor to report.
        destination: Output stream or exact file path; None selects stderr.
        format: Text or stable JSON output.
        color: ANSI color policy for text output.

    Errors are recorded and re-raised here. Automatic callers provide the
    suppression boundary so explicit I/O retains conventional error handling.
    """
    report_format = validate_format(format)
    color_mode = validate_color(color)
    try:
        use_color = report_format == "text" and _color_enabled(destination, color_mode)
        rendered = render_report(monitor, format=report_format, color=use_color)
        if destination is None:
            sys.stderr.write(rendered)
        elif isinstance(destination, (str, os.PathLike)):
            _write_atomic(destination, rendered)
        else:
            destination.write(rendered)
    except Exception as exc:
        monitor._record_internal_error("report_write", exc)
        raise


def render_report(monitor: "Monitor", *, format: "_ReportFormat" = "text", color: bool = False) -> str:
    """Render the full diagnostic report as text or stable JSON.

    Args:
        monitor: Installed process monitor to report.
        format: Text or stable JSON output.
        color: Include ANSI styling in text output. JSON is never styled.

    Ordinary exceptions become a valid failure report. Control-flow
    exceptions outside ``Exception`` continue to propagate unchanged.
    """
    report_format = validate_format(format)
    try:
        document = capture_document(monitor)
        if report_format == "json":
            json_report = json_document(document)
            return json.dumps(json_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return "\n".join(render_lines(document, color=color)) + "\n"
    except Exception as exc:  # The report must never break the host program.
        error_name = type_name(exc)
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
