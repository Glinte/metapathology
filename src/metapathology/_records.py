"""Plain event records captured by the monitor.

Records hold only primitive data (type names, ids, precomputed strings)
extracted at capture time. Foreign objects are never repr()'d while an import
may be in flight; all formatting happens at report time in ``_report``.

The field vocabularies (``MutationOp`` etc.) are imported only under
``TYPE_CHECKING``; annotations that reference them use forward-reference
strings so the module needs no runtime ``typing`` import.
"""

import types

from metapathology._record import _Record

TYPE_CHECKING = False

if TYPE_CHECKING:
    from traceback import StackSummary
    from typing import Literal
    from typing import cast as _cast

    # Closed vocabularies for enum-like record fields. Free-form captured
    # strings (type names, module names, paths, thread names) stay ``str``.
    MutationOp = Literal[
        "append",
        "insert",
        "extend",
        "remove",
        "pop",
        "clear",
        "reverse",
        "sort",
        "__setitem__",
        "__delitem__",
        "__iadd__",
        "__imul__",
    ]
    CacheState = Literal["unavailable", "missing", "none", "object"]
    ProtocolAvailability = Literal["callable", "non_callable", "indeterminate", "absent"]
    LocationsState = Literal["not_applicable", "captured", "current_state", "deferred", "failed"]
    ImportMechanismBoundary = Literal[
        "path_entry_finder",
        "loader_create_module",
        "loader_exec_module",
        "path_hook",
    ]
    ImportMechanismOutcome = Literal[
        "started",
        "loaded",
        "failed",
        "found",
        "not_found",
        "returned",
        "raised",
        "unobserved_reentrant",
    ]
    SearchPathKind = Literal["sys_path", "parent_path", "path_entry"]
    FinderAPIObservationKind = Literal["install", "replacement", "change"]
    ImportBranchBoundary = Literal["meta_path", "path_hook", "path_entry"]
    ImportBranchTriggerOutcome = Literal["found", "returned", "raised"]
    ImportBranchExplorationOutcome = Literal[
        "found",
        "not_found",
        "returned_finder",
        "declined",
        "raised",
        "unsupported",
        "negative_cache",
    ]
else:

    def _cast(_type: object, value: object) -> object:
        return value


def type_name(obj: object) -> str:
    """Best-effort display name: ``__name__`` for class entries (e.g. ``PathFinder``), type name otherwise."""
    obj_type = type(obj)
    # ``isinstance(obj, type)`` may read a foreign object's spoofed
    # ``__class__`` attribute. Inspect the actual type hierarchy instead.
    cls = _cast("type[object]", obj) if issubclass(obj_type, type) else obj_type
    # Calling ``cls.__name__`` normally dispatches through a custom metaclass.
    # Finder names are captured inside imports, so bypass foreign overrides.
    return type.__getattribute__(cls, "__name__")


class ObjectIdentity(_Record):
    """Plain identity metadata for an observed foreign object."""

    object_id: int
    type_name: str
    name: str | None = None

    @classmethod
    def of(cls, obj: object) -> "ObjectIdentity":
        """Reduce an arbitrary object to safe identity metadata without foreign dispatch.

        Captures a function/method's ``__name__`` when available (path hooks are
        plain functions); other objects carry only their id and type name.

        Uses ``type(obj)`` rather than ``isinstance`` so a hostile object's
        spoofed ``__class__`` cannot trigger foreign dispatch during inspection.
        """
        name: str | None = None
        if type(obj) in (types.FunctionType, types.BuiltinFunctionType, types.MethodType):
            try:
                raw_name = object.__getattribute__(obj, "__name__")
            except (AttributeError, TypeError):
                pass
            else:
                if isinstance(raw_name, str):
                    name = raw_name
        return cls(object_id=id(obj), type_name=type_name(obj), name=name)


class FinderProtocol(_Record):
    """Conservative raw-dictionary evidence for one finder protocol."""

    availability: "ProtocolAvailability"
    evidence: str
    defined_by: str | None


