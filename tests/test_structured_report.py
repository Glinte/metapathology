"""Structured report schema and explicit file-output behavior."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


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
assert document["schema"] == {"major": 0, "minor": 3, "name": "metapathology.report"}
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
assert mechanisms["importer_cache_snapshots"]["capacity"] == 2
assert mechanisms["importer_cache_snapshots"]["overflow_policy"] == "replace_latest"
no_spec = next(finding for finding in document["findings"] if finding["kind"] == "no_spec")
assert no_spec["module"] == "ghost_mod"
assert no_spec["evidence"] == {"finder_claim": "not_recorded", "module_spec": "missing"}
assert document["diagnostics"]["report_errors"] == []
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
        "from metapathology._report_data import ReportDocument\n"
        "assert '__dataclass_fields__' not in ReportDocument.__dict__\n"
        "assert '__slots__' in ReportDocument.__dict__\n"
        "document = ReportDocument(\n"
        "    generated_at='now', cutoff_seq=0, monitor_enabled=False,\n"
        "    baseline_module_count=0, initial_meta_path=(), current_meta_path=(),\n"
        "    path_hooks_enabled=False, initial_path_hooks=(), current_path_hooks=None,\n"
        "    importer_cache_enabled=False, initial_importer_cache=(),\n"
        "    initial_importer_cache_non_string_keys=0, current_importer_cache=None,\n"
        "    current_importer_cache_non_string_keys=None, importer_cache_observations=0,\n"
        "    importer_cache_coalesced=0,\n"
        "    modules_since_install=(), events=(), skipped_finders=(), findings=(),\n"
        "    report_errors=(), cwd=None, argv=(),\n"
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
