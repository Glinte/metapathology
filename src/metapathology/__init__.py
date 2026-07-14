"""metapathology: sys.meta_path diagnostics.

Find out who is messing with Python's import machinery: which finder claimed
each import, who mutated ``sys.meta_path`` (with stack traces), and which
modules were loaded in ways that bypass ``sys.path_hooks``.

Preferred usage is the CLI: ``python -m metapathology <script.py>`` or
``python -m metapathology -m <module>``. The library API below exists for
cases where a wrapper is impossible (notebooks, conftest.py-only access).
"""

from metapathology._monitor import Monitor, get_monitor, install, render_report, report, uninstall
from metapathology._records import (
    FindSpecCall,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
)

__version__ = "0.1.0"

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
