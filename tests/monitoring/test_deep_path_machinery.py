"""Deep path-hook, path-entry-finder, and loader instrumentation."""

import json
from pathlib import Path

from support import PythonRunner

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
    capture=metapathology.CaptureConfig(
        deep=metapathology.DeepConfig(
            path_hooks=True,
            path_entry_finders=True,
            loaders=True,
        )
    ),
)
# The wrapper shadows the hook by identity but compares equal to it, so
# ``in`` (which uses ``==``) still finds it while ``is`` sees the wrapper.
assert hook in sys.path_hooks
assert not any(candidate is hook for candidate in sys.path_hooks)
assert any(getattr(candidate, "__wrapped__", None) is hook for candidate in sys.path_hooks)


def later_hook(path):
    raise ImportError


sys.path_hooks.append(later_hook)
assert later_hook in sys.path_hooks
assert not any(candidate is later_hook for candidate in sys.path_hooks)
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
deep = [event["data"] for event in document["timeline"] if event["kind"] == "deep_diagnostic_call"]
assert {event["boundary"] for event in deep} == {
    "path_hook", "path_entry_finder", "loader_create_module", "loader_exec_module"
}, deep
assert any(event["outcome"] == "unobserved_reentrant" for event in deep), deep
# A successful path hook captures the identity of the finder it returned, so the
# report can link an accepting hook to the finder it installed in the cache.
assert any(
    event["boundary"] == "path_hook"
    and event["outcome"] == "returned"
    and event["returned_finder"] is not None
    and event["returned_finder"]["type_name"] == "Finder"
    for event in deep
), deep
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


