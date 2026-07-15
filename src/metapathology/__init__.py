"""metapathology: sys.meta_path diagnostics.

Reports which finder located each imported module, where ``sys.meta_path`` was
changed (with stack traces), and which modules were loaded without the normal
``sys.path_hooks`` search.

Preferred usage is the CLI: ``python -m metapathology <script.py>`` or
``python -m metapathology -m <module>``. The library API below exists for
cases where a wrapper is impossible (notebooks, conftest.py-only access).

Documentation: https://glinte.github.io/metapathology/
"""

from metapathology._monitor import Monitor, get_monitor, install, render_report, report, uninstall
from metapathology._records import (
    FindSpecCall,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
)

# Keep this in sync with ``project.version`` in pyproject.toml. The package
# test enforces that invariant without making every CLI invocation import the
# comparatively expensive ``importlib.metadata`` module.
__version__ = "0.2.2"

__all__ = [
    "FindSpecCall",
    "InternalError",
    "MetaPathMutation",
    "MetaPathReassignment",
    "Monitor",
    "MonitorEvent",
    "__version__",
    "get_monitor",
    "install",
    "render_report",
    "report",
    "uninstall",
]
