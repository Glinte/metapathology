"""Target application that fails only when frozen by PyInstaller.

Importing ``key_value.aio`` runs ``py-key-value-aio``'s package initializer,
which calls ``beartype.claw.beartype_this_package()``. That prepends a beartype
``FileFinder`` to ``sys.path_hooks`` and clears ``sys.path_importer_cache``.

Run normally the program succeeds: real ``.py`` files back every module, so the
beartype path hook resolves them. Frozen, the modules live only in PyInstaller's
PYZ archive; the beartype hook shadows PyInstaller's ``PyiFrozenFinder`` on the
``_MEIPASS`` path entries, so the next archive-only import fails with
``ModuleNotFoundError``.
"""

import key_value.aio  # noqa: F401  (side effect: installs beartype's path hook)

# The DiskStore imports its optional backends (diskcache, pathvalidate). Frozen,
# the first archive-only backend import after beartype's hook fails, which the
# store re-raises as "DiskStore requires py-key-value-aio[disk]".
import key_value.aio.stores.disk  # noqa: F401

print("imported key_value.aio.stores.disk with no import failure")
