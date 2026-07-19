"""Show that Bifrost's front insertion can shadow frozen stdlib modules."""

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from types import ModuleType

MODULE_NAME = "__hello__"
VIRTUAL_ORIGIN = "workspace/__hello__.py"


class VirtualLoader(importlib.abc.Loader):
    """Minimal equivalent of Bifrost's source-executing virtual loader."""

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        """Use default module creation."""
        return None

    def exec_module(self, module: ModuleType) -> None:
        """Mark the module as workspace-owned."""
        module.ORIGIN = "workspace"  # type: ignore[attr-defined]


class VirtualModuleFinder(importlib.abc.MetaPathFinder):
    """Reduce Bifrost's index-gated finder to the relevant decision."""

    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        """Claim the indexed workspace module."""
        del path, target
        if fullname == MODULE_NAME:
            return importlib.util.spec_from_loader(
                fullname,
                VirtualLoader(),
                origin=VIRTUAL_ORIGIN,
            )
        return None


def install_at_front(finder: VirtualModuleFinder) -> None:
    """Match Bifrost PR #419's current installation policy."""
    sys.meta_path.insert(0, finder)


def main() -> None:
    """Run the front-insertion reproduction."""
    finder = VirtualModuleFinder()
    frozen_spec = importlib.machinery.FrozenImporter.find_spec(MODULE_NAME)
    assert frozen_spec is not None, f"{MODULE_NAME} must be frozen on CPython"

    install_at_front(finder)
    try:
        module = __import__(MODULE_NAME)
    finally:
        sys.meta_path.remove(finder)

    print(f"expected standard origin: {frozen_spec.origin}")
    print(f"observed origin: {module.__spec__.origin}")
    print(f"observed implementation: {module.ORIGIN}")


if __name__ == "__main__":
    main()
