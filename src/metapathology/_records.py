"""Plain event records captured by the monitor.

Records hold only primitive data (type names, ids, precomputed strings)
extracted at capture time. Foreign objects are never repr()'d while an import
may be in flight; all formatting happens at report time in ``_report``.

Each record is declared as a small class of field annotations. ``_RecordMeta``
derives ``__slots__``, the public ``_fields`` order, and a read-only ``__init__``
from those annotations, so the field list is stated once. Records are frozen
(read-only after construction) and intentionally identity-compared: two records
with equal fields are distinct captured events, so no ``__eq__`` is generated.

The field vocabularies (``MutationOp`` etc.) and ``dataclass_transform`` are
imported only under ``TYPE_CHECKING``; annotations that reference them use
forward-reference strings so the module needs no runtime ``typing`` import.
"""

import types

TYPE_CHECKING = False

if TYPE_CHECKING:
    from traceback import StackSummary
    from typing import ClassVar, Literal
    from typing import cast as _cast

    # ``dataclass_transform`` lives in ``typing`` only from 3.11; import it from
    # ``typing_extensions`` (checker-only, never imported at runtime) so the
    # 3.10 type-check target still resolves it.
    from typing_extensions import dataclass_transform

    # ``annotationlib`` (3.14+) is unavailable at the 3.10 check target; the
    # runtime branch below binds it. None here keeps its usage sites dead code
    # for the checker.
    _annotationlib = None

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
    LocationsState = Literal["not_applicable", "captured", "post_hoc", "deferred", "failed"]
    DeepBoundary = Literal[
        "path_entry_finder",
        "loader_create_module",
        "loader_exec_module",
        "path_hook",
    ]
    DeepOutcome = Literal[
        "started",
        "loaded",
        "failed",
        "found",
        "not_found",
        "returned",
        "raised",
        "unobserved_reentrant",
    ]
    SearchPathKind = Literal["sys_path", "parent_path"]
else:

    def _cast(_type: object, value: object) -> object:
        return value

    def dataclass_transform(**_kwargs: object):
        """Runtime no-op standing in for ``typing.dataclass_transform``."""

        def decorate(cls: object) -> object:
            return cls

        return decorate

    try:
        # Python 3.14+ (PEP 649): class namespaces expose a lazy
        # ``__annotate_func__`` instead of an eager ``__annotations__`` dict.
        import annotationlib as _annotationlib
    except ImportError:  # Python < 3.14
        _annotationlib = None


_MISSING = object()


def _field_names(namespace: "dict[str, object]") -> "tuple[str, ...]":
    """Return the public field names annotated in a class namespace, in order.

    Reads the eager ``__annotations__`` dict on Python < 3.14, or the lazy
    ``__annotate__`` function on 3.14+. ``FORWARDREF`` format keeps
    ``TYPE_CHECKING``-only field types (e.g. ``MutationOp``) from being
    evaluated; only the annotation keys are needed here.
    """
    annotations = namespace.get("__annotations__")
    if not isinstance(annotations, dict) and _annotationlib is not None:
        annotate = namespace.get("__annotate_func__")
        if annotate is not None:
            annotations = _annotationlib.call_annotate_function(annotate, _annotationlib.Format.FORWARDREF)
    if not isinstance(annotations, dict):
        return ()
    return tuple(name for name in annotations if isinstance(name, str) and not name.startswith("_"))


def _make_init(cls_name: str, fields: "tuple[str, ...]", defaults: "dict[str, object]"):
    """Build a read-only ``__init__`` that assigns fields positionally or by keyword."""

    def __init__(self: object, *args: object, **kwargs: object) -> None:
        if len(args) > len(fields):
            raise TypeError(f"{cls_name}() takes at most {len(fields)} positional arguments")
        values: dict[str, object] = dict(zip(fields, args))
        for key, value in kwargs.items():
            if key not in fields:
                raise TypeError(f"{cls_name}() got an unexpected keyword argument {key!r}")
            if key in values:
                raise TypeError(f"{cls_name}() got multiple values for argument {key!r}")
            values[key] = value
        for name in fields:
            if name not in values:
                if name in defaults:
                    values[name] = defaults[name]
                else:
                    raise TypeError(f"{cls_name}() missing required argument {name!r}")
            object.__setattr__(self, name, values[name])

    return __init__


@dataclass_transform(frozen_default=True, eq_default=False)
class _RecordMeta(type):
    """Derive slots, field order, and a frozen ``__init__`` from field annotations."""

    def __new__(mcs, name: str, bases: "tuple[type, ...]", namespace: "dict[str, object]") -> type:
        fields = _field_names(namespace)
        if fields:
            defaults = {n: namespace.pop(n) for n in fields if n in namespace}
            namespace["__slots__"] = fields
            namespace["_fields"] = fields
            namespace["__init__"] = _make_init(name, fields, defaults)
        return super().__new__(mcs, name, bases, namespace)