class FinderAPIObservation(_Record):
    """Protocol inventory captured before metapathology changes a finder."""

    finder_id: int
    finder_type_name: str
    position: int
    observation: "FinderAPIObservationKind"
    observation_seq: int | None
    find_spec: FinderProtocol
    find_module: FinderProtocol


class ModuleCacheState(_Record):
    """Plain identity state for one name in a module cache."""

    state: "CacheState"
    object_id: int | None = None
    type_name: str | None = None

    def compare(self, other: "ModuleCacheState") -> 'Literal["same", "different", "unavailable"]':
        """Compare two captured cache states without inventing missing evidence."""
        if self.state == "unavailable" or other.state == "unavailable":
            return "unavailable"
        if self.state == other.state and self.object_id == other.object_id and self.type_name == other.type_name:
            return "same"
        return "different"


class ImporterCacheEntry(_Record):
    """One string-keyed ``sys.path_importer_cache`` entry.

    ``finder=None`` represents a negative cache entry, not an absent path.
    """

    path: str
    finder: ObjectIdentity | None


class ImporterCacheReplacement(_Record):
    """One cache path whose finder identity or negative status changed."""

    path: str
    before: ObjectIdentity | None
    after: ObjectIdentity | None


class ImporterCacheChange(_Record):
    """A batch of importer-cache changes observed at one observation point."""

    sequence: int
    observation: str
    added: tuple[ImporterCacheEntry, ...]
    removed: tuple[ImporterCacheEntry, ...]
    replaced: tuple[ImporterCacheReplacement, ...]
    non_string_keys_before: int
    non_string_keys_after: int
    thread_name: str


class ImportSearchStarted(_Record):
    """One CPython ``import`` audit event proving resolution started.

    The audit event has no matching completion signal. This record therefore
    does not claim that the import succeeded, failed, or selected a finder.
    Enabled auxiliary mechanisms contribute only constant-size identities and
    the importer-cache size; detailed list and cache changes remain separate
    events.
    """

    sequence: int
    search_id: int
    fullname: str
    meta_path_id: int
    meta_path_type_names: tuple[str, ...]
    path_hooks_id: int | None
    importer_cache_id: int | None
    importer_cache_size: int | None
    thread_name: str
    thread_id: int


class MetaPathChange(_Record):
    """A mutating method call observed on the instrumented ``sys.meta_path`` list.

    Attributes:
        sequence: Position in the monitor's single event log; one counter is shared
            by all record types so events can be interleaved chronologically.
        op: Name of the list method that mutated the list, e.g. ``"insert"``
            or ``"__setitem__"``.
        added: Display names (via :func:`type_name`) of finders the mutation
            added; empty for pure removals and order changes.
        removed: Display names of finders the mutation removed.
        contents_after: Display names of the whole list right after the
            mutation. This is the mutated list, which after a reassignment is
            not necessarily the live ``sys.meta_path``.
        thread_name: Name of the thread that performed the mutation.
        stack: Caller stack at mutation time, innermost frame first, captured
            without source lines (they are resolved at report time).
    """

    sequence: int
    op: "MutationOp"
    added: tuple[str, ...]
    removed: tuple[str, ...]
    contents_after: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class MetaPathReplacement(_Record):
    """``sys.meta_path`` was replaced wholesale, detected via the ``import`` audit event.

    Detection granularity is the next import after the fact, so every field
    describes the state *at detection time*, not at the moment of reassignment.

    Attributes:
        sequence: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathChange`).
        during_import: Name of the module whose import triggered detection.
            The reassignment happened some time before this import.
        old_contents: Display names of the abandoned instrumented list's
            entries at detection time; they may have drifted since the
            reassignment.
        new_contents: Display names of the entries of the list that replaced
            ours.
        thread_name: Name of the thread whose import triggered detection.
        stack: Stack of the triggering import, not of the reassigner (plain
            attribute assignment raises no event, so that stack is unknowable).
    """

    sequence: int
    during_import: str
    old_contents: tuple[str, ...]
    new_contents: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class PathHooksChange(_Record):
    """A mutating method call observed on the instrumented ``sys.path_hooks`` list."""

    sequence: int
    op: "MutationOp"
    added: tuple[ObjectIdentity, ...]
    removed: tuple[ObjectIdentity, ...]
    contents_after: tuple[ObjectIdentity, ...]
    thread_name: str
    stack: "StackSummary"


