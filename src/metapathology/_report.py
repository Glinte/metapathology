"""Report rendering and output boundaries.

All live-state capture and resolution-route analysis happens once in
``_report_data``. The renderers only project that document, while this module
contains the I/O behavior shared by explicit, CLI, and atexit reporting.
"""

import json
import os
import sys
import tempfile
from contextlib import suppress

from metapathology._records import type_name
from metapathology._report_data import capture_document, failed_json_document, json_document
from metapathology._report_text import render_lines

# Supported type checkers treat this conventional name as true without making
# report generation import typing solely for annotations.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from os import PathLike
    from typing import Literal, TextIO

    from metapathology._monitor import Monitor

    _ReportFormat = Literal["text", "json"]


_REPORT_FORMATS = frozenset(("json", "text"))


def validate_format(format: str) -> "_ReportFormat":
    """Validate and narrow a public report-format value."""
    if format not in _REPORT_FORMATS:
        choices = ", ".join(sorted(_REPORT_FORMATS))
        raise ValueError(f"unknown report format {format!r}; expected one of: {choices}")
    return format  # type: ignore[return-value]


def write_report(
    monitor: "Monitor",
    destination: "TextIO | str | PathLike[str] | None" = None,
    *,
    format: "_ReportFormat" = "text",
) -> None:
    """Render a report and write it to stderr, a stream, or an atomic file.

    Errors are recorded and re-raised here. Automatic callers provide the
    suppression boundary so explicit I/O retains conventional error handling.
    """
    report_format = validate_format(format)
    try:
        rendered = render_report(monitor, format=report_format)
        if destination is None:
            sys.stderr.write(rendered)
        elif isinstance(destination, (str, os.PathLike)):
            _write_atomic(destination, rendered)
        else:
            destination.write(rendered)
    except Exception as exc:
        monitor._record_internal_error("report_write", exc)
        raise


def render_report(monitor: "Monitor", *, format: "_ReportFormat" = "text") -> str:
    """Render the full diagnostic report as text or stable JSON.

    Ordinary exceptions become a valid failure report. Control-flow
    exceptions outside ``Exception`` continue to propagate unchanged.
    """
    report_format = validate_format(format)
    try:
        document = capture_document(monitor)
        if report_format == "json":
            json_report = json_document(document)
            return json.dumps(json_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return "\n".join(render_lines(document)) + "\n"
    except Exception as exc:  # The report must never break the host program.
        error_name = type_name(exc)
        if report_format == "json":
            return json.dumps(failed_json_document(error_name), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return f"== metapathology report ==\nreport generation failed: {error_name}\n"


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