class _Record(metaclass=_RecordMeta):
    """Frozen, slotted base with a shared repr and read-only enforcement."""

    __slots__ = ()
    _fields: "ClassVar[tuple[str, ...]]" = ()

    if not TYPE_CHECKING:
        # Runtime read-only enforcement. Hidden from the checker, which already
        # treats records as frozen (via ``dataclass_transform``) and forbids
        # overriding ``__setattr__``/``__delattr__`` on a frozen dataclass.
        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError(f"{type(self).__name__!r} attribute {name!r} is read-only")

        def __delattr__(self, name: str) -> None:
            raise AttributeError(f"{type(self).__name__!r} attribute {name!r} is read-only")

    def __repr__(self) -> str:
        fields = ", ".join(f"{name}={getattr(self, name)!r}" for name in self._fields)
        return f"{type(self).__name__}({fields})"


def type_name(obj: object) -> str:
    """Best-effort display name: ``__name__`` for class entries (e.g. ``PathFinder``), type name otherwise."""
    obj_type = type(obj)
    # ``isinstance(obj, type)`` may read a foreign object's spoofed
    # ``__class__`` attribute. Inspect the actual type hierarchy instead.
    cls = _cast("type[object]", obj) if issubclass(obj_type, type) else obj_type
    # Calling ``cls.__name__`` normally dispatches through a custom metaclass.
    # Finder names are captured inside imports, so bypass foreign overrides.
    return type.__getattribute__(cls, "__name__")


class ObjectRef(_Record):
    """Plain identity metadata for an observed foreign object."""

    object_id: int
    type_name: str
    name: str | None = None

    @classmethod
    def of(cls, obj: object) -> "ObjectRef":
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


class FinderContract(_Record):
    """Protocol inventory captured before metapathology changes a finder."""

    finder_id: int
    finder_type_name: str
    position: int
    observation: str
    observation_seq: int | None
    find_spec: FinderProtocol
    find_module: FinderProtocol


class ModuleCacheState(_Record):
    """Plain identity state for one name in a module cache."""

    state: "CacheState"
    object_id: int | None = None
    type_name: str | None = None


class ImporterCacheEntry(_Record):
    """One string-keyed ``sys.path_importer_cache`` entry.

    ``finder=None`` represents a negative cache entry, not an absent path.
    """

    path: str
    finder: ObjectRef | None


class ImporterCacheReplacement(_Record):
    """One cache path whose finder identity or negative status changed."""

    path: str
    before: ObjectRef | None
    after: ObjectRef | None


class ImporterCacheDiff(_Record):
    """A batch of importer-cache changes observed at one observation point."""

    seq: int
    observation: str
    added: tuple[ImporterCacheEntry, ...]
    removed: tuple[ImporterCacheEntry, ...]
    replaced: tuple[ImporterCacheReplacement, ...]
    non_string_keys_before: int
    non_string_keys_after: int
    thread_name: str


class ImportAuditStart(_Record):
    """One CPython ``import`` audit event proving resolution started.

    The audit event has no matching completion signal. This record therefore
    does not claim that the import succeeded, failed, or selected a finder.
    Enabled auxiliary mechanisms contribute only constant-size identities and
    the importer-cache size; detailed list and cache changes remain separate
    events.
    """

    seq: int
    attempt_id: int
    fullname: str
    meta_path_id: int
    meta_path_type_names: tuple[str, ...]
    path_hooks_id: int | None
    importer_cache_id: int | None
    importer_cache_size: int | None
    thread_name: str
    thread_id: int


