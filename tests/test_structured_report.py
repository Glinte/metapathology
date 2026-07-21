"""Structured report schema and explicit file-output behavior."""

import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from metapathology._records import InternalError, MonitorEvent
from metapathology._report_json import _json_events

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


class _CountingEvents:
    """Iterable that fails a regression back to repeated event-log scans."""

    def __init__(self, events: tuple[MonitorEvent, ...]) -> None:
        self.events = events
        self.iterations = 0

    def __iter__(self) -> Iterator[MonitorEvent]:
        self.iterations += 1
        yield from self.events


def test_json_event_projection_traverses_exhaustive_log_once() -> None:
    events = _CountingEvents((InternalError(7, "audit_hook", "RuntimeError"),))

    timeline, counts, internal_error_refs = _json_events(events)

    assert events.iterations == 1
    assert timeline[0]["kind"] == "internal_error"
    assert internal_error_refs == ["event:7"]
    assert all(count == 0 for count in counts.values())


JSON_REPORT = r"""
import json
import sys
import types

import metapathology

class ProbeFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

metapathology.install(report_at_exit=False)
sys.path_importer_cache["structured-cache-probe"] = None
sys.meta_path = list(sys.meta_path)
sys.path_hooks = list(sys.path_hooks)
import reassignment_mod
def probe_hook(path):
    return None
sys.path_hooks.append(probe_hook)
sys.meta_path.insert(0, ProbeFinder())
import observed_mod
sys.modules["ghost_mod"] = types.ModuleType("ghost_mod")

document = json.loads(metapathology.render_report(format="json"))
assert document["schema"] == {"major": 1, "minor": 3, "name": "metapathology.report"}
assert document["report_status"] == "complete"
assert isinstance(document["resolution_routes"], list)
assert isinstance(document["route_comparisons"], list)
assert document["capture"]["early_site_bootstrap"] is None
assert document["capture"]["frozen_bootstrap"] is None
assert document["capture"]["cutoff_seq"] == max(event["seq"] for event in document["timeline"])
assert document["snapshots"][0]["id"] == "snapshot:install"
assert document["snapshots"][1]["id"] == "snapshot:report"
kinds = {event["kind"] for event in document["timeline"]}
assert "meta_path_mutation" in kinds, kinds
assert "meta_path_reassignment" in kinds, kinds
assert "path_hooks_mutation" in kinds, kinds
assert "path_hooks_reassignment" in kinds, kinds
assert "find_spec_call" in kinds, kinds
assert "importer_cache_diff" in kinds, kinds
assert "import_audit_start" in kinds, kinds
assert all(event["id"] == f"event:{event['seq']}" for event in document["timeline"])
stack_event = next(event for event in document["timeline"] if event["kind"] == "meta_path_mutation")
assert set(stack_event["stack"][0]) == {"filename", "function", "lineno"}
path_hook_event = next(event for event in document["timeline"] if event["kind"] == "path_hooks_mutation")
assert path_hook_event["added"][0]["name"] == "probe_hook"
assert path_hook_event["added"][0]["object_id"].startswith("0x")
snapshot_ids = {snapshot["id"] for snapshot in document["snapshots"]}
assert "snapshot:path-hooks:install" in snapshot_ids
assert "snapshot:path-hooks:report" in snapshot_ids
assert "snapshot:importer-cache:install" in snapshot_ids
assert "snapshot:importer-cache:report" in snapshot_ids
mechanisms = {mechanism["name"]: mechanism for mechanism in document["capture"]["mechanisms"]}
assert mechanisms["path_hooks_mutations"]["overflow_policy"] == "retain_all"
assert mechanisms["path_hooks_mutations"]["retained"] >= 1
assert mechanisms["finder_contracts"]["overflow_policy"] == "retain_all"
assert mechanisms["finder_contracts"]["retained"] >= 4
assert mechanisms["resolution_route_analysis"]["overflow_policy"] == "retain_all"
assert mechanisms["resolution_route_analysis"]["shutdown"] == "synchronous_no_retry"
assert all(mechanism["shutdown"] == "synchronous_no_retry" for mechanism in mechanisms.values())
assert mechanisms["resolution_route_analysis"]["retained"] == len(document["resolution_routes"])
assert mechanisms["resolution_route_analysis"]["comparison_count"] == len(document["route_comparisons"])
assert mechanisms["importer_cache_snapshots"]["capacity"] == 2
assert mechanisms["importer_cache_snapshots"]["overflow_policy"] == "replace_latest"
no_spec = next(finding for finding in document["findings"] if finding["kind"] == "no_spec")
assert no_spec["module"] == "ghost_mod"
assert no_spec["evidence"] == {
    "event_refs": [],
    "finder_claim": "not_recorded",
    "level": "post_hoc",
    "limitations": ["report_snapshot_cannot_identify_load_mechanism"],
    "module_spec": "missing",
}
assert document["diagnostics"]["report_errors"] == []
assert document["loader_inventory"]["evidence"] == "post_hoc"
assert document["loader_inventory"]["phase"] == "report"
assert document["loader_inventory"]["available"] is True
print("OK")
"""


