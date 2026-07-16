"""Unified capture-order timeline and import-audit start evidence."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


TIMELINE = r"""
import json
import sys
from importlib.machinery import PathFinder

import metapathology
from metapathology import FindSpecCall, ImportAuditStart, ImporterCacheDiff, PathHooksMutation

class DelegatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "timeline_target":
            return PathFinder.find_spec(fullname, path, target)
        return None

def probe_hook(path):
    raise ImportError

monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DelegatingFinder())
sys.path_hooks.append(probe_hook)
sys.path_importer_cache["timeline-cache-probe"] = None
sys.path_hooks.reverse()
import timeline_target

events = monitor.events()
hook = next(event for event in events if isinstance(event, PathHooksMutation) and event.op == "append")
cache = next(
    event
    for event in events
    if isinstance(event, ImporterCacheDiff)
    and any(entry.path == "timeline-cache-probe" for entry in event.added)
)
start = next(event for event in events if isinstance(event, ImportAuditStart) and event.fullname == "timeline_target")
claim = next(
    event
    for event in events
    if isinstance(event, FindSpecCall) and event.fullname == "timeline_target" and event.found
)
assert hook.seq < cache.seq < start.seq < claim.seq, (hook, cache, start, claim)
assert start.meta_path_id == id(sys.meta_path)
assert start.meta_path_type_names[0] == "DelegatingFinder"
assert start.path_hooks_id == id(sys.path_hooks)
assert start.importer_cache_id == id(sys.path_importer_cache)
assert start.importer_cache_size is not None
assert start.attempt_id > 0
assert start.thread_id == claim.thread_id

text = metapathology.render_report()
timeline = text.split("-- chronological evidence timeline", 1)[1].split("-- sys.meta_path mutations", 1)[0]
positions = [timeline.index(f"#{event.seq}") for event in (hook, cache, start, claim)]
assert positions == sorted(positions), timeline
assert "resolution started for 'timeline_target'" in timeline
assert "the audit event has no outcome or winner signal" in timeline
assert "capture order; concurrent events are not a global wall-clock order" in timeline

document = json.loads(metapathology.render_report(format="json"))
assert document["schema"] == {"major": 0, "minor": 16, "name": "metapathology.report"}
audit = next(
    event
    for event in document["timeline"]
    if event["kind"] == "import_audit_start" and event["fullname"] == "timeline_target"
)
assert audit["evidence"] == "resolution_started"
assert audit["attempt_id"] == start.attempt_id
assert audit["thread_id"] == start.thread_id
assert audit["meta_path"]["entries"][0] == "DelegatingFinder"
assert audit["meta_path"]["object_id"].startswith("0x")
assert audit["path_hooks_id"].startswith("0x")
assert audit["importer_cache"]["size"] >= 1
attempt = next(item for item in document["import_attempts"] if item["fullname"] == "timeline_target")
assert attempt["id"] == f"attempt:{start.attempt_id}"
assert attempt["start_event_ref"] == f"event:{start.seq}"
assert attempt["evidence_event_refs"] == [f"event:{claim.seq}"]
assert attempt["progress"] == "finder_claimed"
assert attempt["presence"] == "present_at_report"
mechanisms = {item["name"]: item for item in document["capture"]["mechanisms"]}
assert mechanisms["import_audit_starts"]["overflow_policy"] == "retain_all"
assert mechanisms["import_audit_starts"]["completeness"] == "resolution_starts"
assert mechanisms["import_audit_starts"]["retained"] >= 1
print("OK")
"""


def test_timeline_interleaves_all_capture_mechanisms(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "timeline_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    proc = run_python(TIMELINE)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


THREAD_IDENTITIES = r"""
import threading

import metapathology
from metapathology import ImportAuditStart

monitor = metapathology.install(report_at_exit=False)

def import_first():
    import thread_identity_first

def import_second():
    import thread_identity_second

first = threading.Thread(target=import_first, name="duplicate-name")
first.start()
first.join()
second = threading.Thread(target=import_second, name="duplicate-name")
second.start()
second.join()

starts = {
    event.fullname: event
    for event in monitor.events()
    if isinstance(event, ImportAuditStart) and event.fullname.startswith("thread_identity_")
}
assert starts["thread_identity_first"].thread_name == starts["thread_identity_second"].thread_name
assert starts["thread_identity_first"].thread_id != starts["thread_identity_second"].thread_id
assert starts["thread_identity_first"].attempt_id != starts["thread_identity_second"].attempt_id
print("OK")
"""


def test_monitor_identities_do_not_join_threads_by_reused_name(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "thread_identity_first.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "thread_identity_second.py").write_text("VALUE = 2\n", encoding="utf-8")

    proc = run_python(THREAD_IDENTITIES)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


AUDIT_SHAPES = r"""
import importlib
import sys

