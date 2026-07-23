"""Timeline text rendering, collapsing, and expansion behavior."""

from pathlib import Path

from support import PythonRunner

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


def test_run_of_four_or_more_collapsible_events_collapses(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(COLLAPSE_RUN)

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


def test_deep_events_render_in_plain_words_and_routine_runs_collapse(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(COLLAPSE_DEEP_RUN)

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


def test_claim_in_middle_of_collapsible_run_splits_the_collapse(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "claim_split_target.py").write_text("VALUE = 1\n", encoding="utf-8")

    proc = python_runner.run_code_ok(CLAIM_SPLITS_RUN)

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


def test_event_referenced_by_explanation_renders_expanded_inside_a_run(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    omitted_root = tmp_path / "installed"
    child = omitted_root / "ref_ns" / "child"
    child.mkdir(parents=True)
    (child / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    claimed = tmp_path / "editable" / "ref_ns"
    claimed.mkdir(parents=True)

    proc = python_runner.run_code_ok(FINDING_REFERENCED_EVENT, str(omitted_root), str(claimed))

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


def test_timeline_full_env_var_disables_collapsing(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(TIMELINE_FULL_ESCAPE_HATCH)

    assert proc.stdout.strip() == "OK"