def test_json_report_is_versioned_and_preserves_timeline_records(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "observed_mod.py").write_text("VALUE = 1\n")
    (tmp_path / "reassignment_mod.py").write_text("VALUE = 2\n")
    proc = run_python(JSON_REPORT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


EXPLICIT_FILE = r"""
import json
import pathlib
import sys

import metapathology

destination = pathlib.Path(sys.argv[1])
metapathology.install(report_at_exit=False)
metapathology.write_report(destination, format="json")
document = json.loads(destination.read_text(encoding="utf-8"))
assert document["process"]["pid"] > 0
assert destination.exists()
print("OK")
"""


def test_explicit_file_write_uses_exact_path(run_python: RunPython, tmp_path: Path) -> None:
    destination = tmp_path / "exact.json"
    proc = run_python(EXPLICIT_FILE, str(destination))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
    assert destination.is_file()
    assert list(tmp_path.glob("exact.*.json")) == []


def test_failure_report_has_the_same_top_level_contract(run_python: RunPython) -> None:
    proc = run_python(
        "import metapathology\n"
        "from metapathology._report_json import failed_json_document\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "complete = __import__('json').loads(metapathology.render_report(format='json'))\n"
        "failed = failed_json_document('BrokenReport')\n"
        "assert set(failed) == set(complete)\n"
        "assert failed['report_status'] == 'generation_failed'\n"
        "assert failed['standard_resolutions'] == []\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_bundled_json_schema_matches_report_version(run_python: RunPython) -> None:
    proc = run_python(
        "import importlib.resources, json\n"
        "import metapathology\n"
        "schema = json.loads(importlib.resources.files('metapathology').joinpath('report.schema.json').read_text())\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "report = json.loads(metapathology.render_report(format='json'))\n"
        "version = schema['$defs']['SchemaVersion']['properties']\n"
        "assert version['major']['const'] == report['schema']['major']\n"
        "assert version['minor']['const'] == report['schema']['minor']\n"
        "assert set(schema['required']) == set(report)\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_bundled_json_schema_is_current() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/generate_report_schema.py", "--check"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


REFERENCE_REPORT = r"""
import importlib
import json
import sys

import metapathology

metapathology.install(report_at_exit=False)
for index in range(int(sys.argv[1])):
    importlib.import_module(f"generated_reference_{index}")
document = json.loads(metapathology.render_report(format="json"))

ids = set()
for section_name in (
    "snapshots", "finder_contracts", "import_attempts", "resolution_routes",
    "route_comparisons", "timeline", "findings", "explanations",
):
    for item in document[section_name]:
        assert item["id"] not in ids, item["id"]
        ids.add(item["id"])

def check(value, location="report"):
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key.endswith("_ref"):
                assert child is None or child in ids, (child_location, child)
            elif key.endswith("_refs"):
                assert isinstance(child, list)
                assert all(reference in ids for reference in child), (child_location, child)
            else:
                check(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check(child, f"{location}[{index}]")

check(document)
print("OK")
"""


@given(module_count=st.integers(min_value=0, max_value=12))
@settings(max_examples=6, deadline=None, suppress_health_check=(HealthCheck.function_scoped_fixture,))
def test_all_document_references_resolve(
    run_python: RunPython,
    tmp_path: Path,
    module_count: int,
) -> None:
    for index in range(module_count):
        (tmp_path / f"generated_reference_{index}.py").write_text(f"VALUE = {index}\n")
    proc = run_python(REFERENCE_REPORT, str(module_count))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


WRITE_FAILURE = r"""
import pathlib
import sys

import metapathology
from metapathology import InternalError

monitor = metapathology.install(report_at_exit=False)
try:
    metapathology.write_report(pathlib.Path(sys.argv[1]), format="json")
except OSError:
    pass
else:
    raise AssertionError("explicit write failure did not propagate")
assert any(
    isinstance(event, InternalError) and event.where == "report_write"
    for event in monitor.events()
)
document = __import__("json").loads(metapathology.render_report(format="json"))
assert "internal_error" in {event["kind"] for event in document["timeline"]}
assert document["diagnostics"]["internal_error_refs"]
print("OK")
"""


def test_explicit_file_failure_is_recorded_and_raised(run_python: RunPython, tmp_path: Path) -> None:
    destination = tmp_path / "missing" / "report.json"
    proc = run_python(WRITE_FAILURE, str(destination))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_report_document_uses_hand_written_slots(run_python: RunPython) -> None:
    proc = run_python(
        "from metapathology._report_model import LoaderInventory, ReportDocument, ReportSummary\n"
        "assert '__dataclass_fields__' not in ReportDocument.__dict__\n"
        "assert '__slots__' in ReportDocument.__dict__\n"
        "document = ReportDocument(\n"
        "    generated_at='now', cutoff_seq=0, monitor_enabled=False,\n"
        "    baseline_module_count=0, initial_meta_path=(), current_meta_path=(),\n"
        "    path_hooks_enabled=False, initial_path_hooks=(), current_path_hooks=None,\n"
        "    sys_path_enabled=False,\n"
        "    importer_cache_enabled=False, initial_importer_cache=(),\n"
        "    initial_importer_cache_non_string_keys=0, current_importer_cache=None,\n"
        "    current_importer_cache_non_string_keys=None, importer_cache_observations=0,\n"
        "    importer_cache_coalesced=0,\n"
        "    loader_inventory=LoaderInventory(True, (), 0), attempts=(),\n"
        "    modules_since_install=(), events=(), explanations=(), skipped_finders=(), standard_resolutions=(),\n"
        "    resolution_routes=(), route_comparisons=(),\n"
        "    standard_finder_status='disabled', findings=(), finder_contracts=(),\n"
        "    early_site_bootstrap=None, frozen_bootstrap=None,\n"
        "    deep_diagnostics=(), deep_import_outcomes_status='disabled',\n"
        "    deep_import_calls_status='disabled',\n"
        "    report_errors=(),\n"
        "    summary=ReportSummary(actionable=0, warning=0, informational=0,\n"
        "                          unresolved_import_count=0, top_finding_id=None, top_explanation_id=None),\n"
        "    target_outcome=None, cwd=None, argv=(),\n"
        ")\n"
        "try:\n"
        "    document.cutoff_seq = 1\n"
        "except AttributeError as exc:\n"
        "    assert 'read-only' in str(exc)\n"
        "else:\n"
        "    raise AssertionError('report documents must be immutable')\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