class MetaPathMutation(_Record):
    """A mutating method call observed on the instrumented ``sys.meta_path`` list.

    Attributes:
        seq: Position in the monitor's single event log; one counter is shared
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

    seq: int
    op: "MutationOp"
    added: tuple[str, ...]
    removed: tuple[str, ...]
    contents_after: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class MetaPathReassignment(_Record):
    """``sys.meta_path`` was replaced wholesale, detected via the ``import`` audit event.

    Detection granularity is the next import after the fact, so every field
    describes the state *at detection time*, not at the moment of reassignment.

    Attributes:
        seq: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathMutation`).
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

    seq: int
    during_import: str
    old_contents: tuple[str, ...]
    new_contents: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class PathHooksMutation(_Record):
    """A mutating method call observed on the instrumented ``sys.path_hooks`` list."""

    seq: int
    op: "MutationOp"
    added: tuple[ObjectRef, ...]
    removed: tuple[ObjectRef, ...]
    contents_after: tuple[ObjectRef, ...]
    thread_name: str
    stack: "StackSummary"


class PathHooksReassignment(_Record):
    """``sys.path_hooks`` replacement detected at the next import audit event."""

    seq: int
    during_import: str
    old_contents: tuple[ObjectRef, ...]
    new_contents: tuple[ObjectRef, ...]
    thread_name: str
    stack: "StackSummary"


class SysPathMutation(_Record):
    """A mutating method call observed on the opt-in instrumented ``sys.path`` list."""

    seq: int
    op: "MutationOp"
    added: tuple[str, ...]
    removed: tuple[str, ...]
    contents_after: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class SysPathReassignment(_Record):
    """``sys.path`` replacement detected at the next import audit event."""

    seq: int
    during_import: str
    old_contents: tuple[str, ...]
    new_contents: tuple[str, ...]
    thread_name: str
    stack: "StackSummary"


class SpecSummary(_Record):
    """Import-safe semantic summary of a finder-produced module spec."""

    spec: ObjectRef
    loader: ObjectRef | None
    origin: str | ObjectRef | None
    cached: str | ObjectRef | None
    is_package: bool | None
    is_namespace: bool | None
    submodule_search_locations: tuple[str | ObjectRef, ...] | None
    locations_state: "LocationsState"
    unavailable_fields: tuple[str, ...] = ()


class FindSpecCall(_Record):
    """One ``find_spec`` call on an instrumented meta-path finder.

    Attributes:
        seq: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathMutation`).
        fullname: The module name that was probed.
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
            ``sys.path`` for a top-level import. Used by report-time probes
            without substituting a mutable report-time search path.
        target_state: Identity-only snapshot of the reload target, when one
            was passed to ``find_spec``. A report-time probe only reuses the
            object if that exact identity remains available.
        exception_type_name: Type name of the exception if ``find_spec``
            raised instead of returning; ``found`` is False in that case.
        thread_name: Name of the thread that ran the import.
    """

    seq: int
    fullname: str
    finder_type_name: str
    finder_id: int
    found: bool
    loader_type_name: str | None
    origin: str | None
    search_path: tuple[str, ...]
    search_path_kind: "SearchPathKind"
    spec_summary: SpecSummary | None
    exception_type_name: str | None
    thread_name: str
    thread_id: int
    module_state_before: ModuleCacheState | None = None
    module_state_after: ModuleCacheState | None = None
    target_state: ModuleCacheState | None = None


class DeepDiagnosticCall(_Record):
    """One call crossing an explicitly enabled deep-diagnostics boundary."""

    seq: int
    boundary: "DeepBoundary"
    object_id: int
    object_type_name: str
    fullname: str | None
    path: str | None
    outcome: "DeepOutcome"
    exception_type_name: str | None
    thread_name: str
    thread_id: int
    module_state_before: ModuleCacheState | None = None
    module_state_after: ModuleCacheState | None = None
    target_state: ModuleCacheState | None = None


class DeepImportEvent(_Record):
    """Entry or exact completion observed at CPython's complete import boundary."""

    seq: int
    attempt_id: int
    fullname: str
    outcome: "DeepOutcome"
    thread_id: int
    thread_name: str


class StandardFinderCall(_Record):
    """A captured aggregate call to a shared standard finder."""

    seq: int
    attempt_id: int
    fullname: str
    finder_type_name: str
    spec_summary: SpecSummary
    thread_id: int
    thread_name: str


class InternalError(_Record):
    """An exception raised inside metapathology's own instrumentation.

    Recorded instead of raised: an exception escaping a hook would abort the
    user's import, which a diagnostic tool must never do.

    Attributes:
        seq: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathMutation`).
        where: Short label of the failing code path, e.g. ``"audit_hook"``.
        exception_type_name: Type name of the caught exception.
        message: Optional pre-sanitized detail. The monitor leaves this unset
            because stringifying an exception supplied by foreign import
            machinery can execute arbitrary code while an import is in flight.
    """

    seq: int
    where: str
    exception_type_name: str
    message: str | None = None


# Everything the monitor records goes into one chronological log; ``seq`` orders records across types.
MonitorEvent = (
    DeepDiagnosticCall
    | DeepImportEvent
    | FindSpecCall
    | ImportAuditStart
    | ImporterCacheDiff
    | InternalError
    | MetaPathMutation
    | MetaPathReassignment
    | PathHooksMutation
    | PathHooksReassignment
    | StandardFinderCall
    | SysPathMutation
    | SysPathReassignment
)
