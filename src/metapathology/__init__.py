"""metapathology: sys.meta_path diagnostics.

Reports which finder located each imported module, where ``sys.meta_path`` was
changed (with stack traces), and which modules were loaded without the normal
``sys.path_hooks`` search.

Preferred usage is the CLI: ``python -m metapathology <script.py>`` or
``python -m metapathology -m <module>``. The library API below exists for
cases where a wrapper is impossible (notebooks, conftest.py-only access).

Documentation: https://glinte.github.io/metapathology/
"""

# Static analyzers treat this conventional name as true; runtime skips the
# imports until a public attribute is actually requested.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._config import AnalysisConfig, CaptureConfig, DetailedCaptureConfig
    from metapathology._frozen_bootstrap import activate_frozen
    from metapathology._monitor import Monitor
    from metapathology._records import (
        ImportCall,
        ImporterCacheChange,
        ImporterCacheEntry,
        ImporterCacheReplacement,
        ImportMechanismCall,
        ImportResult,
        ImportSearchStarted,
        MetaPathChange,
        MetaPathFinderCall,
        MetaPathReplacement,
        ModuleCacheState,
        ModuleSpecSnapshot,
        MonitorEvent,
        MonitoringError,
        ObjectIdentity,
        PathFinderCall,
        PathHooksChange,
        PathHooksReplacement,
        SysPathChange,
        SysPathReplacement,
    )
    from metapathology._report_schema import ReportJSON, ReportStatus
    from metapathology._runtime import get_monitor, install, monitoring, render_report, uninstall, write_report

# Keep this in sync with ``project.version`` in pyproject.toml. The package
# test enforces that invariant without making every CLI invocation import the
# comparatively expensive ``importlib.metadata`` module.
__version__ = "0.5.0"

_MONITOR_EXPORTS = frozenset(("Monitor",))
_CONFIG_EXPORTS = frozenset(("AnalysisConfig", "CaptureConfig", "DetailedCaptureConfig"))
_RUNTIME_EXPORTS = frozenset(("get_monitor", "install", "monitoring", "render_report", "uninstall", "write_report"))
_FROZEN_EXPORTS = frozenset(("activate_frozen",))
_RECORD_EXPORTS = frozenset(
    (
        "MetaPathFinderCall",
        "ImportMechanismCall",
        "ImportResult",
        "ImportSearchStarted",
        "ImportCall",
        "ImporterCacheChange",
        "ImporterCacheEntry",
        "ImporterCacheReplacement",
        "MonitoringError",
        "MetaPathChange",
        "MetaPathReplacement",
        "ModuleCacheState",
        "MonitorEvent",
        "ObjectIdentity",
        "PathHooksChange",
        "PathHooksReplacement",
        "ModuleSpecSnapshot",
        "PathFinderCall",
        "SysPathChange",
        "SysPathReplacement",
    )
)
_SCHEMA_EXPORTS = frozenset(("ReportJSON", "ReportStatus"))

__all__ = [
    "AnalysisConfig",
    "CaptureConfig",
    "DetailedCaptureConfig",
    "ImportCall",
    "ImportMechanismCall",
    "ImportResult",
    "ImportSearchStarted",
    "ImporterCacheChange",
    "ImporterCacheEntry",
    "ImporterCacheReplacement",
    "MetaPathChange",
    "MetaPathFinderCall",
    "MetaPathReplacement",
    "ModuleCacheState",
    "ModuleSpecSnapshot",
    "Monitor",
    "MonitorEvent",
    "MonitoringError",
    "ObjectIdentity",
    "PathFinderCall",
    "PathHooksChange",
    "PathHooksReplacement",
    "ReportJSON",
    "ReportStatus",
    "SysPathChange",
    "SysPathReplacement",
    "__version__",
    "activate_frozen",
    "get_monitor",
    "install",
    "monitoring",
    "render_report",
    "uninstall",
    "write_report",
]


def __getattr__(name: str) -> object:
    """Load public monitoring APIs only when first accessed."""
    if name in _MONITOR_EXPORTS:
        import metapathology._monitor as module
    elif name in _CONFIG_EXPORTS:
        import metapathology._config as module
    elif name in _RUNTIME_EXPORTS:
        import metapathology._runtime as module
    elif name in _FROZEN_EXPORTS:
        import metapathology._frozen_bootstrap as module
    elif name in _RECORD_EXPORTS:
        import metapathology._records as module
    elif name in _SCHEMA_EXPORTS:
        import metapathology._report_schema as module
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include deferred public APIs in module introspection."""
    return sorted(set(globals()) | set(__all__))
