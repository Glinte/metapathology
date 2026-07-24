"""Structured report schema and explicit file-output behavior."""

import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from support import PythonRunner, assert_mapping_contains, one_matching

from metapathology._records import MonitorEvent, MonitoringError
from metapathology._report_json import _json_events
from metapathology._report_model import ReportDocument


class _CountingEvents:
    """Iterable that fails a regression back to repeated event-log scans."""

    def __init__(self, events: tuple[MonitorEvent, ...]) -> None:
        self.events = events
        self.iterations = 0

    def __iter__(self) -> Iterator[MonitorEvent]:
        self.iterations += 1
        yield from self.events


def test_json_event_projection_traverses_exhaustive_log_once() -> None:
    events = _CountingEvents((MonitoringError(7, "audit_hook", "RuntimeError"),))

    timeline, counts, monitoring_error_refs = _json_events(events)

    assert events.iterations == 1
    error = one_matching(timeline, kind="monitoring_error")
    assert_mapping_contains(error, id="event:7", kind="monitoring_error")
    assert monitoring_error_refs == ["event:7"]
    assert all(count == 0 for count in counts.values())


def test_json_report_is_versioned_and_preserves_timeline_records(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "observed_mod.py").write_text("VALUE = 1\n")
    (tmp_path / "reassignment_mod.py").write_text("VALUE = 2\n")
    python_runner.run_scenario_ok("reporting/structured_report.py", "json_report")


def test_explicit_file_write_uses_exact_path(python_runner: PythonRunner, tmp_path: Path) -> None:
    destination = tmp_path / "exact.json"
    python_runner.run_scenario_ok("reporting/structured_report.py", "explicit_file", str(destination))
    assert destination.is_file()
    assert list(tmp_path.glob("exact.*.json")) == []


def test_captured_report_supports_schema_failure_and_multiple_exports(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("reporting/structured_report.py", "report_contracts")


def test_bundled_json_schema_is_current() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/generate_report_schema.py", "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


@given(module_count=st.integers(min_value=0, max_value=12))
@settings(max_examples=6, deadline=None, suppress_health_check=(HealthCheck.function_scoped_fixture,))
def test_all_document_references_resolve(
    python_runner: PythonRunner,
    tmp_path: Path,
    module_count: int,
) -> None:
    for index in range(module_count):
        (tmp_path / f"generated_reference_{index}.py").write_text(f"VALUE = {index}\n")
    python_runner.run_scenario_ok("reporting/structured_report.py", "reference_report", str(module_count))


def test_explicit_file_failure_is_recorded_and_raised(python_runner: PythonRunner, tmp_path: Path) -> None:
    destination = tmp_path / "missing" / "report.json"
    python_runner.run_scenario_ok("reporting/structured_report.py", "write_failure", str(destination))


def test_report_document_uses_hand_written_slots() -> None:
    assert "__dataclass_fields__" not in ReportDocument.__dict__
    assert "__slots__" in ReportDocument.__dict__

    document = object.__new__(ReportDocument)
    with pytest.raises(AttributeError, match="read-only"):
        setattr(document, "capture", None)
