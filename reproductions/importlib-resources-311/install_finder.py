import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

_SENTINEL = "__editable__.sample_namespace-1.0.finder.__path_hook__"


class _EditableNamespaceFinder:
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname != "sample_namespace":
            return None

        spec = ModuleSpec(fullname, loader=None, is_package=True)
        spec.submodule_search_locations = [str(Path(__file__).parent), _SENTINEL]
        return spec


sys.meta_path.insert(0, _EditableNamespaceFinder())