class PathHooksReplacement(_Record):
    """``sys.path_hooks`` replacement detected at the next import audit event."""

    sequence: int
    during_import: str
    old_contents: tuple[ObjectIdentity, ...]
    new_contents: tuple[ObjectIdentity, ...]
    thread_name: str
    stack: "StackSummary"


class SysPathChange(_Record):
    """A mutating method call observed on the opt-in instrumented ``sys.path`` list."""

    sequence: int
    op: "MutationOp"
    added: tuple[str, ...]
    removed: tuple[str, ...]
    contents_after: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class SysPathReplacement(_Record):
    """``sys.path`` replacement detected at the next import audit event."""

    sequence: int
    during_import: str
    old_contents: tuple[str, ...]
    new_contents: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class ModuleSpecSnapshot(_Record):
    """Import-safe semantic summary of a finder-produced module spec."""

    spec: ObjectIdentity
    loader: ObjectIdentity | None
    origin: str | ObjectIdentity | None
    cached: str | ObjectIdentity | None
    is_package: bool | None
    is_namespace: bool | None
    submodule_search_locations: tuple[str | ObjectIdentity, ...] | None
    locations_state: "LocationsState"
    unavailable_fields: tuple[str, ...] = ()


class MetaPathFinderCall(_Record):
    """One ``find_spec`` call on an instrumented meta-path finder.

    Attributes:
        sequence: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathChange`).
        fullname: The module name that was checked.
        finder_type_name: Display name of the finder that was asked.
        finder_id: ``id()`` of that finder; stable because the monitor keeps a
            strong reference, and needed to tell apart two finders of the same
            type.
        found: True when the finder claimed the module (returned a spec).
        loader_type_name: Display name of ``spec.loader``'s type, or None when
            there is no spec or no loader (e.g. namespace packages).
        origin: ``spec.origin`` when it is a string (a file path for
            filesystem imports), else None.
        search_path: Snapshot of the path passed to ``find_spec``, or of
            ``sys.path`` for a top-level import. Used by report-time checks
            without substituting a mutable report-time search path.
        target_state: Identity-only snapshot of the reload target, when one
            was passed to ``find_spec``. A report-time check only reuses the
            object if that exact identity remains available.
        exception_type_name: Type name of the exception if ``find_spec``
            raised instead of returning; ``found`` is False in that case.
        thread_name: Name of the thread that ran the import.
    """

    sequence: int
    fullname: str
    finder_type_name: str
    finder_id: int
    found: bool
    loader_type_name: str | None
    origin: str | None
    search_path: tuple[str, ...]
    search_path_kind: "SearchPathKind"
    spec_summary: ModuleSpecSnapshot | None
    exception_type_name: str | None
    thread_name: str
    thread_id: int
    module_state_before: ModuleCacheState | None = None
    module_state_after: ModuleCacheState | None = None
    target_state: ModuleCacheState | None = None


class ImportMechanismCall(_Record):
    """One call crossing an explicitly enabled detailed-diagnostics boundary.

    Attributes:
        returned_finder: Safe identity of the path-entry finder a successful
            ``path_hook`` call returned. Captured so the report can link an
            accepting hook to the finder it installed in
            ``sys.path_importer_cache`` — the ``hook H returned finder F for
            path P`` step of the cache-displacement provenance chain. ``None``
            for every other boundary and for hooks that raised or declined.
    """

    sequence: int
    boundary: "ImportMechanismBoundary"
    object_id: int
    object_type_name: str
    fullname: str | None
    path: str | None
    outcome: "ImportMechanismOutcome"
    exception_type_name: str | None
    thread_name: str
    thread_id: int
    module_state_before: ModuleCacheState | None = None
    module_state_after: ModuleCacheState | None = None
    target_state: ModuleCacheState | None = None
    returned_finder: ObjectIdentity | None = None


