"""Show the narrower placement that preserves standard finder precedence."""

import importlib.machinery
import sys

from reproduce import MODULE_NAME, VirtualModuleFinder

sys.modules.pop(MODULE_NAME, None)
finder = VirtualModuleFinder()
path_finder_index = sys.meta_path.index(importlib.machinery.PathFinder)
sys.meta_path.insert(path_finder_index, finder)
try:
    module = __import__(MODULE_NAME)
finally:
    sys.meta_path.remove(finder)

print(f"observed origin: {module.__spec__.origin}")
assert module.__spec__.origin == "frozen"
