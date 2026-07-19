"""Reproduce the module-versus-namespace collision from rules_python#2009."""

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()

# The intended runfiles namespace is earlier. rules_python's coverage patch moves
# the directly executed coverage package directory to the end, but leaves it on
# sys.path. A later regular module still beats an earlier PEP 420 namespace.
sys.path.insert(0, str(ROOT / "runfiles_root"))
sys.path.append(str(ROOT / "coverage"))

runfiles_module = importlib.import_module("python.runfiles")
print(runfiles_module.runfiles.Create())
