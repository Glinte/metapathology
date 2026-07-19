from __future__ import annotations

import sys

import repro_pkg
from repro_pkg.probe import checked

print("finders:", [type(finder).__name__ for finder in sys.meta_path])
print("package path:", list(repro_pkg.__path__))
checked("not an int")
