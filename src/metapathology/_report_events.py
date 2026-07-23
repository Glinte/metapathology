"""Single source of truth for per-event-type projections.

Both renderers dispatch on a ``MonitorEvent``'s concrete type. Keeping the JSON
``kind`` string and the JSON count bucket for each event type in one table here
prevents the two renderers (and the count aggregation) from drifting apart. This
module is a leaf: it imports only :mod:`metapathology._records`.
"""

from metapathology._records import (
    ImportBranchExplorationCall,
    ImportBranchExplorationStarted,
    ImportCall,
    ImporterCacheChange,
    ImportMechanismCall,
    ImportResult,
    ImportSearchStarted,
    MetaPathChange,
    MetaPathFinderCall,
    MetaPathReplacement,
    MonitorEvent,
    MonitoringError,
    PathFinderCall,
    PathHooksChange,
    PathHooksReplacement,
    SysPathChange,
    SysPathReplacement,
)

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    EventKind = Literal[
        "import_search_started",
        "import_mechanism_call",
        "import_result",
        "import_call",
        "path_finder_call",
        "meta_path_finder_call",
        "importer_cache_change",
        "meta_path_change",
        "meta_path_replacement",
        "path_hooks_change",
        "path_hooks_replacement",
        "sys_path_change",
        "sys_path_replacement",
        "monitoring_error",
        "import_branch_exploration_started",
        "import_branch_exploration_call",
    ]
    EventCountKind = Literal[
        "calls",
        "audit_starts",
        "import_mechanism_calls",
        "import_results",
        "import_calls",
        "path_finder_calls",
        "meta_path_changes",
        "meta_path_replacements",
        "path_hooks_changes",
        "path_hooks_replacements",
        "sys_path_changes",
        "sys_path_replacements",
        "importer_cache_changes",
        "import_branch_exploration_starts",
        "import_branch_exploration_calls",
    ]

# Stable JSON ``kind`` discriminator per event type. Part of the public JSON
# schema; do not rename values without a schema bump.
EVENT_KIND: "dict[type[MonitorEvent], EventKind]" = {
    ImportBranchExplorationStarted: "import_branch_exploration_started",
    ImportBranchExplorationCall: "import_branch_exploration_call",
    ImportSearchStarted: "import_search_started",
    ImportMechanismCall: "import_mechanism_call",
    ImportResult: "import_result",
    ImportCall: "import_call",
    PathFinderCall: "path_finder_call",
    MetaPathFinderCall: "meta_path_finder_call",
    ImporterCacheChange: "importer_cache_change",
    MetaPathChange: "meta_path_change",
    MetaPathReplacement: "meta_path_replacement",
    PathHooksChange: "path_hooks_change",
    PathHooksReplacement: "path_hooks_replacement",
    SysPathChange: "sys_path_change",
    SysPathReplacement: "sys_path_replacement",
    MonitoringError: "monitoring_error",
}

# JSON summary count bucket per event type. ``MonitoringError`` is intentionally
# absent: it is surfaced as ``monitoring_error_refs`` rather than a count.
EVENT_COUNT_KEY: "dict[type[MonitorEvent], EventCountKind]" = {
    ImportBranchExplorationStarted: "import_branch_exploration_starts",
    ImportBranchExplorationCall: "import_branch_exploration_calls",
    MetaPathFinderCall: "calls",
    ImportSearchStarted: "audit_starts",
    ImportMechanismCall: "import_mechanism_calls",
    ImportResult: "import_results",
    ImportCall: "import_calls",
    PathFinderCall: "path_finder_calls",
    MetaPathChange: "meta_path_changes",
    MetaPathReplacement: "meta_path_replacements",
    PathHooksChange: "path_hooks_changes",
    PathHooksReplacement: "path_hooks_replacements",
    SysPathChange: "sys_path_changes",
    SysPathReplacement: "sys_path_replacements",
    ImporterCacheChange: "importer_cache_changes",
}
