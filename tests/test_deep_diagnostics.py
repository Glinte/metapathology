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
assert {event["boundary"] for event in deep} == {"path_hook", "path_entry_finder", "loader_exec_module"}, deep
assert any(event["outcome"] == "unobserved_reentrant" for event in deep), deep
assert any(event["fullname"] == "deep_broken" and event["outcome"] == "raised" for event in deep), deep
assert document["capture"]["mechanisms"][4]["enabled"] is True
text = metapathology.render_report()
assert "WARNING: opt-in deep diagnostics" in text
metapathology.uninstall()
assert hook in sys.path_hooks
assert later_hook in sys.path_hooks
assert "find_spec" not in finder.__dict__
assert all("exec_module" not in loader.__dict__ for loader in finder.loaders)
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
