import json
import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

DEEP_CAPTURE = r"""
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
        if self.fullname == "deep_broken":
            raise RuntimeError("target failure")
        module.VALUE = self.fullname


class Finder:
    def __init__(self):
        self.loaders = []

    def find_spec(self, fullname, target=None):
        if fullname == "deep_outer":
            __import__("deep_nested")
        if fullname not in {"deep_outer", "deep_nested", "deep_broken"}:
            return None
        loader = Loader(fullname)
        self.loaders.append(loader)
        return importlib.machinery.ModuleSpec(fullname, loader)


finder = Finder()
sentinel = sys.path[0] + "\\deep-sentinel"


def hook(path):
    if path != sentinel:
        raise ImportError
    return finder


sys.path_hooks.insert(0, hook)
sys.path_importer_cache.pop(sentinel, None)
sys.path.insert(0, sentinel)
monitor = metapathology.install(
    report_at_exit=False,
    deep_path_hooks=True,
    deep_path_entry_finders=True,
    deep_loaders=True,
)
assert hook not in sys.path_hooks
assert any(getattr(candidate, "__wrapped__", None) is hook for candidate in sys.path_hooks)


def later_hook(path):
    raise ImportError


sys.path_hooks.append(later_hook)
assert later_hook not in sys.path_hooks
assert any(getattr(candidate, "__wrapped__", None) is later_hook for candidate in sys.path_hooks)
sys.path_importer_cache.pop(sentinel, None)
__import__("deep_outer")
try:
    __import__("deep_broken")
except RuntimeError:
    pass
else:
    raise AssertionError("loader exception changed")
assert isinstance(sys.path_importer_cache[sentinel], Finder)
document = json.loads(metapathology.render_report(format="json"))
deep = [event for event in document["timeline"] if event["kind"] == "deep_diagnostic_call"]
assert {event["boundary"] for event in deep} == {
    "path_hook", "path_entry_finder", "loader_create_module", "loader_exec_module"
}, deep
assert any(event["outcome"] == "unobserved_reentrant" for event in deep), deep
assert any(event["fullname"] == "deep_broken" and event["outcome"] == "raised" for event in deep), deep
assert document["capture"]["mechanisms"][4]["enabled"] is True
text = metapathology.render_report()
assert "WARNING: opt-in deep diagnostics" in text
metapathology.uninstall()
assert hook in sys.path_hooks
assert later_hook in sys.path_hooks
assert "find_spec" not in finder.__dict__
assert all("create_module" not in loader.__dict__ and "exec_module" not in loader.__dict__ for loader in finder.loaders)
print(json.dumps({"deep": deep, "mechanisms": monitor.deep_diagnostics}))
"""


def test_deep_boundaries_delegate_record_and_restore(run_python: RunPython) -> None:
    proc = run_python(DEEP_CAPTURE)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["mechanisms"] == []


