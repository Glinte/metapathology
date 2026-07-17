"""Human report content: bypass detection and the no-spec bucket."""

import subprocess
import zipfile
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
routes = [item for item in document["resolution_routes"] if item["module"] == target_name]
captured = next(item for item in routes if item["kind"] == "captured_claim")
probe = next(item for item in routes if item["kind"] == "standard_path_probe")
assert probe["evidence_level"] == "live_probe", probe
assert probe["state_phase"] == "report", probe
assert probe["predicts_alternative_winner"] is False, probe
assert "meta_path_short_circuit" in captured["signals"], captured
assert not any(item["module"] == target_name for item in document["findings"])
print(text)
"""


def test_finder_shadowing_path_hooks_reports_neutral_route_difference(run_python: RunPython, tmp_path: Path) -> None:
    # The module file is in cwd, so the standard path machinery *would* find it
    # with a plain SourceFileLoader; the sneaky finder claims it first.
    module_file = tmp_path / "real_mod.py"
    module_file.write_text("VALUE = 7\n")
    proc = run_python(BYPASS, "real_mod", str(module_file))
    assert proc.returncode == 0, proc.stderr
    assert "[loader-displacement]" not in proc.stdout
    assert "-- resolution route divergences" in proc.stdout
    assert "captured route signals: meta path short circuit" in proc.stdout
    assert "intervening meta-path finders skipped" in proc.stdout
    assert "real_mod" in proc.stdout
    assert "SneakyLoader" in proc.stdout
    assert "SourceFileLoader" in proc.stdout
    # Findings lead the report, and paths under the reported cwd are relativized.
    assert proc.stdout.index("-- findings") < proc.stdout.index("-- chronological evidence timeline")
    assert "origin 'real_mod.py'" in proc.stdout
    assert f"paths shown relative to: {tmp_path}" in proc.stdout


def test_source_and_archive_routes_are_compared_without_classifying_a_defect(
    run_python: RunPython, tmp_path: Path
) -> None:
    archive = tmp_path / "frozen-like.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("archive_conflict.py", "VALUE = 'archive'\n")
    custom = tmp_path / "custom" / "archive_conflict.py"
    custom.parent.mkdir()
    custom.write_text("VALUE = 7\n", encoding="utf-8")
    proc = run_python(
        "import json, sys\n"
        "from importlib.machinery import SourceFileLoader\n"
        "from importlib.util import spec_from_file_location\n"
        "import metapathology\n"
        "class Finder:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'archive_conflict':\n"
        "            return spec_from_file_location(fullname, sys.argv[2], loader=SourceFileLoader(fullname, sys.argv[2]))\n"
        "        return None\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "metapathology.install(report_at_exit=False)\n"
        "sys.meta_path.insert(0, Finder())\n"
        "import archive_conflict\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "routes = [item for item in document['resolution_routes'] if item['module'] == 'archive_conflict']\n"
        "probe = next(item for item in routes if item['kind'] == 'standard_path_probe')\n"
        "assert probe['spec']['loader']['type_name'] == 'zipimporter'\n"
        "assert not any(item['module'] == 'archive_conflict' for item in document['findings'])\n"
        "assert '[frozen-source-conflict]' not in metapathology.render_report()\n"
        "print('OK')\n",
        str(archive),
        str(custom),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_module_invisible_to_path_machinery_has_a_not_found_standard_route(
    run_python: RunPython, tmp_path: Path
) -> None:
    # The module file lives in a directory that is not on sys.path, so the
    # standard path machinery cannot find it at all.
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    module_file = hidden / "hidden_mod.py"
    module_file.write_text("VALUE = 7\n")
    proc = run_python(BYPASS, "hidden_mod", str(module_file))
    assert proc.returncode == 0, proc.stderr
    assert "[unfindable]" not in proc.stdout
    assert "PathFinder status not_found" in proc.stdout
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
    assert "Custom claims may be compared with an independent standard path probe below" in proc.stdout
    assert "BuiltinImporter: class entry" not in proc.stdout
    assert "FrozenImporter: class entry" not in proc.stdout
    assert "PathFinder: class entry" not in proc.stdout
    assert "monitoring: sys.meta_path, sys.path_hooks, sys.path_importer_cache" in proc.stdout
    assert "low-level loader identity transitions unobservable" in proc.stdout
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
assert "[loader-displacement] 'transient_path_mod'" not in text, text
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
assert "[loader-displacement] 'cwd_sensitive_mod'" not in text, text
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
