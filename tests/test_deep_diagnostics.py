import json
import subprocess
from collections.abc import Callable

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
