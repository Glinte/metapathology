"""Bounded, report-time replay of displaced importer-cache finders.

The scenario reproduces the beartype#599 shape with real finders: a finder is
cached for a path entry, a later cache change displaces it with one that finds
nothing, and a subsequent lookup on that path fails. Speculative replay then
asks the retained displaced finder whether it returns a spec for the failed
module now.
"""

import subprocess
from collections.abc import Callable

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

# Shared preamble: install cache + path-hook monitoring, then arrange a real
# cache displacement whose intermediate state is observed at path-hook
# mutations. ``RETAINED_RESULT`` decides what the displaced finder returns when
# it is replayed at report time.
SCENARIO = r"""
import json
import sys
import importlib.machinery

import metapathology

# Importing the public record must not raise; the replay results themselves are
# read back from the JSON/text projections below.
from metapathology import SpeculativeReplay as _SpeculativeReplay  # noqa: F401

P = "<virtual-entry>"


class RetainedFinder:
    def find_spec(self, fullname, target=None):
        if fullname.startswith("ghostmod") and RETAINED_RESULT == "spec":
            return importlib.machinery.ModuleSpec(fullname, loader=object())
        if fullname.startswith("ghostmod") and RETAINED_RESULT == "raise":
            raise RuntimeError("boom")
        return None


class DisplacingFinder:
    def find_spec(self, fullname, target=None):
        return None


monitor = metapathology.install(
    report_at_exit=False,
    monitor_importer_cache=True,
    monitor_path_hooks=True,
)

retained = RetainedFinder()
displacing = DisplacingFinder()

decline = lambda path: (_ for _ in ()).throw(ImportError)

# Cache P -> retained, then observe the state via a path-hooks mutation.
sys.path_importer_cache[P] = retained
sys.path_hooks.append(decline)

# Displace: P -> displacing, observe again.
sys.path_importer_cache[P] = displacing
sys.path_hooks.append(decline)

# Instrument the current cache finder and enable replay.
metapathology.install(deep_path_entry_finders=True, speculative_replay=True)
"""


def _run(run_python: RunPython, retained_result: str, body: str) -> "subprocess.CompletedProcess[str]":
    code = f"RETAINED_RESULT = {retained_result!r}\n" + SCENARIO + body
    return run_python(code)


RETURNS_SPEC = r"""
# A later lookup on P fails through the displacing finder.
assert displacing.find_spec("ghostmod", None) is None

document = json.loads(metapathology.render_report(format="json"))
block = document["speculative_replay"]
assert block["enabled"] is True
assert block["probe_cap"] == 16
assert len(block["replays"]) == 1, block
replay = block["replays"][0]
assert replay["fullname"] == "ghostmod"
assert replay["path"] == P
assert replay["outcome"] == "returned_spec"
assert replay["displaced_finder"]["type_name"] == "RetainedFinder"
assert replay["state_phase"] == "report"
assert replay["spec"] is not None

text = metapathology.render_report(format="text")
assert "-- speculative replays (1) --" in text
assert "displaced RetainedFinder" in text
assert "currently returns a spec" in text
# The wording must never claim the original import would have succeeded.
assert "would have succeeded" not in text
print("OK")
"""


def test_displaced_finder_that_still_resolves_is_reported(run_python: RunPython) -> None:
    proc = _run(run_python, "spec", RETURNS_SPEC)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


RETURNS_NONE = r"""
assert displacing.find_spec("ghostmod", None) is None

document = json.loads(metapathology.render_report(format="json"))
replays = document["speculative_replay"]["replays"]
assert len(replays) == 1, replays
assert replays[0]["outcome"] == "returned_none"
assert replays[0]["spec"] is None
print("OK")
"""


def test_displaced_finder_that_no_longer_resolves_is_reported(run_python: RunPython) -> None:
    proc = _run(run_python, "none", RETURNS_NONE)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


