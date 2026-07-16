"""Counterfactual replay evidence and failure isolation."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


CHANGED_LOADER = r"""
import json
import os
import sys
from importlib.machinery import PathFinder, SourceFileLoader
from importlib.util import spec_from_file_location

import metapathology

class InitialLoader(SourceFileLoader):
    pass

class CurrentLoader(SourceFileLoader):
    pass

class InitialPathEntryFinder:
    def find_spec(self, fullname, target=None):
        if fullname != "counterfactual_target":
            return None
        source = os.path.join(module_dir, "counterfactual_target.py")
        return spec_from_file_location(fullname, source, loader=InitialLoader(fullname, source))

class CurrentPathEntryFinder:
    def find_spec(self, fullname, target=None):
        if fullname != "counterfactual_target":
            return None
        source = os.path.join(module_dir, "counterfactual_target.py")
        return spec_from_file_location(fullname, source, loader=CurrentLoader(fullname, source))

def initial_hook(path):
    if os.path.normcase(os.path.abspath(path)) == normalized_module_dir:
        return InitialPathEntryFinder()
    raise ImportError

def current_hook(path):
    if os.path.normcase(os.path.abspath(path)) == normalized_module_dir:
        return CurrentPathEntryFinder()
    raise ImportError

class DelegatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        return PathFinder.find_spec(fullname, path, target)

module_dir = sys.argv[1]
normalized_module_dir = os.path.normcase(os.path.abspath(module_dir))
sys.path.insert(0, module_dir)
sys.path_hooks[0:0] = [initial_hook, current_hook]
sys.path_importer_cache.pop(module_dir, None)

finder = DelegatingFinder()
metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, finder)
import counterfactual_target
assert type(counterfactual_target.__loader__).__name__ == "InitialLoader"

sys.path_hooks[:2] = [current_hook, initial_hook]
sys.path_importer_cache.pop(module_dir, None)
current_spec = PathFinder.find_spec("counterfactual_target", [module_dir])
assert current_spec is not None
assert type(current_spec.loader).__name__ == "CurrentLoader"

document = json.loads(metapathology.render_report(format="json"))
finding = next(item for item in document["findings"] if item["module"] == "counterfactual_target")
assert finding["kind"] == "bypass"
assert finding["path_finder_replay"]["evidence_level"] == "live_replay"
assert finding["path_finder_replay"]["state_phase"] == "report"
assert finding["path_finder_replay"]["loader_type_name"] == "CurrentLoader"
comparison = finding["structural_comparison"]
assert comparison["evidence_level"] == "structural_comparison"
assert comparison["path_hooks"] == {
    "changed": True,
    "install_snapshot_ref": "snapshot:path-hooks:install",
    "report_snapshot_ref": "snapshot:path-hooks:report",
}
cache = comparison["importer_cache"]
assert cache["changed"] is True
assert cache["install_snapshot_ref"] == "snapshot:importer-cache:install"
assert cache["report_snapshot_ref"] == "snapshot:importer-cache:report"
assert module_dir in cache["changed_paths"]
assert cache["change_event_refs"]

text = metapathology.render_report()
assert "structural evidence: sys.path_hooks changed" in text, text
assert "PathFinder replay: loader CurrentLoader" in text, text
print("OK")
"""


def test_hook_reorder_and_cache_clear_change_live_replay_loader(
    run_python: RunPython,
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "counterfactual"
    module_dir.mkdir()
    (module_dir / "counterfactual_target.py").write_text("VALUE = 7\n")

    proc = run_python(CHANGED_LOADER, str(module_dir))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


FAILED_REPLAY = r"""
import json
import os
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_file_location

import metapathology

class DirectFinder:
    def __init__(self, source):
        self.source = source
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "failed_replay_target":
            return spec_from_file_location(
                fullname,
                self.source,
                loader=SourceFileLoader(fullname, self.source),
            )
        return None

def failing_hook(path):
    if os.path.normcase(os.path.abspath(path)) == normalized_module_dir:
        raise RuntimeError("live replay failed")
    raise ImportError

module_dir, source = sys.argv[1:]
normalized_module_dir = os.path.normcase(os.path.abspath(module_dir))
sys.path.insert(0, module_dir)
finder = DirectFinder(source)
monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, finder)
import failed_replay_target

sys.path_hooks.insert(0, failing_hook)
sys.path_importer_cache.pop(module_dir, None)
document = json.loads(metapathology.render_report(format="json"))
assert {
    (error["where"], error["exception_type_name"])
    for error in document["diagnostics"]["report_errors"]
} >= {("path_finder_replay", "RuntimeError")}

metapathology.uninstall()
assert type(sys.meta_path) is list
assert type(sys.path_hooks) is list
assert "find_spec" not in finder.__dict__
assert not monitor.enabled
print("OK")
"""


def test_live_replay_failure_is_reported_without_blocking_cleanup(
    run_python: RunPython,
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "failed_replay"
    module_dir.mkdir()
    source = module_dir / "failed_replay_target.py"
    source.write_text("VALUE = 7\n")

    proc = run_python(FAILED_REPLAY, str(module_dir), str(source))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
