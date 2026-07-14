"""Report content: bypass detection and the no-spec bucket."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]

BYPASS = """
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
