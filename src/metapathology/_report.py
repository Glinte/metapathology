"""Report rendering and output boundaries.

Live-state capture and finder-result analysis happen once before rendering.
The renderers only project that document, while this module contains the I/O
behavior shared by explicit, CLI, and atexit reporting.
"""

import json
import os
import sys
import tempfile
from contextlib import contextmanager, suppress

from metapathology._config import validate_report_color as validate_color
from metapathology._config import validate_report_format as validate_format
from metapathology._record import _Record
from metapathology._records import type_name
from metapathology._report_capture import capture_document
from metapathology._report_json import failed_json_document, json_document
from metapathology._report_text import iter_report_lines, render_failure_lines, render_lines

# Supported type checkers treat this conventional name as true without making
# report generation import typing solely for annotations.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Iterator
    from os import PathLike
    from typing import IO, Literal, TextIO

    from metapathology._config import ResolvedAnalysisConfig
    from metapathology._monitor_model import MonitorSnapshot
    from metapathology._report_model import ReportDocument
    from metapathology._report_schema import ReportJSON

    _ColorMode = Literal["auto", "always", "never"]
    _ReportFormat = Literal["text", "json"]


class ReportFailure(_Record):
    """Format-neutral reduction of an ordinary report-generation failure."""

    error_name: str


def capture_report(snapshot: "MonitorSnapshot", analysis: "ResolvedAnalysisConfig") -> "ReportDocument | ReportFailure":
    """Build one reusable report artifact from an immutable monitor snapshot."""
    try:
        return capture_document(snapshot, analysis)
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
    # Text file output streams line-by-line into the atomic temp file so the
    # whole report is never resident at once; stderr and streams keep the
    # buffered fallback guarantee (a partial write there could not be undone).
    if (
        report_format == "text"
        and not isinstance(artifact, ReportFailure)
        and isinstance(destination, (str, os.PathLike))
    ):
        _write_text_file(destination, artifact, color=use_color)
        return
    rendered = (
        _serialize_json(get_report(artifact)) if report_format == "json" else render_report(artifact, color=use_color)
    )
    if destination is None:
        sys.stderr.write(rendered)
    elif isinstance(destination, (str, os.PathLike)):
        _write_atomic(destination, rendered)
    else:
        destination.write(rendered)


def render_report(artifact: "ReportDocument | ReportFailure", *, color: bool = False) -> str:
    """Render the full human-readable diagnostic report.

    Args:
        artifact: Captured report document or generation failure.
        color: Include ANSI styling in the output.

    Capture has already reduced ordinary failures into a reusable artifact.
    """
    try:
        if isinstance(artifact, ReportFailure):
            return _render_text_failure(artifact.error_name, color)
        return "\n".join(render_lines(artifact, color=color)) + "\n"
    except Exception as exc:
        return _render_text_failure(type_name(exc), color)


def get_report(artifact: "ReportDocument | ReportFailure") -> "ReportJSON":
    """Return the structured report document for a captured artifact.

    Capture has already reduced ordinary failures into a reusable artifact.
    Projection failures are represented by a generation-failed document.
    """
    try:
        if isinstance(artifact, ReportFailure):
            return failed_json_document(artifact.error_name)
        return json_document(artifact)
    except Exception as exc:
        return failed_json_document(type_name(exc))


def _serialize_json(document: "ReportJSON") -> str:
    """Serialize one structured report for an output destination."""
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _render_text_failure(error_name: str, color: bool) -> str:
    """Render a human-readable failure artifact."""
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


@contextmanager
def _atomic_writer(path: "str | PathLike[str]") -> "Iterator[IO[str]]":
    """Yield a same-directory temp file, then atomically replace ``path`` on clean exit.

    If the body raises, the temp file is discarded and ``path`` is left
    untouched, so no partial content ever reaches the destination.
    """
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
            yield temporary
        os.replace(temporary_path, absolute)
        temporary_path = None
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                os.unlink(temporary_path)


def _write_atomic(path: "str | PathLike[str]", rendered: str) -> None:
    """Write through a same-directory temporary file, then atomically replace."""
    with _atomic_writer(path) as temporary:
        temporary.write(rendered)


def _write_text_file(path: "str | PathLike[str]", document: "ReportDocument", *, color: bool) -> None:
    """Stream a text report into the atomic temp file, one line at a time.

    A mid-render failure rewinds the temp file and writes the failure report
    instead; because nothing has reached ``path`` yet, the fallback is intact.
    """
    with _atomic_writer(path) as temporary:
        try:
            for line in iter_report_lines(document, color=color):
                temporary.write(line + "\n")
        except Exception as exc:
            temporary.seek(0)
            temporary.truncate()
            temporary.write(_render_text_failure(type_name(exc), color))
