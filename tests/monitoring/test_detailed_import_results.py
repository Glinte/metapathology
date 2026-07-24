"""Detailed import-result capture and profiler interaction."""

import json
from pathlib import Path

from support import PythonRunner

EQUIVALENT_OUTCOMES = r"""
import importlib.machinery
import json
import sys

import metapathology


class Loader:
    def __init__(self, fullname):
        self.fullname = fullname

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if self.fullname == "equivalent_broken":
            raise LookupError("same failure")
        if self.fullname == "equivalent_recursive":
            __import__("equivalent_nested")
        module.VALUE = self.fullname


class Finder:
    def find_spec(self, fullname, target=None):
        if fullname not in {
            "equivalent_ok", "equivalent_broken", "equivalent_recursive", "equivalent_nested"
        }:
            return None
        return importlib.machinery.ModuleSpec(fullname, Loader(fullname))


finder = Finder()
sentinel = sys.path[0] + "\\equivalence"


def hook(path):
    if path == sentinel:
        return finder
    raise ImportError


sys.path_hooks.insert(0, hook)
sys.path_importer_cache.pop(sentinel, None)
sys.path.insert(0, sentinel)
mode = sys.argv[1]
if mode == "default":
    metapathology.install(report_at_exit=False)
elif mode == "detailed":
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            detailed=metapathology.DetailedCaptureConfig(
                path_hooks=True,
                path_entry_finders=True,
                loaders=True,
            )
        ),
    )
    sys.path_importer_cache.pop(sentinel, None)
outcomes = []
for name in ("equivalent_ok", "equivalent_missing", "equivalent_broken", "equivalent_recursive"):
    try:
        first = __import__(name)
        second = __import__(name)
        outcomes.append((name, "returned", first.VALUE, first is second))
    except BaseException as exc:
        outcomes.append((name, "raised", type(exc).__name__))
print(json.dumps(outcomes))
"""


def test_detailed_monitoring_preserves_import_results(python_runner: PythonRunner) -> None:
    outcomes = []
    for mode in ("disabled", "default", "detailed"):
        proc = python_runner.run_code_ok(EQUIVALENT_OUTCOMES, mode)
        assert proc.returncode == 0, proc.stderr
        outcomes.append(json.loads(proc.stdout))
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_shared_loader_uses_each_call_actual_module_name(python_runner: PythonRunner) -> None:
    python_runner.run_code_ok(
        "import importlib.machinery\n"
        "import json\n"
        "import sys\n"
        "import metapathology\n"
        "class Loader:\n"
        "    def create_module(self, spec): return None\n"
        "    def exec_module(self, module): module.VALUE = module.__name__\n"
        "class Finder:\n"
        "    def __init__(self, loader): self.loader = loader\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname in {'shared_first', 'shared_second'}:\n"
        "            return importlib.machinery.ModuleSpec(fullname, self.loader)\n"
        "        return None\n"
        "loader = Loader()\n"
        "finder = Finder(loader)\n"
        "sys.meta_path.insert(0, finder)\n"
        "metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(loaders=True)))\n"
        "import shared_first, shared_second\n"
        "document = metapathology.get_report()\n"
        "calls = [event['data'] for event in document['timeline'] "
        "if event['kind'] == 'import_mechanism_call' and event['data']['boundary'].startswith('loader_')]\n"
        "assert {(event['boundary'], event['fullname']) for event in calls} == {\n"
        "    ('loader_create_module', 'shared_first'), ('loader_exec_module', 'shared_first'),\n"
        "    ('loader_create_module', 'shared_second'), ('loader_exec_module', 'shared_second'),\n"
        "}, calls\n"
        "assert shared_first.VALUE == 'shared_first' and shared_second.VALUE == 'shared_second'\n"
        "metapathology.uninstall()\n"
        "assert 'create_module' not in loader.__dict__ and 'exec_module' not in loader.__dict__\n"
    )


def test_partial_detailed_install_and_hostile_cleanup_are_isolated(python_runner: PythonRunner) -> None:
    python_runner.run_code_ok(
        "import sys\n"
        "import metapathology\n"
        "class HostileFinder:\n"
        "    def __getattribute__(self, name):\n"
        "        if name == '__dict__': raise RuntimeError('hostile')\n"
        "        return object.__getattribute__(self, name)\n"
        "    def find_spec(self, fullname, target=None): return None\n"
        "class OrdinaryFinder:\n"
        "    def find_spec(self, fullname, target=None): return None\n"
        "ordinary = OrdinaryFinder()\n"
        "sys.path_importer_cache['hostile'] = HostileFinder()\n"
        "sys.path_importer_cache['ordinary'] = ordinary\n"
        "monitor = metapathology.install(\n"
        "    report_at_exit=False,\n"
        "    capture=metapathology.CaptureConfig(\n"
        "        detailed=metapathology.DetailedCaptureConfig(\n"
        "            path_hooks=True,\n"
        "            path_entry_finders=True,\n"
        "        )\n"
        "    ),\n"
        ")\n"
        "assert 'find_spec' in ordinary.__dict__\n"
        "class HostileHooks:\n"
        "    def __iter__(self): raise RuntimeError('hostile cleanup')\n"
        "sys.path_hooks = HostileHooks()\n"
        "metapathology.uninstall()\n"
        "assert 'find_spec' not in ordinary.__dict__\n"
        "where = [event.where for event in monitor.events() if isinstance(event, metapathology.MonitoringError)]\n"
        "assert 'detailed_path_entry_finder' in where\n"
        "assert 'uninstall_detailed_path_hooks' in where\n"
    )


