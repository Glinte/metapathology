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
timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path mutations", 1)[0]
positions = [timeline.index(f"#{event.seq}") for event in (hook, cache, start, claim)]
assert positions == sorted(positions), timeline
assert "import started: 'timeline_target'" in timeline
assert "Events are numbered in the order they were recorded" in timeline

document = json.loads(metapathology.render_report(format="json"))
assert document["schema"] == {"major": 1, "minor": 3, "name": "metapathology.report"}
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
import os
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
os.environ["METAPATHOLOGY_TEXT_TIMELINE"] = "full"
text = metapathology.render_report()
timeline = text.split("-- event timeline", 1)[1]
line = next(line for line in timeline.splitlines() if "metapathology_missing_timeline_module" in line)
assert "import started" in line, line
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
timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path reassignments", 1)[0]
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


COLLAPSE_RUN = r"""
import sys

import metapathology
from metapathology import FindSpecCall, ImportAuditStart

class DecliningFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DecliningFinder())

for name in ("collapse_missing_one", "collapse_missing_two", "collapse_missing_three"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError(f"{name} unexpectedly resolved")

events = monitor.events()
collapsible_lines = []
for event in events:
    if isinstance(event, ImportAuditStart):
        collapsible_lines.append(f"#{event.seq} import started")
    elif isinstance(event, FindSpecCall) and not event.found and event.exception_type_name is None:
        collapsible_lines.append(f"#{event.seq} DecliningFinder probed")

text = metapathology.render_report()
timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path mutations", 1)[0]
assert "(collapsed)" in timeline, timeline
for line in collapsible_lines:
    assert line not in timeline, (line, timeline)
print("OK")
"""


def test_run_of_four_or_more_collapsible_events_collapses(run_python: RunPython) -> None:
    proc = run_python(COLLAPSE_RUN)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


COLLAPSE_DEEP_RUN = r"""
import importlib.machinery
import sys

import metapathology


class Loader:
    def __init__(self, fullname):
        self.fullname = fullname

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if self.fullname == "deep_collapse_outer":
            for name in ("deep_collapse_m1", "deep_collapse_m2", "deep_collapse_m3", "deep_collapse_m4"):
                __import__(name)


class Finder:
    def find_spec(self, fullname, target=None):
        if not fullname.startswith("deep_collapse"):
            return None
        return importlib.machinery.ModuleSpec(fullname, Loader(fullname))


finder = Finder()
sentinel = sys.path[0] + "\\deep-collapse-sentinel"


def hook(path):
    if path != sentinel:
        raise ImportError
    return finder


sys.path_hooks.insert(0, hook)
sys.path_importer_cache.pop(sentinel, None)
sys.path.insert(0, sentinel)
metapathology.install(
    report_at_exit=False,
    deep_path_hooks=True,
    deep_path_entry_finders=True,
    deep_loaders=True,
)
sys.path_importer_cache.pop(sentinel, None)
__import__("deep_collapse_outer")
text = metapathology.render_report()
timeline = text.split("-- event timeline", 1)[1]
assert "unobserved_reentrant" not in timeline, timeline
assert "not_found" not in timeline, timeline
assert "nested; result not recorded" in text, text
assert "nested calls (collapsed)" in timeline, timeline
print("OK")
"""


def test_deep_events_render_in_plain_words_and_routine_runs_collapse(run_python: RunPython) -> None:
    proc = run_python(COLLAPSE_DEEP_RUN)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


CLAIM_SPLITS_RUN = r"""
import sys
from importlib.machinery import PathFinder

import metapathology
from metapathology import FindSpecCall

class DecliningFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "claim_split_target":
            return PathFinder.find_spec(fullname, path, target)
        return None

metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DecliningFinder())

for name in ("claim_split_missing_one", "claim_split_missing_two"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        pass

import claim_split_target

for name in ("claim_split_missing_three", "claim_split_missing_four"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        pass

monitor = metapathology.get_monitor()
events = monitor.events()
claim = next(
    event for event in events
    if isinstance(event, FindSpecCall) and event.fullname == "claim_split_target" and event.found
)

text = metapathology.render_report()
timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path mutations", 1)[0]
assert f"#{claim.seq} DecliningFinder.find_spec('claim_split_target'): found it" in timeline, timeline
print("OK")
"""


def test_claim_in_middle_of_collapsible_run_splits_the_collapse(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "claim_split_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    proc = run_python(CLAIM_SPLITS_RUN)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


FINDING_REFERENCED_EVENT = r"""
import importlib.machinery
import json
import sys

import metapathology

class TruncatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "ref_ns":
            return None
        spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
        spec.submodule_search_locations = [sys.argv[2]]
        return spec

class DecliningFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

sys.path.insert(0, sys.argv[1])
metapathology.install(report_at_exit=False, deep_import_outcomes=True)
sys.meta_path.insert(0, TruncatingFinder())
sys.meta_path.insert(0, DecliningFinder())

for name in ("ref_pad_one", "ref_pad_two"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        pass

try:
    import ref_ns.child
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("truncated namespace unexpectedly found child")

for name in ("ref_pad_three", "ref_pad_four"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        pass

document = json.loads(metapathology.render_report(format="json"))
explanation = next(item for item in document["explanations"] if item["kind"] == "namespace_truncation_failure")
referenced_seqs = [int(ref.split(":", 1)[1]) for ref in explanation["event_refs"]]
assert referenced_seqs, explanation

text = metapathology.render_report()
timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path mutations", 1)[0]
for seq in referenced_seqs:
    assert f"#{seq} " in timeline, (seq, timeline)
print("OK")
"""


def test_event_referenced_by_explanation_renders_expanded_inside_a_run(run_python: RunPython, tmp_path: Path) -> None:
    omitted_root = tmp_path / "installed"
    child = omitted_root / "ref_ns" / "child"
    child.mkdir(parents=True)
    (child / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    claimed = tmp_path / "editable" / "ref_ns"
    claimed.mkdir(parents=True)

    proc = run_python(FINDING_REFERENCED_EVENT, str(omitted_root), str(claimed))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


TIMELINE_FULL_ESCAPE_HATCH = r"""
import os
import sys

import metapathology
from metapathology import FindSpecCall, ImportAuditStart

os.environ["METAPATHOLOGY_TEXT_TIMELINE"] = "full"

class DecliningFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None

monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DecliningFinder())

for name in ("full_missing_one", "full_missing_two", "full_missing_three"):
    try:
        __import__(name)
    except ModuleNotFoundError:
        pass

events = monitor.events()
text = metapathology.render_report()
timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path mutations", 1)[0]
assert "(collapsed)" not in timeline, timeline
for event in events:
    assert f"#{event.seq} " in timeline, (event, timeline)
print("OK")
"""


def test_timeline_full_env_var_disables_collapsing(run_python: RunPython) -> None:
    proc = run_python(TIMELINE_FULL_ESCAPE_HATCH)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