RAISES = r"""
assert displacing.find_spec("ghostmod", None) is None

document = json.loads(metapathology.render_report(format="json"))
replays = document["speculative_replay"]["replays"]
assert len(replays) == 1, replays
assert replays[0]["outcome"] == "raised"
assert replays[0]["exception_type_name"] == "RuntimeError"
print("OK")
"""


def test_replayed_finder_that_raises_cannot_break_the_report(run_python: RunPython) -> None:
    proc = _run(run_python, "raise", RAISES)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


DECLINES_TARGET = r"""
# A failed lookup that carried a reload target cannot be faithfully replayed.
assert displacing.find_spec("ghostmod", object()) is None

document = json.loads(metapathology.render_report(format="json"))
replays = document["speculative_replay"]["replays"]
assert len(replays) == 1, replays
assert replays[0]["outcome"] == "declined_target_unavailable"
print("OK")
"""


def test_reload_target_lookups_are_declined_not_answered_wrongly(run_python: RunPython) -> None:
    proc = _run(run_python, "spec", DECLINES_TARGET)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


PROBE_CAP = r"""
# Each distinct failed name on P is one candidate; exceed the fixed cap.
for index in range(20):
    assert displacing.find_spec("ghostmod_%d" % index, None) is None

document = json.loads(metapathology.render_report(format="json"))
block = document["speculative_replay"]
assert len(block["replays"]) == block["probe_cap"] == 16, block
assert block["omitted"] == 4, block

text = metapathology.render_report(format="text")
assert "4 more candidates omitted at the per-report cap" in text
print("OK")
"""


def test_replay_is_capped_and_reports_the_overflow(run_python: RunPython) -> None:
    proc = _run(run_python, "spec", PROBE_CAP)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


REPEAT_REPORTS_DO_NOT_GROW_EVENTS = r"""
assert displacing.find_spec("ghostmod", None) is None

before = len(monitor.events())
first = json.loads(metapathology.render_report(format="json"))
second = json.loads(metapathology.render_report(format="json"))
after = len(monitor.events())

# Replay is a report-phase computation: it never appends to the monitor log,
# so repeated reports neither grow the log nor drop the replay result.
assert before == after, (before, after)
assert len(first["speculative_replay"]["replays"]) == 1
assert len(second["speculative_replay"]["replays"]) == 1
print("OK")
"""


def test_repeated_reports_do_not_grow_the_event_log(run_python: RunPython) -> None:
    proc = _run(run_python, "spec", REPEAT_REPORTS_DO_NOT_GROW_EVENTS)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


OFF_BY_DEFAULT = r"""
import json
import sys
import importlib.machinery

import metapathology

P = "<virtual-entry>"


class RetainedFinder:
    def find_spec(self, fullname, target=None):
        return importlib.machinery.ModuleSpec(fullname, loader=object())


class DisplacingFinder:
    def find_spec(self, fullname, target=None):
        return None


metapathology.install(report_at_exit=False, monitor_importer_cache=True, monitor_path_hooks=True)
decline = lambda path: (_ for _ in ()).throw(ImportError)
sys.path_importer_cache[P] = RetainedFinder()
sys.path_hooks.append(decline)
displacing = DisplacingFinder()
sys.path_importer_cache[P] = displacing
sys.path_hooks.append(decline)
metapathology.install(deep_path_entry_finders=True)  # replay NOT requested
assert displacing.find_spec("ghostmod", None) is None

document = json.loads(metapathology.render_report(format="json"))
block = document["speculative_replay"]
assert block["enabled"] is False
assert block["replays"] == []
assert "speculative replays" not in metapathology.render_report(format="text").lower() \
    or "Nothing was recorded" in metapathology.render_report(format="text")
print("OK")
"""


def test_replay_performs_no_foreign_calls_unless_requested(run_python: RunPython) -> None:
    proc = run_python(OFF_BY_DEFAULT)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
