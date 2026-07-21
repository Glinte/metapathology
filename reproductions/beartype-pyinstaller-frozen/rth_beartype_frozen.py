"""PyInstaller runtime hook demonstrating the fix (control build).

Loaded before user code, this neutralizes ``beartype.claw``'s
``add_beartype_pathhook`` when frozen, so beartype never prepends its
source-expecting ``FileFinder`` to ``sys.path_hooks`` nor clears
``sys.path_importer_cache``. PyInstaller's ``PyiFrozenFinder`` keeps precedence
and archive-only imports resolve normally.

Two bindings must be patched: ``clawpkgmain`` does
``from ... import add_beartype_pathhook``, so it holds its own reference to the
original. This mirrors the fix in the upstream reproduction and exists only to
prove the diagnosis, not as a recommendation for how third parties should ship.
"""

import sys


def _frozen() -> bool:
    return getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")


def _disable_beartype_pathhook() -> None:
    if not _frozen():
        return
    try:
        from beartype.claw._importlib import clawimpmain
        from beartype.claw._package import clawpkgmain
    except ImportError:
        return

    def _noop() -> None:
        """Skip installing beartype's path hook inside the frozen bundle."""

    clawimpmain.add_beartype_pathhook = _noop
    clawpkgmain.add_beartype_pathhook = _noop


_disable_beartype_pathhook()