IMPORT_RESULTS = r"""
import json
import sys
import threading
from importlib.machinery import ModuleSpec
import metapathology

class BrokenLoader:
    def create_module(self, spec): return None
    def exec_module(self, module): raise RuntimeError("broken")
class BrokenFinder:
    def find_spec(self, fullname, path=None, target=None):
        return ModuleSpec(fullname, BrokenLoader()) if fullname == "detailed_outcome_broken" else None

monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)))
sys.meta_path.insert(0, BrokenFinder())
import detailed_outcome_loaded
del sys.modules["detailed_outcome_loaded"]
try: import detailed_outcome_missing
except ModuleNotFoundError: pass
try: import detailed_outcome_broken
except RuntimeError: pass
import detailed_outcome_cached
__import__("detailed_outcome_cached")
thread = threading.Thread(target=lambda: __import__("detailed_outcome_threaded"))
thread.start(); thread.join()

document = metapathology.get_report()
by_name = {}
for attempt in document["import_searches"]:
    by_name.setdefault(attempt["fullname"], []).append(attempt)
assert by_name["detailed_outcome_loaded"][0]["progress"] == "loaded"
assert by_name["detailed_outcome_loaded"][0]["presence"] == "absent_at_report"
assert by_name["detailed_outcome_nested"][0]["progress"] == "loaded"
assert by_name["detailed_outcome_missing"][0]["progress"] == "failed"
assert by_name["detailed_outcome_broken"][0]["progress"] == "failed"
assert by_name["detailed_outcome_threaded"][0]["progress"] == "loaded"
assert len(by_name["detailed_outcome_cached"]) == 1
assert not any(item["kind"] == "import_failed_after_state_change" for item in document["findings"])
mechanism = next(item for item in document["capture"]["mechanisms"] if item["name"] == "import_results")
assert mechanism["completeness"].endswith("cache_hits_not_observed")
assert "import outcome observation:" in metapathology.render_report()
assert "[import-failed-after-state-change]" not in metapathology.render_report()
metapathology.uninstall()
assert sys.getprofile() is None and threading.getprofile() is None
print("OK")
"""


def test_exact_import_results_and_runtime_coverage(python_runner: PythonRunner, tmp_path: Path) -> None:
    for name in ("detailed_outcome_nested", "detailed_outcome_cached", "detailed_outcome_threaded"):
        (tmp_path / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "detailed_outcome_loaded.py").write_text(
        "import detailed_outcome_nested\nVALUE = 1\n", encoding="utf-8"
    )
    proc = python_runner.run_code_ok(IMPORT_RESULTS)
    assert proc.stdout.strip() == "OK"


def test_import_failed_after_state_change_requires_mutation_inside_failed_attempt(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "mutated_during_resolution.py").write_text(
        "import sys\nsys.meta_path.append(object())\nraise RuntimeError('broken')\n",
        encoding="utf-8",
    )
    proc = python_runner.run_code_ok(
        "import json, sys, metapathology\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)))\n"
        "try:\n"
        "    import mutated_during_resolution\n"
        "except RuntimeError:\n"
        "    pass\n"
        "document = metapathology.get_report()\n"
        "finding = next(item for item in document['findings'] "
        "if item['kind'] == 'import_failed_after_state_change')\n"
        "assert finding['module'] == 'mutated_during_resolution'\n"
        "assert len(finding['evidence']['event_refs']) == 3\n"
        "print('OK')\n",
        str(tmp_path),
    )
    assert proc.stdout.strip() == "OK"


def test_detailed_outcomes_capture_path_finder_aggregate(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "standard_aggregate_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = python_runner.run_code_ok(
        "import sys, metapathology\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)))\n"
        "import standard_aggregate_target\n"
        "events = [event for event in monitor.events() "
        "if isinstance(event, metapathology.PathFinderCall) "
        "and event.fullname == 'standard_aggregate_target']\n"
        "assert len(events) == 1, events\n"
        "event = events[0]\n"
        "assert event.finder_type_name == 'PathFinder'\n"
        "assert event.spec_summary.loader.type_name == 'SourceFileLoader'\n"
        "assert event.search_id > 0\n"
        "metapathology.uninstall()\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_exact_outcomes_refuse_an_existing_profiler(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys, metapathology\n"
        "profile = lambda frame, event, arg: None\n"
        "sys.setprofile(profile)\n"
        "monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)))\n"
        "assert monitor.import_results_capture_status == 'refused_existing_profiler'\n"
        "assert monitor.path_finder_capture_status == 'unavailable_existing_profiler'\n"
        "assert sys.getprofile() is profile\n"
        "metapathology.uninstall()\n"
        "assert sys.getprofile() is profile\n"
        "sys.setprofile(None)\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_exact_outcomes_preserve_later_profiler_replacements(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys, threading, metapathology\n"
        "monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)))\n"
        "sys_profile = lambda frame, event, arg: None\n"
        "thread_profile = lambda frame, event, arg: None\n"
        "sys.setprofile(sys_profile)\n"
        "threading.setprofile(thread_profile)\n"
        "metapathology.uninstall()\n"
        "assert sys.getprofile() is sys_profile\n"
        "assert threading.getprofile() is thread_profile\n"
        "sys.setprofile(None)\n"
        "threading.setprofile(None)\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"
