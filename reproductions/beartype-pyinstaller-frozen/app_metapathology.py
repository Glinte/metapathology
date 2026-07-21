"""Frozen target under metapathology, writing a report even when the import fails.

``metapathology.install()`` runs first so the beartype path-hook mutation and the
importer-cache clear are observed. ``report_at_exit`` is off because PyInstaller's
bootloader hard-exits on an unhandled exception (``atexit`` never runs); the report
is written explicitly from ``finally`` instead, next to the executable.

Deep diagnostics are left off because they are not needed to attribute this
failure: the path-hook mutation and importer-cache clear are captured without
them.
"""

import os
import sys

import metapathology

metapathology.install(
    report_at_exit=False,
    monitor_path_hooks=True,
    monitor_importer_cache=True,
)

report_path = os.path.join(os.path.dirname(sys.executable), "mp_report.txt")
try:
    import key_value.aio  # noqa: F401  (installs beartype's path hook)
    import key_value.aio.stores.disk  # noqa: F401  (archive-only import fails)
    print("imported key_value.aio.stores.disk with no import failure")
finally:
    metapathology.write_report(report_path, format="text", color="never")
    print(f"metapathology report written to {report_path}")