def test_deep_boundaries_delegate_record_and_restore(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(DEEP_CAPTURE)
    result = json.loads(proc.stdout)
    assert result["mechanisms"] == []


def test_distinct_hooks_accepting_one_path_report_structural_shadow(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    proc = python_runner.run_code_ok(
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
        "metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(deep=metapathology.DeepConfig(path_hooks=True)))\n"
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
    assert proc.stdout.strip() == "OK"


def test_deep_path_hook_wrapper_survives_equality_scan(python_runner: PythonRunner, tmp_path: Path) -> None:
    """A third party locating its own hook by ``==`` must still find it.

    PyInstaller's ``PyiFrozenFinder.fallback_finder`` walks ``sys.path_hooks``
    testing ``hook == self.path_hook`` to find the hooks after its own and
    build a fallback ``FileFinder``. A deep wrapper that did not compare equal
    to the shadowed hook broke that scan, silently disabling on-disk extension
    imports in frozen apps. The wrapper delegates equality, so the scan works.
    """
    proc = python_runner.run_code_ok(
        "import sys, metapathology\n"
        "created = []\n"
        "sentinel = sys.argv[1]\n"
        "class FallbackFinder:\n"
        "    def find_spec(self, fullname, target=None): return None\n"
        "def anchor_hook(path):\n"
        "    raise ImportError\n"
        "def tail_hook(path):\n"
        "    if path != sentinel:\n"
        "        raise ImportError\n"
        "    created.append(path)\n"
        "    return FallbackFinder()\n"
        "sys.path_hooks[:0] = [anchor_hook, tail_hook]\n"
        "metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(deep=metapathology.DeepConfig(path_hooks=True)))\n"
        # Emulate the fallback scan: find our anchor by equality, then build
        # the first hook that comes after it.
        "anchor_seen = False\n"
        "fallback = None\n"
        "for hook in sys.path_hooks:\n"
        "    if hook == anchor_hook:\n"
        "        anchor_seen = True\n"
        "        continue\n"
        "    if not anchor_seen:\n"
        "        continue\n"
        "    try:\n"
        "        fallback = hook(sentinel)\n"
        "        break\n"
        "    except ImportError:\n"
        "        pass\n"
        "assert anchor_seen, 'anchor hook not found via == scan'\n"
        "assert isinstance(fallback, FallbackFinder), fallback\n"
        "assert created == [sentinel], created\n"
        "print('OK')\n",
        str(tmp_path),
    )
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
monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(deep=metapathology.DeepConfig(loaders=True)))
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
finding = next(item for item in document["findings"] if item["kind"] == "repeated_loader_execution")
assert finding["module"] == "deep_identity_ext"
assert finding["evidence"]["level"] == "captured"
assert set(finding["evidence"]["event_refs"]) == {"event:" + str(events[0].seq), "event:" + str(events[1].seq)}
assert finding["data"]["deep_call"]["event_ref"] == "event:" + str(events[1].seq)
assert finding["data"]["module_state_baseline"]["object_id"] == hex(id(first))
assert finding["data"]["deep_call"]["module_state_before"]["object_id"] == hex(id(second))
assert finding["data"]["deep_call"]["module_state_after"]["object_id"] == hex(id(second))
assert finding["data"]["deep_call"]["target_state"]["object_id"] == hex(id(second))
explanation = next(item for item in document["explanations"] if item["kind"] == "repeated_loader_execution")
assert explanation["confidence"] == "captured"
assert explanation["cause_finding_ref"] == finding["id"]
assert explanation["state_before"]["object_id"] == hex(id(first))
assert explanation["state_after"]["object_id"] == hex(id(second))
assert explanation["effect_status"] == "separate_module_executed"
text = metapathology.render_report()
assert "[repeated-loader-execution] 'deep_identity_ext'" in text, text
assert "internal steps and temporary objects are unknown" in text, text
assert "[captured] ReplacingLoader executed 'deep_identity_ext' again with a different module object" in text, text
metapathology.uninstall()
print("OK")
"""


def test_discord_10017_valid_spec_module_replacement_fixture(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(DISCORD_10017_MODULE_REPLACEMENT)
    assert proc.stdout.strip() == "OK"


def test_deep_mechanisms_are_independently_toggleable(python_runner: PythonRunner) -> None:
    python_runner.run_code_ok(
        "import metapathology\n"
        "monitor = metapathology.install(\n"
        "    report_at_exit=False,\n"
        "    capture=metapathology.CaptureConfig(\n"
        "        path_hooks=False,\n"
        "        importer_cache=False,\n"
        "        deep=metapathology.DeepConfig(path_entry_finders=True),\n"
        "    ),\n"
        ")\n"
        "assert monitor.deep_diagnostics == ('path_entry_finders',)\n"
        "assert type(__import__('sys').path_hooks) is list\n"
        "metapathology.uninstall()\n"
    )


def test_deep_path_entry_finder_preserves_later_shadow(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys, metapathology\n"
        "class Finder:\n"
        "    def find_spec(self, fullname, target=None): return None\n"
        "finder = Finder()\n"
        "sys.path_importer_cache['owned-finder-probe'] = finder\n"
        "metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(deep=metapathology.DeepConfig(path_entry_finders=True)))\n"
        "replacement = lambda fullname, target=None: None\n"
        "finder.find_spec = replacement\n"
        "metapathology.uninstall()\n"
        "assert finder.__dict__['find_spec'] is replacement\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_deep_loader_preserves_later_method_shadows(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import importlib.util, sys, metapathology\n"
        "class Loader:\n"
        "    def create_module(self, spec): return None\n"
        "    def exec_module(self, module): module.VALUE = 1\n"
        "class Finder:\n"
        "    def __init__(self, loader): self.loader = loader\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'owned_loader_probe':\n"
        "            return importlib.util.spec_from_loader(fullname, self.loader)\n"
        "        return None\n"
        "loader = Loader()\n"
        "finder = Finder(loader)\n"
        "sys.meta_path.insert(0, finder)\n"
        "metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(deep=metapathology.DeepConfig(loaders=True)))\n"
        "import owned_loader_probe\n"
        "replacement_create = lambda spec: None\n"
        "replacement_exec = lambda module: None\n"
        "loader.create_module = replacement_create\n"
        "loader.exec_module = replacement_exec\n"
        "metapathology.uninstall()\n"
        "assert loader.__dict__['create_module'] is replacement_create\n"
        "assert loader.__dict__['exec_module'] is replacement_exec\n"
        "sys.meta_path.remove(finder)\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"


def test_deep_path_hooks_restore_only_owned_wrappers(python_runner: PythonRunner) -> None:
    proc = python_runner.run_code_ok(
        "import sys, metapathology\n"
        "original_hooks = list(sys.path_hooks)\n"
        "metapathology.install(\n"
        "    report_at_exit=False,\n"
        "    capture=metapathology.CaptureConfig(\n"
        "        path_hooks=True,\n"
        "        deep=metapathology.DeepConfig(path_hooks=True),\n"
        "    ),\n"
        ")\n"
        "replacement = lambda path: (_ for _ in ()).throw(ImportError)\n"
        "sys.path_hooks[0] = replacement\n"
        "metapathology.uninstall()\n"
        "assert sys.path_hooks[0] is replacement\n"
        "assert all(current is original for current, original in zip(sys.path_hooks[1:], original_hooks[1:]))\n"
        "sys.path_hooks = original_hooks\n"
        "print('OK')\n"
    )
    assert proc.stdout.strip() == "OK"
