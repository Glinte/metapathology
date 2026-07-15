import importlib
import sys
from importlib.util import spec_from_file_location
from pathlib import Path

root = Path(__file__).parent
editable_package = root / "editable-source" / "distributed" / "__init__.py"


class _EditableFinder:
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname == "distributed":
            return spec_from_file_location(
                fullname,
                editable_package,
                submodule_search_locations=[str(editable_package.parent)],
            )
        return None


# Setuptools' lenient PEP 660 finder was appended after PathFinder. The cwd
# contains an empty distributed/ directory, so PathFinder returns a namespace
# package spec and resolution stops before this finder is asked.
sys.meta_path.append(_EditableFinder())
module = importlib.import_module("distributed")
print(f"module spec origin: {module.__spec__.origin}")
print(f"module file: {getattr(module, '__file__', None)}")
print(f"editable marker: {getattr(module, 'VERSION', None)}")
