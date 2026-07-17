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
    from metapathology._frozen_bootstrap import activate_frozen
    from metapathology._monitor import Monitor, get_monitor, install, render_report, uninstall, write_report
    from metapathology._records import (
        DeepDiagnosticCall,
        DeepImportEvent,
        FindSpecCall,
        ImportAuditStart,
        ImporterCacheDiff,
        ImporterCacheEntry,
        ImporterCacheReplacement,
        ImportObjectRef,
        InternalError,
        MetaPathMutation,
        MetaPathReassignment,
        ModuleCacheState,
        MonitorEvent,
        PathHooksMutation,
        PathHooksReassignment,
        SpecSummary,
        StandardFinderCall,
    )
    from metapathology._report_schema import ReportJSON, ReportStatus

# Keep this in sync with ``project.version`` in pyproject.toml. The package
# test enforces that invariant without making every CLI invocation import the
# comparatively expensive ``importlib.metadata`` module.
__version__ = "0.4.1"

_MONITOR_EXPORTS = frozenset(("Monitor", "get_monitor", "install", "render_report", "uninstall", "write_report"))
_FROZEN_EXPORTS = frozenset(("activate_frozen",))
_RECORD_EXPORTS = frozenset(
    (
        "FindSpecCall",
        "DeepDiagnosticCall",
        "DeepImportEvent",
        "ImportAuditStart",
        "ImportObjectRef",
        "ImporterCacheDiff",
        "ImporterCacheEntry",
        "ImporterCacheReplacement",
        "InternalError",
        "MetaPathMutation",
        "MetaPathReassignment",
        "ModuleCacheState",
        "MonitorEvent",
        "PathHooksMutation",
        "PathHooksReassignment",
        "SpecSummary",
        "StandardFinderCall",
    )
)
_SCHEMA_EXPORTS = frozenset(("ReportJSON", "ReportStatus"))

__all__ = [
    "DeepDiagnosticCall",
    "DeepImportEvent",
    "FindSpecCall",
    "ImportAuditStart",
    "ImportObjectRef",
    "ImporterCacheDiff",
    "ImporterCacheEntry",
    "ImporterCacheReplacement",
    "InternalError",
    "MetaPathMutation",
    "MetaPathReassignment",
    "ModuleCacheState",
    "Monitor",
    "MonitorEvent",
    "PathHooksMutation",
    "PathHooksReassignment",
    "ReportJSON",
    "ReportStatus",
    "SpecSummary",
    "StandardFinderCall",
    "__version__",
    "activate_frozen",
    "get_monitor",
    "install",
    "render_report",
    "uninstall",
    "write_report",
]


def __getattr__(name: str) -> object:
    """Load public monitoring APIs only when first accessed."""
    if name in _MONITOR_EXPORTS:
        import metapathology._monitor as module
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
