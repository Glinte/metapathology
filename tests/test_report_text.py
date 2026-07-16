"""Human report content: bypass detection and the no-spec bucket."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

BYPASS = """
import json
import sys
from importlib.machinery import SourceFileLoader
from importlib.util import spec_from_file_location

import metapathology

class SneakyLoader(SourceFileLoader):
    pass

class SneakyFinder:
    def __init__(self, name, path):
        self.name = name
        self.path = path
    def find_spec(self, fullname, path=None, target=None):
        if fullname == self.name:
            return spec_from_file_location(fullname, self.path, loader=SneakyLoader(fullname, self.path))
        return None

target_name, target_file = sys.argv[1], sys.argv[2]
monitor = metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, SneakyFinder(target_name, target_file))

module = __import__(target_name)
assert module.VALUE == 7

text = metapathology.render_report()
document = json.loads(metapathology.render_report(format="json"))
finding = next(item for item in document["findings"] if item["module"] == target_name)
assert finding["path_finder_replay"]["evidence_level"] == "live_replay", finding
assert finding["path_finder_replay"]["state_phase"] == "report", finding
print(text)
"""


def test_finder_shadowing_path_hooks_is_flagged_as_bypass(run_python: RunPython, tmp_path: Path) -> None:
    # The module file is in cwd, so the standard path machinery *would* find it
    # with a plain SourceFileLoader; the sneaky finder claims it first.
    module_file = tmp_path / "real_mod.py"
    module_file.write_text("VALUE = 7\n")
    proc = run_python(BYPASS, "real_mod", str(module_file))
    assert proc.returncode == 0, proc.stderr
    assert "[bypass]" in proc.stdout
    assert "real_mod" in proc.stdout
    assert "SneakyLoader" in proc.stdout
    assert "SourceFileLoader" in proc.stdout
    # Findings lead the report, and paths under the reported cwd are relativized.
    assert proc.stdout.index("-- suspicious findings") < proc.stdout.index("-- chronological evidence timeline")
    assert "origin 'real_mod.py'" in proc.stdout
    assert f"paths shown relative to: {tmp_path}" in proc.stdout


def test_module_invisible_to_path_machinery_is_flagged_as_unfindable(run_python: RunPython, tmp_path: Path) -> None:
    # The module file lives in a directory that is not on sys.path, so the
    # standard path machinery cannot find it at all.
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    module_file = hidden / "hidden_mod.py"
    module_file.write_text("VALUE = 7\n")
    proc = run_python(BYPASS, "hidden_mod", str(module_file))
    assert proc.returncode == 0, proc.stderr
    assert "[unfindable]" in proc.stdout
    assert "hidden_mod" in proc.stdout


NO_SPEC = """
import sys
import types

import metapathology

metapathology.install(report_at_exit=False)
sys.modules["ghost_mod"] = types.ModuleType("ghost_mod")

text = metapathology.render_report()
assert "[no-spec]" in text, text
assert "ghost_mod" in text, text
print("OK")
"""


def test_manually_registered_module_is_flagged_as_no_spec(run_python: RunPython) -> None:
    proc = run_python(NO_SPEC)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


DEFAULT_FINDERS = """
import metapathology

metapathology.install(report_at_exit=False)
print(metapathology.render_report())
"""


def test_standard_unwrapped_finders_are_explained(run_python: RunPython) -> None:
    proc = run_python(DEFAULT_FINDERS)
    assert proc.returncode == 0, proc.stderr
    assert "report guide: https://glinte.github.io/metapathology/report/" in proc.stdout
    assert "standard CPython finders left unwrapped (expected)" in proc.stdout
    assert "interpreter-shared classes handle built-in, frozen, and sys.path imports" in proc.stdout
    assert "Custom claims are checked against PathFinder below" in proc.stdout
    assert "BuiltinImporter: class entry" not in proc.stdout
    assert "FrozenImporter: class entry" not in proc.stdout
    assert "PathFinder: class entry" not in proc.stdout
    assert "monitoring: sys.meta_path, sys.path_hooks, sys.path_importer_cache" in proc.stdout
    assert "sys.meta_path (unchanged since install):" in proc.stdout
    assert (
        "nothing recorded: sys.meta_path mutations, sys.meta_path reassignments, "
        "sys.path_hooks mutations, sys.path_hooks reassignments" in proc.stdout
    )
    assert "-- sys.path_hooks mutations (0) --" not in proc.stdout


PATH_REMOVED_AFTER_IMPORT = """
import sys
from importlib.machinery import PathFinder

import metapathology

class DelegatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        return PathFinder.find_spec(fullname, path, target)

module_dir = sys.argv[1]
sys.path.insert(0, module_dir)
metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DelegatingFinder())

import transient_path_mod
assert transient_path_mod.VALUE == 7
sys.path.remove(module_dir)

text = metapathology.render_report()
assert "[unfindable] 'transient_path_mod'" not in text, text
assert "[bypass] 'transient_path_mod'" not in text, text
print("OK")
"""


def test_path_removal_after_import_does_not_create_bypass_finding(run_python: RunPython, tmp_path: Path) -> None:
    module_dir = tmp_path / "temporary_path"
    module_dir.mkdir()
    (module_dir / "transient_path_mod.py").write_text("VALUE = 7\n")

    proc = run_python(PATH_REMOVED_AFTER_IMPORT, str(module_dir))
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


CWD_CHANGED_AFTER_IMPORT = """
import os
import sys
from importlib.machinery import PathFinder

import metapathology

class DelegatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        return PathFinder.find_spec(fullname, path, target)

new_cwd = sys.argv[1]
metapathology.install(report_at_exit=False)
sys.meta_path.insert(0, DelegatingFinder())

import cwd_sensitive_mod
assert cwd_sensitive_mod.VALUE == 7
os.chdir(new_cwd)

text = metapathology.render_report()
assert "[unfindable] 'cwd_sensitive_mod'" not in text, text
assert "[bypass] 'cwd_sensitive_mod'" not in text, text
print("OK")
"""


def test_cwd_change_after_import_does_not_create_false_bypass_finding(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "cwd_sensitive_mod.py").write_text("VALUE = 7\n")
    new_cwd = tmp_path / "elsewhere"
    new_cwd.mkdir()

    proc = run_python(CWD_CHANGED_AFTER_IMPORT, str(new_cwd))
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


HOSTILE_MODULE_SPEC = """
import sys

import metapathology

class HostileModule:
    touched = False
    def __getattribute__(self, name):
        type(self).touched = True
        raise SystemExit(94)
        return super().__getattribute__(name)

metapathology.install(report_at_exit=False)
hostile = HostileModule()
sys.modules["hostile_module"] = hostile
text = metapathology.render_report()
assert "metadata unavailable" in text, text
del sys.modules["hostile_module"]
assert not HostileModule.touched
print("OK")
"""


def test_report_does_not_dispatch_to_foreign_module_like_objects(run_python: RunPython) -> None:
    proc = run_python(HOSTILE_MODULE_SPEC)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
