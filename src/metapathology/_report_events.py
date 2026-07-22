"""Single source of truth for per-event-type projections.

Both renderers dispatch on a ``MonitorEvent``'s concrete type. Keeping the JSON
``kind`` string and the JSON count bucket for each event type in one table here
prevents the two renderers (and the count aggregation) from drifting apart. This
module is a leaf: it imports only :mod:`metapathology._records`.
"""

from metapathology._records import (
    DeepDiagnosticCall,
    DeepImportEvent,
    FindSpecCall,
    ImportAuditStart,
    ImportCall,
    ImporterCacheDiff,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
    PathHooksMutation,
    PathHooksReassignment,
    StandardFinderCall,
    SysPathMutation,
    SysPathReassignment,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    EventKind = Literal[
        "import_audit_start",
        "deep_diagnostic_call",
        "deep_import_event",
        "import_call",
        "standard_finder_call",
        "find_spec_call",
        "importer_cache_diff",
        "meta_path_mutation",
        "meta_path_reassignment",
        "path_hooks_mutation",
        "path_hooks_reassignment",
        "sys_path_mutation",
        "sys_path_reassignment",
        "internal_error",
    ]
    EventCountKind = Literal[
        "calls",
        "audit_starts",
        "deep_calls",
        "deep_import_events",
        "import_calls",
        "standard_finder_calls",
        "mutations",
        "reassignments",
        "path_hook_mutations",
        "path_hook_reassignments",
        "sys_path_mutations",
        "sys_path_reassignments",
        "importer_cache_diffs",
    ]

# Stable JSON ``kind`` discriminator per event type. Part of the public JSON
# schema; do not rename values without a schema bump.
EVENT_KIND: "dict[type[MonitorEvent], EventKind]" = {
    ImportAuditStart: "import_audit_start",
    DeepDiagnosticCall: "deep_diagnostic_call",
    DeepImportEvent: "deep_import_event",
    ImportCall: "import_call",
    StandardFinderCall: "standard_finder_call",
    FindSpecCall: "find_spec_call",
    ImporterCacheDiff: "importer_cache_diff",
    MetaPathMutation: "meta_path_mutation",
    MetaPathReassignment: "meta_path_reassignment",
    PathHooksMutation: "path_hooks_mutation",
    PathHooksReassignment: "path_hooks_reassignment",
    SysPathMutation: "sys_path_mutation",
    SysPathReassignment: "sys_path_reassignment",
    InternalError: "internal_error",
}

# JSON summary count bucket per event type. ``InternalError`` is intentionally
# absent: it is surfaced as ``internal_error_refs`` rather than a count.
EVENT_COUNT_KEY: "dict[type[MonitorEvent], EventCountKind]" = {
    FindSpecCall: "calls",
    ImportAuditStart: "audit_starts",
    DeepDiagnosticCall: "deep_calls",
    DeepImportEvent: "deep_import_events",
    ImportCall: "import_calls",
    StandardFinderCall: "standard_finder_calls",
    MetaPathMutation: "mutations",
    MetaPathReassignment: "reassignments",
    PathHooksMutation: "path_hook_mutations",
    PathHooksReassignment: "path_hook_reassignments",
    SysPathMutation: "sys_path_mutations",
    SysPathReassignment: "sys_path_reassignments",
    ImporterCacheDiff: "importer_cache_diffs",
}