class ImportResult(_Record):
    """Entry or exact completion observed at CPython's complete import boundary."""

    sequence: int
    search_id: int
    fullname: str
    outcome: "ImportMechanismOutcome"
    thread_id: int
    thread_name: str


class ImportCall(_Record):
    """One observed ``builtins.__import__`` call, including cache hits.

    Unlike every other record, this one is captured even when the import is a
    ``sys.modules`` cache hit: the ``import`` audit event does not fire and no
    finder is called, so this is the only evidence that the code ran an import
    statement at all. There is no ``search_id`` because a cache hit has no
    corresponding resolution start to link to.

    Attributes:
        sequence: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathChange`).
        name: The module name passed to ``__import__`` (the first argument);
            empty for ``from . import x``-style relative imports.
        fromlist: Names in the import's ``fromlist`` that were plain strings.
        level: Relative-import level (0 for an absolute import).
        importing_module: ``__name__`` read from the caller's globals, i.e. the
            module that ran the import, or None when it could not be read.
        module_state_before: Whether ``name`` was already in ``sys.modules``
            when the call began; an ``"object"`` state on an absolute import is
            the signature of a cache hit.
        outcome: ``"returned"`` or ``"raised"``.
        exception_type_name: Type name of the exception if the call raised.
        thread_id: Monitor-assigned per-thread identity.
        thread_name: Name of the thread that ran the import.
    """

    sequence: int
    name: str
    fromlist: tuple[str, ...]
    level: int
    importing_module: str | None
    module_state_before: ModuleCacheState
    outcome: "ImportMechanismOutcome"
    exception_type_name: str | None
    thread_id: int
    thread_name: str


class PathFinderCall(_Record):
    """A captured aggregate call to a shared standard finder."""

    sequence: int
    search_id: int
    fullname: str
    finder_type_name: str
    spec_summary: ModuleSpecSnapshot
    thread_id: int
    thread_name: str


class ImportBranchExplorationStarted(_Record):
    """One actual terminal import branch whose skipped siblings will be invoked."""

    sequence: int
    boundary: "ImportBranchBoundary"
    fullname: str | None
    path: str | None
    trigger: ObjectIdentity
    trigger_index: int
    trigger_outcome: "ImportBranchTriggerOutcome"
    trigger_event_seq: int | None
    thread_id: int
    thread_name: str


class ImportBranchExplorationCall(_Record):
    """One unsafe call to a finder or hook skipped by the actual import route."""

    sequence: int
    exploration_seq: int
    boundary: "ImportBranchBoundary"
    fullname: str | None
    path: str | None
    candidate: ObjectIdentity
    candidate_index: int
    outcome: "ImportBranchExplorationOutcome"
    spec_summary: ModuleSpecSnapshot | None
    returned_finder: ObjectIdentity | None
    exception_type_name: str | None
    module_state_before: ModuleCacheState | None
    module_state_after: ModuleCacheState | None
    thread_id: int
    thread_name: str


class MonitoringError(_Record):
    """An exception raised inside metapathology's own instrumentation.

    Recorded instead of raised: an exception escaping a hook would abort the
    user's import, which a diagnostic tool must never do.

    Attributes:
        sequence: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathChange`).
        where: Short label of the failing code path, e.g. ``"audit_hook"``.
        exception_type_name: Type name of the caught exception.
        message: Optional pre-sanitized detail. The monitor leaves this unset
            because stringifying an exception supplied by foreign import
            machinery can execute arbitrary code while an import is in flight.
    """

    sequence: int
    where: str
    exception_type_name: str
    message: str | None = None


# Everything the monitor records goes into one chronological log; ``sequence`` orders records across types.
MonitorEvent = (
    ImportBranchExplorationCall
    | ImportBranchExplorationStarted
    | ImportMechanismCall
    | ImportResult
    | MetaPathFinderCall
    | ImportSearchStarted
    | ImportCall
    | ImporterCacheChange
    | MonitoringError
    | MetaPathChange
    | MetaPathReplacement
    | PathHooksChange
    | PathHooksReplacement
    | PathFinderCall
    | SysPathChange
    | SysPathReplacement
)