def test_distinct_hooks_accepting_one_path_report_structural_shadow(run_python: RunPython, tmp_path: Path) -> None:
    proc = run_python(
        "import json, sys, metapathology\n"
        "class Finder:\n"
        "    def find_spec(self, fullname, target=None): return None\n"
        "def first(path):\n"
        "    if path == sys.argv[1]: return Finder()\n"
        "    raise ImportError\n"
        "def second(path):\n"
        "    if path == sys.argv[1]: return Finder()\n"
        "    raise ImportError\n"
        "sys.path_hooks[:0] = [first, second]\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "sys.path_importer_cache.pop(sys.argv[1], None)\n"
        "metapathology.install(report_at_exit=False, deep_path_hooks=True)\n"
        "sys.path_importer_cache.pop(sys.argv[1], None)\n"
        "try: __import__('shadow_first_missing')\n"
        "except ModuleNotFoundError: pass\n"
        "list.__setitem__(sys.path_hooks, slice(0, 2), [sys.path_hooks[1], sys.path_hooks[0]])\n"
        "sys.path_importer_cache.pop(sys.argv[1], None)\n"
        "try: __import__('shadow_second_missing')\n"
        "except ModuleNotFoundError: pass\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "finding = next(item for item in document['findings'] if item['kind'] == 'path_hook_shadow')\n"
        "assert finding['subject'] == {'kind': 'path', 'value': sys.argv[1]}\n"
        "assert finding['evidence']['level'] == 'structural_inference'\n"
        "assert len(finding['evidence']['event_refs']) == 2\n"
        "assert '[path-hook-shadow]' in metapathology.render_report()\n"
        "print('OK')\n",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


DISCORD_10017_MODULE_REPLACEMENT = r"""
import importlib.machinery
import json
import sys
import types

import metapathology

class ReplacingLoader:
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        module.LOADED = True

class Finder:
    def __init__(self):
        self.loader = ReplacingLoader()
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "deep_identity_ext":
            return None
        return importlib.machinery.ModuleSpec(fullname, self.loader, origin="shared.ext")

finder = Finder()
sys.meta_path.insert(0, finder)
monitor = metapathology.install(report_at_exit=False, deep_loaders=True)
import deep_identity_ext
first = deep_identity_ext
spec = first.__spec__
second = types.ModuleType("deep_identity_ext")
second.__spec__ = spec
second.__loader__ = spec.loader
sys.modules["deep_identity_ext"] = second
spec.loader.exec_module(second)
sys.modules["deep_identity_ext"] = first

events = [
    event for event in monitor.events()
    if isinstance(event, metapathology.DeepDiagnosticCall)
    and event.boundary == "loader_exec_module"
    and event.fullname == "deep_identity_ext"
]
assert len(events) == 2, events
assert events[0].module_state_before.object_id == id(first)
assert events[0].module_state_after.object_id == id(first)
assert events[0].target_state.object_id == id(first)
assert events[1].module_state_before.object_id == id(second)
assert events[1].module_state_after.object_id == id(second)
assert events[1].target_state.object_id == id(second)
assert first.__spec__.origin == second.__spec__.origin == "shared.ext"
document = json.loads(metapathology.render_report(format="json"))
finding = next(item for item in document["findings"] if item["kind"] == "module_replacement")
assert finding["module"] == "deep_identity_ext"
assert finding["evidence"]["level"] == "captured"
assert set(finding["evidence"]["event_refs"]) == {"event:" + str(events[0].seq), "event:" + str(events[1].seq)}
assert finding["deep_call"]["event_ref"] == "event:" + str(events[1].seq)
assert finding["module_state_baseline"]["object_id"] == hex(id(first))
assert finding["deep_call"]["module_state_before"]["object_id"] == hex(id(second))
assert finding["deep_call"]["module_state_after"]["object_id"] == hex(id(second))
assert finding["deep_call"]["target_state"]["object_id"] == hex(id(second))
explanation = next(item for item in document["explanations"] if item["kind"] == "module_replacement")
assert explanation["confidence"] == "captured"
assert explanation["cause_finding_ref"] == finding["id"]
assert explanation["state_before"]["object_id"] == hex(id(first))
assert explanation["state_after"]["object_id"] == hex(id(second))
assert explanation["effect_status"] == "separate_module_executed"
text = metapathology.render_report()
assert "[module-replacement] 'deep_identity_ext'" in text, text
assert "internal steps and temporary objects are unknown" in text, text
assert "[captured] ReplacingLoader executed a separate module object for 'deep_identity_ext'" in text, text
metapathology.uninstall()
print("OK")
"""


def test_discord_10017_valid_spec_module_replacement_fixture(run_python: RunPython) -> None:
    proc = run_python(DISCORD_10017_MODULE_REPLACEMENT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_deep_mechanisms_are_independently_toggleable(run_python: RunPython) -> None:
    proc = run_python(
        "import metapathology\n"
        "monitor = metapathology.install(\n"
        "    report_at_exit=False, monitor_path_hooks=False, monitor_importer_cache=False,\n"
        "    deep_path_entry_finders=True,\n"
        ")\n"
        "assert monitor.deep_diagnostics == ('path_entry_finders',)\n"
        "assert type(__import__('sys').path_hooks) is list\n"
        "metapathology.uninstall()\n"
    )
    assert proc.returncode == 0, proc.stderr


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
elif mode == "deep":
    metapathology.install(
        report_at_exit=False,
        deep_path_hooks=True,
        deep_path_entry_finders=True,
        deep_loaders=True,
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


def test_deep_monitoring_preserves_import_outcomes(run_python: RunPython) -> None:
    outcomes = []
    for mode in ("disabled", "default", "deep"):
        proc = run_python(EQUIVALENT_OUTCOMES, mode)
        assert proc.returncode == 0, proc.stderr
        outcomes.append(json.loads(proc.stdout))
    assert outcomes[0] == outcomes[1] == outcomes[2]


def test_shared_loader_uses_each_call_actual_module_name(run_python: RunPython) -> None:
    proc = run_python(
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
        "metapathology.install(report_at_exit=False, deep_loaders=True)\n"
        "import shared_first, shared_second\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "calls = [event for event in document['timeline'] if event.get('boundary', '').startswith('loader_')]\n"
        "assert {(event['boundary'], event['fullname']) for event in calls} == {\n"
        "    ('loader_create_module', 'shared_first'), ('loader_exec_module', 'shared_first'),\n"
        "    ('loader_create_module', 'shared_second'), ('loader_exec_module', 'shared_second'),\n"
        "}, calls\n"
        "assert shared_first.VALUE == 'shared_first' and shared_second.VALUE == 'shared_second'\n"
        "metapathology.uninstall()\n"
        "assert 'create_module' not in loader.__dict__ and 'exec_module' not in loader.__dict__\n"
    )
    assert proc.returncode == 0, proc.stderr


def test_partial_deep_install_and_hostile_cleanup_are_isolated(run_python: RunPython) -> None:
    proc = run_python(
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
        "    report_at_exit=False, deep_path_hooks=True, deep_path_entry_finders=True\n"
        ")\n"
        "assert 'find_spec' in ordinary.__dict__\n"
        "class HostileHooks:\n"
        "    def __iter__(self): raise RuntimeError('hostile cleanup')\n"
        "sys.path_hooks = HostileHooks()\n"
        "metapathology.uninstall()\n"
        "assert 'find_spec' not in ordinary.__dict__\n"
        "where = [event.where for event in monitor.events() if isinstance(event, metapathology.InternalError)]\n"
        "assert 'deep_path_entry_finder' in where\n"
        "assert 'uninstall_deep_path_hooks' in where\n"
    )
    assert proc.returncode == 0, proc.stderr


IMPORT_OUTCOMES = r"""
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
        return ModuleSpec(fullname, BrokenLoader()) if fullname == "deep_outcome_broken" else None

monitor = metapathology.install(report_at_exit=False, deep_import_outcomes=True)
sys.meta_path.insert(0, BrokenFinder())
import deep_outcome_loaded
del sys.modules["deep_outcome_loaded"]
try: import deep_outcome_missing
except ModuleNotFoundError: pass
try: import deep_outcome_broken
except RuntimeError: pass
import deep_outcome_cached
__import__("deep_outcome_cached")
thread = threading.Thread(target=lambda: __import__("deep_outcome_threaded"))
thread.start(); thread.join()

document = json.loads(metapathology.render_report(format="json"))
by_name = {}
for attempt in document["import_attempts"]:
    by_name.setdefault(attempt["fullname"], []).append(attempt)
assert by_name["deep_outcome_loaded"][0]["progress"] == "loaded"
assert by_name["deep_outcome_loaded"][0]["presence"] == "absent_at_report"
assert by_name["deep_outcome_nested"][0]["progress"] == "loaded"
assert by_name["deep_outcome_missing"][0]["progress"] == "failed"
assert by_name["deep_outcome_broken"][0]["progress"] == "failed"
assert by_name["deep_outcome_threaded"][0]["progress"] == "loaded"
assert len(by_name["deep_outcome_cached"]) == 1
failed = [item for item in document["findings"] if item["kind"] == "failed_after_mutation"]
broken = next(item for item in failed if item["module"] == "deep_outcome_broken")
assert broken["evidence"]["level"] == "structural_inference"
assert len(broken["evidence"]["event_refs"]) == 3
mechanism = next(item for item in document["capture"]["mechanisms"] if item["name"] == "deep_import_outcomes")
assert mechanism["completeness"].endswith("cache_hits_not_observed")
assert "import outcome observation:" in metapathology.render_report()
assert "[failed-after-mutation] 'deep_outcome_broken'" in metapathology.render_report()
metapathology.uninstall()
assert sys.getprofile() is None and threading.getprofile() is None
print("OK")
"""


def test_exact_import_outcomes_and_runtime_coverage(run_python: RunPython, tmp_path: Path) -> None:
    for name in ("deep_outcome_nested", "deep_outcome_cached", "deep_outcome_threaded"):
        (tmp_path / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "deep_outcome_loaded.py").write_text("import deep_outcome_nested\nVALUE = 1\n", encoding="utf-8")
    proc = run_python(IMPORT_OUTCOMES)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_deep_outcomes_capture_path_finder_aggregate(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "standard_aggregate_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = run_python(
        "import sys, metapathology\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "monitor = metapathology.install(report_at_exit=False, deep_import_outcomes=True)\n"
        "import standard_aggregate_target\n"
        "events = [event for event in monitor.events() "
        "if isinstance(event, metapathology.StandardFinderCall) "
        "and event.fullname == 'standard_aggregate_target']\n"
        "assert len(events) == 1, events\n"
        "event = events[0]\n"
        "assert event.finder_type_name == 'PathFinder'\n"
        "assert event.spec_summary.loader.type_name == 'SourceFileLoader'\n"
        "assert event.attempt_id > 0\n"
        "metapathology.uninstall()\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_exact_outcomes_refuse_an_existing_profiler(run_python: RunPython) -> None:
    proc = run_python(
        "import sys, metapathology\n"
        "profile = lambda frame, event, arg: None\n"
        "sys.setprofile(profile)\n"
        "monitor = metapathology.install(report_at_exit=False, deep_import_outcomes=True)\n"
        "assert monitor.deep_import_outcomes_status == 'refused_existing_profiler'\n"
        "assert monitor.standard_finder_status == 'unavailable_existing_profiler'\n"
        "assert sys.getprofile() is profile\n"
        "metapathology.uninstall()\n"
        "assert sys.getprofile() is profile\n"
        "sys.setprofile(None)\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
