"""Resolution-route probe evidence and failure isolation."""

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
routes = [item for item in document["resolution_routes"] if item["module"] == "counterfactual_target"]
captured = next(item for item in routes if item["kind"] == "captured_claim")
probe = next(item for item in routes if item["kind"] == "standard_path_probe")
assert probe["evidence_level"] == "live_probe"
assert probe["state_phase"] == "report"
assert probe["spec"]["loader"]["type_name"] == "CurrentLoader"
assert probe["predicts_alternative_winner"] is False
assert "skips_intervening_meta_path_finders" in probe["limitations"]
route_comparison = next(
    item
    for item in document["route_comparisons"]
    if item["left_route_ref"] == captured["id"] and item["right_route_ref"] == probe["id"]
)
assert route_comparison["loader_type_differs"] is True
comparison = route_comparison["structural_comparison"]
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
assert captured["signals"] == ["meta_path_short_circuit"]
assert not any(item["module"] == "counterfactual_target" for item in document["findings"])

text = metapathology.render_report()
assert "since install: sys.path_hooks changed" in text, text
assert "standard search at report time: PathFinder, loader CurrentLoader" in text, text
assert "[loader-displacement]" not in text, text
assert "does not show which finder would have won" in text, text
print("OK")
"""


def test_hook_reorder_and_cache_clear_change_live_probe_loader(
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
} >= {("standard_path_probe", "RuntimeError")}
probe = next(
    item
    for item in document["resolution_routes"]
    if item["module"] == "failed_replay_target" and item["kind"] == "standard_path_probe"
)
assert probe["status"] == "failed"
assert probe["exception_type_name"] == "RuntimeError"

metapathology.uninstall()
assert type(sys.meta_path) is list
assert type(sys.path_hooks) is list
assert "find_spec" not in finder.__dict__
assert not monitor.enabled
print("OK")
"""


def test_live_probe_failure_is_reported_without_blocking_cleanup(
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


REPORT_PROBE_ISOLATION = r"""
import json
import sys
from importlib.machinery import PathFinder

import metapathology
from metapathology import DeepDiagnosticCall

class DelegatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        return PathFinder.find_spec(fullname, path, target)

module_dir = sys.argv[1]
sys.path.insert(0, module_dir)
monitor = metapathology.install(
    report_at_exit=False,
    deep_path_hooks=True,
    deep_path_entry_finders=True,
)
sys.meta_path.insert(0, DelegatingFinder())
import isolated_probe_target

before = sum(isinstance(event, DeepDiagnosticCall) for event in monitor.events())
document = json.loads(metapathology.render_report(format="json"))
after = sum(isinstance(event, DeepDiagnosticCall) for event in monitor.events())
assert after == before
routes_by_id = {item["id"]: item for item in document["resolution_routes"]}
comparison = next(
    item
    for item in document["route_comparisons"]
    if routes_by_id[item["left_route_ref"]]["module"] == "isolated_probe_target"
)
assert comparison["structural_comparison"]["path_hooks"]["changed"] is False
print("OK")
"""


def test_standard_path_probe_does_not_record_deep_evidence_or_wrapper_mutation(
    run_python: RunPython, tmp_path: Path
) -> None:
    (tmp_path / "isolated_probe_target.py").write_text("VALUE = 7\n", encoding="utf-8")

    proc = run_python(REPORT_PROBE_ISOLATION, str(tmp_path))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


RELOAD_TARGET = r"""
import importlib
import json
import os
import sys
from importlib.machinery import PathFinder, SourceFileLoader
from importlib.util import spec_from_file_location

import metapathology

class InitialLoader(SourceFileLoader):
    pass

class ReloadLoader(SourceFileLoader):
    pass

class TargetSensitivePathFinder:
    def find_spec(self, fullname, target=None):
        if fullname != "reload_probe_target":
            return None
        loader_type = ReloadLoader if target is not None else InitialLoader
        return spec_from_file_location(fullname, source, loader=loader_type(fullname, source))

def hook(path):
    if os.path.normcase(os.path.abspath(path)) == normalized_module_dir:
        return TargetSensitivePathFinder()
    raise ImportError

class DelegatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        return PathFinder.find_spec(fullname, path, target)

module_dir, source = sys.argv[1:]
normalized_module_dir = os.path.normcase(os.path.abspath(module_dir))
sys.path.insert(0, module_dir)
sys.path_hooks.insert(0, hook)
sys.path_importer_cache.pop(module_dir, None)
metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DelegatingFinder())
import reload_probe_target
assert type(reload_probe_target.__loader__).__name__ == "InitialLoader"
importlib.reload(reload_probe_target)
assert type(reload_probe_target.__loader__).__name__ == "ReloadLoader"

document = json.loads(metapathology.render_report(format="json"))
probe = next(
    item
    for item in document["resolution_routes"]
    if item["module"] == "reload_probe_target" and item["kind"] == "standard_path_probe"
)
assert probe["status"] == "found"
assert probe["spec"]["loader"]["type_name"] == "ReloadLoader"
claim = next(
    event
    for event in reversed(document["timeline"])
    if event["kind"] == "find_spec_call"
    and event["data"]["fullname"] == "reload_probe_target"
    and event["data"]["found"]
)
assert claim["data"]["target_state"]["object_id"] == hex(id(reload_probe_target))
print("OK")
"""


def test_standard_path_probe_preserves_an_exact_reload_target(run_python: RunPython, tmp_path: Path) -> None:
    source = tmp_path / "reload_probe_target.py"
    source.write_text("VALUE = 7\n", encoding="utf-8")

    proc = run_python(RELOAD_TARGET, str(tmp_path), str(source))

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