import metapathology
from metapathology import ImportAuditStart, InternalError

monitor = metapathology.install(report_at_exit=False)
import colorsys
first_count = sum(
    isinstance(event, ImportAuditStart) and event.fullname == "colorsys"
    for event in monitor.events()
)
import colorsys
second_count = sum(
    isinstance(event, ImportAuditStart) and event.fullname == "colorsys"
    for event in monitor.events()
)
assert first_count == second_count == 1

import _decimal
assert sum(
    isinstance(event, ImportAuditStart) and event.fullname == "_decimal"
    for event in monitor.events()
) == 1

sys.audit("import", "synthetic-malformed")
assert not any(
    isinstance(event, ImportAuditStart) and event.fullname == "synthetic-malformed"
    for event in monitor.events()
)
assert not any(isinstance(event, InternalError) and event.where == "audit_hook" for event in monitor.events())

with open("importlib_audit_blindspot.py", "w", encoding="utf-8") as module_file:
    module_file.write("VALUE = 1\n")
importlib.import_module("importlib_audit_blindspot")
assert not any(
    isinstance(event, ImportAuditStart) and event.fullname == "importlib_audit_blindspot"
    for event in monitor.events()
)

try:
    import metapathology_missing_timeline_module
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("missing import unexpectedly succeeded")
text = metapathology.render_report()
line = next(line for line in text.splitlines() if "metapathology_missing_timeline_module" in line)
assert "resolution started" in line, line
assert "the audit event has no outcome or winner signal" in text
print("OK")
"""


def test_audit_starts_exclude_cache_hits_native_load_events_and_malformed_events(run_python: RunPython) -> None:
    proc = run_python(AUDIT_SHAPES)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


DEVIATION = r"""
import sys

import metapathology

metapathology.install(report_at_exit=False)
import colorsys
sys.meta_path = list(sys.meta_path)
import graphlib

text = metapathology.render_report()
timeline = text.split("-- chronological evidence timeline", 1)[1].split("-- sys.meta_path reassignments", 1)[0]
assert "sys.path_hooks and sys.path_importer_cache identities stable across all audited imports" in timeline, timeline
assert "\n    meta_path 0x" in timeline, timeline
print("OK")
"""


def test_timeline_details_meta_path_identity_only_on_deviation(run_python: RunPython) -> None:
    proc = run_python(DEVIATION)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


REENTRANT = r"""
import sys

import metapathology
from metapathology import ImportAuditStart

class ReentrantFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "outer_timeline_module":
            import colorsys
        return None

monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, ReentrantFinder())
import outer_timeline_module
starts = [event.fullname for event in monitor.events() if isinstance(event, ImportAuditStart)]
assert "outer_timeline_module" in starts
assert "colorsys" not in starts
print("OK")
"""


def test_reentrant_imports_remain_unobserved_inside_finder_wrapper(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "outer_timeline_module.py").write_text("VALUE = 1\n", encoding="utf-8")

    proc = run_python(REENTRANT)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


REASSIGNMENT = r"""
import sys

import metapathology
from metapathology import ImportAuditStart, MetaPathReassignment

monitor = metapathology.install(report_at_exit=False)
sys.meta_path = list(sys.meta_path)
import reassignment_timeline_module
events = monitor.events()
start = next(
    event
    for event in events
    if isinstance(event, ImportAuditStart) and event.fullname == "reassignment_timeline_module"
)
reassignment = next(
    event
    for event in events
    if isinstance(event, MetaPathReassignment) and event.during_import == "reassignment_timeline_module"
)
assert start.seq < reassignment.seq, (start, reassignment)
assert start.meta_path_id != id(sys.meta_path)

event_count = len(events)
metapathology.uninstall()
import graphlib
assert len(monitor.events()) == event_count
print("OK")
"""


def test_audit_start_precedes_reassignment_recovery_and_uninstall_is_inert(
    run_python: RunPython, tmp_path: Path
) -> None:
    (tmp_path / "reassignment_timeline_module.py").write_text("VALUE = 1\n", encoding="utf-8")

    proc = run_python(REASSIGNMENT)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
