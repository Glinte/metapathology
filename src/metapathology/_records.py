"""Plain event records captured by the monitor.

Records hold only primitive data (type names, ids, precomputed strings)
extracted at capture time. Foreign objects are never repr()'d while an import
may be in flight; all formatting happens at report time in ``_report``.
"""

# Static analyzers treat this conventional name as true; runtime record users
# do not need traceback solely to resolve the StackSummary annotation.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from traceback import StackSummary
    from typing import Generic, NoReturn, TypeVar, overload
    from typing import cast as _cast

    _FieldT = TypeVar("_FieldT")


if TYPE_CHECKING:

    class _ReadOnlyField(Generic[_FieldT]):
        def __init__(self, slot: str) -> None: ...

        @overload
        def __get__(self, instance: None, owner: type[object] | None = None) -> "_ReadOnlyField[_FieldT]": ...

        @overload
        def __get__(self, instance: object, owner: type[object] | None = None) -> _FieldT: ...

        def __get__(
            self, instance: object | None, owner: type[object] | None = None
        ) -> "_ReadOnlyField[_FieldT] | _FieldT": ...

        def __set__(self, instance: object, value: NoReturn) -> NoReturn: ...

else:

    def _cast(_type: object, value: object) -> object:
        return value

    class _ReadOnlyField:
        """Expose a private slot without permitting assignment through its public name."""

        __slots__ = ("_slot",)

        def __init__(self, slot: str) -> None:
            self._slot = slot

        def __get__(self, instance: object | None, owner: type[object] | None = None) -> object:
            if instance is None:
                return self
            return getattr(instance, self._slot)

        def __set__(self, instance: object, value: object) -> None:
            raise AttributeError(f"{type(instance).__name__!r} attribute {self._slot[1:]!r} is read-only")

        @classmethod
        def __class_getitem__(cls, item: object) -> type["_ReadOnlyField"]:
            return cls


class _Record:
    """Shared repr and read-only public-field setup for captured event records."""

    __slots__ = ()
    _fields: tuple[str, ...] = ()

    def __init_subclass__(cls) -> None:
        for field in cls._fields:
            setattr(cls, field, _ReadOnlyField(f"_{field}"))

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


class ImportObjectRef(_Record):
    """Plain identity metadata for an object participating in import resolution."""

    __slots__ = ("_name", "_object_id", "_type_name")
    _fields = ("object_id", "type_name", "name")
    object_id = _ReadOnlyField[int]("_object_id")
    type_name = _ReadOnlyField[str]("_type_name")
    name = _ReadOnlyField[str | None]("_name")

    def __init__(self, object_id: int, type_name: str, name: str | None = None) -> None:
        self._object_id = object_id
        self._type_name = type_name
        self._name = name


class FinderProtocol(_Record):
    """Conservative raw-dictionary evidence for one finder protocol."""

    __slots__ = ("_availability", "_defined_by", "_evidence")
    _fields = ("availability", "evidence", "defined_by")
    availability = _ReadOnlyField[str]("_availability")
    evidence = _ReadOnlyField[str]("_evidence")
    defined_by = _ReadOnlyField[str | None]("_defined_by")

    def __init__(self, availability: str, evidence: str, defined_by: str | None) -> None:
        self._availability = availability
        self._evidence = evidence
        self._defined_by = defined_by


class FinderContract(_Record):
    """Protocol inventory captured before metapathology changes a finder."""

    __slots__ = (
        "_find_module",
        "_find_spec",
        "_finder_id",
        "_finder_type_name",
        "_observation",
        "_observation_seq",
        "_position",
    )
    _fields = (
        "finder_id",
        "finder_type_name",
        "position",
        "observation",
        "observation_seq",
        "find_spec",
        "find_module",
    )
    finder_id = _ReadOnlyField[int]("_finder_id")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    position = _ReadOnlyField[int]("_position")
    observation = _ReadOnlyField[str]("_observation")
    observation_seq = _ReadOnlyField[int | None]("_observation_seq")
    find_spec = _ReadOnlyField[FinderProtocol]("_find_spec")
    find_module = _ReadOnlyField[FinderProtocol]("_find_module")

    def __init__(
        self,
        *,
        finder_id: int,
        finder_type_name: str,
        position: int,
        observation: str,
        observation_seq: int | None,
        find_spec: FinderProtocol,
        find_module: FinderProtocol,
    ) -> None:
        self._finder_id = finder_id
        self._finder_type_name = finder_type_name
        self._position = position
        self._observation = observation
        self._observation_seq = observation_seq
        self._find_spec = find_spec
        self._find_module = find_module


class ModuleCacheState(_Record):
    """Plain identity state for one name in a module cache."""

    __slots__ = ("_object_id", "_state", "_type_name")
    _fields = ("state", "object_id", "type_name")
    state = _ReadOnlyField[str]("_state")
    object_id = _ReadOnlyField[int | None]("_object_id")
    type_name = _ReadOnlyField[str | None]("_type_name")

    def __init__(self, state: str, object_id: int | None = None, type_name: str | None = None) -> None:
        self._state = state
        self._object_id = object_id
        self._type_name = type_name


class ImporterCacheEntry(_Record):
    """One string-keyed ``sys.path_importer_cache`` entry.

    ``finder=None`` represents a negative cache entry, not an absent path.
    """

    __slots__ = ("_finder", "_path")
    _fields = ("path", "finder")
    path = _ReadOnlyField[str]("_path")
    finder = _ReadOnlyField[ImportObjectRef | None]("_finder")

    def __init__(self, path: str, finder: ImportObjectRef | None) -> None:
        self._path = path
        self._finder = finder


class ImporterCacheReplacement(_Record):
    """One cache path whose finder identity or negative status changed."""

    __slots__ = ("_after", "_before", "_path")
    _fields = ("path", "before", "after")
    path = _ReadOnlyField[str]("_path")
    before = _ReadOnlyField[ImportObjectRef | None]("_before")
    after = _ReadOnlyField[ImportObjectRef | None]("_after")

    def __init__(
        self,
        path: str,
        before: ImportObjectRef | None,
        after: ImportObjectRef | None,
    ) -> None:
        self._path = path
        self._before = before
        self._after = after


class ImporterCacheDiff(_Record):
    """Changes found between two passive importer-cache observations."""

    __slots__ = (
        "_added",
        "_non_string_keys_after",
        "_non_string_keys_before",
        "_observation",
        "_removed",
        "_replaced",
        "_seq",
        "_thread_name",
    )
    _fields = (
        "seq",
        "observation",
        "added",
        "removed",
        "replaced",
        "non_string_keys_before",
        "non_string_keys_after",
        "thread_name",
    )
    seq = _ReadOnlyField[int]("_seq")
    observation = _ReadOnlyField[str]("_observation")
    added = _ReadOnlyField[tuple[ImporterCacheEntry, ...]]("_added")
    removed = _ReadOnlyField[tuple[ImporterCacheEntry, ...]]("_removed")
    replaced = _ReadOnlyField[tuple[ImporterCacheReplacement, ...]]("_replaced")
    non_string_keys_before = _ReadOnlyField[int]("_non_string_keys_before")
    non_string_keys_after = _ReadOnlyField[int]("_non_string_keys_after")
    thread_name = _ReadOnlyField[str]("_thread_name")

    def __init__(
        self,
        seq: int,
        observation: str,
        added: tuple[ImporterCacheEntry, ...],
        removed: tuple[ImporterCacheEntry, ...],
        replaced: tuple[ImporterCacheReplacement, ...],
        non_string_keys_before: int,
        non_string_keys_after: int,
        thread_name: str,
    ) -> None:
        self._seq = seq
        self._observation = observation
        self._added = added
        self._removed = removed
        self._replaced = replaced
        self._non_string_keys_before = non_string_keys_before
        self._non_string_keys_after = non_string_keys_after
        self._thread_name = thread_name


class ImportAuditStart(_Record):
    """One CPython ``import`` audit event proving resolution started.

    The audit event has no matching completion signal. This record therefore
    does not claim that the import succeeded, failed, or selected a finder.
    Enabled auxiliary mechanisms contribute only constant-size identities and
    the importer-cache size; detailed list and cache changes remain separate
    events.
    """

    __slots__ = (
        "_attempt_id",
        "_fullname",
        "_importer_cache_id",
        "_importer_cache_size",
        "_meta_path_id",
        "_meta_path_type_names",
        "_path_hooks_id",
        "_seq",
        "_thread_id",
        "_thread_name",
    )
    _fields = (
        "seq",
        "attempt_id",
        "fullname",
        "meta_path_id",
        "meta_path_type_names",
        "path_hooks_id",
        "importer_cache_id",
        "importer_cache_size",
        "thread_name",
        "thread_id",
    )
    seq = _ReadOnlyField[int]("_seq")
    attempt_id = _ReadOnlyField[int]("_attempt_id")
    fullname = _ReadOnlyField[str]("_fullname")
    meta_path_id = _ReadOnlyField[int]("_meta_path_id")
    meta_path_type_names = _ReadOnlyField[tuple[str, ...]]("_meta_path_type_names")
    path_hooks_id = _ReadOnlyField[int | None]("_path_hooks_id")
    importer_cache_id = _ReadOnlyField[int | None]("_importer_cache_id")
    importer_cache_size = _ReadOnlyField[int | None]("_importer_cache_size")
    thread_name = _ReadOnlyField[str]("_thread_name")
    thread_id = _ReadOnlyField[int]("_thread_id")

    def __init__(
        self,
        seq: int,
        attempt_id: int,
        fullname: str,
        meta_path_id: int,
        meta_path_type_names: tuple[str, ...],
        path_hooks_id: int | None,
        importer_cache_id: int | None,
        importer_cache_size: int | None,
        thread_name: str,
        thread_id: int,
    ) -> None:
        self._seq = seq
        self._attempt_id = attempt_id
        self._fullname = fullname
        self._meta_path_id = meta_path_id
        self._meta_path_type_names = meta_path_type_names
        self._path_hooks_id = path_hooks_id
        self._importer_cache_id = importer_cache_id
        self._importer_cache_size = importer_cache_size
        self._thread_name = thread_name
        self._thread_id = thread_id


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

    __slots__ = ("_added", "_contents_after", "_op", "_removed", "_seq", "_stack", "_thread_name")
    _fields = ("seq", "op", "added", "removed", "contents_after", "thread_name", "stack")
    seq = _ReadOnlyField[int]("_seq")
    op = _ReadOnlyField[str]("_op")
    added = _ReadOnlyField[tuple[str, ...]]("_added")
    removed = _ReadOnlyField[tuple[str, ...]]("_removed")
    contents_after = _ReadOnlyField[tuple[str, ...]]("_contents_after")
    thread_name = _ReadOnlyField[str]("_thread_name")
    if TYPE_CHECKING:
        stack = _ReadOnlyField[StackSummary]("_stack")
    else:
        stack = _ReadOnlyField("_stack")

    def __init__(
        self,
        seq: int,
        op: str,
        added: tuple[str, ...],
        removed: tuple[str, ...],
        contents_after: tuple[str, ...],
        thread_name: str,
        stack: "StackSummary",
    ) -> None:
        self._seq = seq
        self._op = op
        self._added = added
        self._removed = removed
        self._contents_after = contents_after
        self._thread_name = thread_name
        self._stack = stack


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

    __slots__ = ("_during_import", "_new_contents", "_old_contents", "_seq", "_stack", "_thread_name")
    _fields = ("seq", "during_import", "old_contents", "new_contents", "thread_name", "stack")
    seq = _ReadOnlyField[int]("_seq")
    during_import = _ReadOnlyField[str]("_during_import")
    old_contents = _ReadOnlyField[tuple[str, ...]]("_old_contents")
    new_contents = _ReadOnlyField[tuple[str, ...]]("_new_contents")
    thread_name = _ReadOnlyField[str]("_thread_name")
    if TYPE_CHECKING:
        stack = _ReadOnlyField[StackSummary]("_stack")
    else:
        stack = _ReadOnlyField("_stack")

    def __init__(
        self,
        seq: int,
        during_import: str,
        old_contents: tuple[str, ...],
        new_contents: tuple[str, ...],
        thread_name: str,
        stack: "StackSummary",
    ) -> None:
        self._seq = seq
        self._during_import = during_import
        self._old_contents = old_contents
        self._new_contents = new_contents
        self._thread_name = thread_name
        self._stack = stack


class PathHooksMutation(_Record):
    """A mutating method call observed on the instrumented ``sys.path_hooks`` list."""

    __slots__ = ("_added", "_contents_after", "_op", "_removed", "_seq", "_stack", "_thread_name")
    _fields = ("seq", "op", "added", "removed", "contents_after", "thread_name", "stack")
    seq = _ReadOnlyField[int]("_seq")
    op = _ReadOnlyField[str]("_op")
    added = _ReadOnlyField[tuple[ImportObjectRef, ...]]("_added")
    removed = _ReadOnlyField[tuple[ImportObjectRef, ...]]("_removed")
    contents_after = _ReadOnlyField[tuple[ImportObjectRef, ...]]("_contents_after")
    thread_name = _ReadOnlyField[str]("_thread_name")
    if TYPE_CHECKING:
        stack = _ReadOnlyField[StackSummary]("_stack")
    else:
        stack = _ReadOnlyField("_stack")

    def __init__(
        self,
        seq: int,
        op: str,
        added: tuple[ImportObjectRef, ...],
        removed: tuple[ImportObjectRef, ...],
        contents_after: tuple[ImportObjectRef, ...],
        thread_name: str,
        stack: "StackSummary",
    ) -> None:
        self._seq = seq
        self._op = op
        self._added = added
        self._removed = removed
        self._contents_after = contents_after
        self._thread_name = thread_name
        self._stack = stack


class PathHooksReassignment(_Record):
    """``sys.path_hooks`` replacement detected at the next import audit event."""

    __slots__ = ("_during_import", "_new_contents", "_old_contents", "_seq", "_stack", "_thread_name")
    _fields = ("seq", "during_import", "old_contents", "new_contents", "thread_name", "stack")
    seq = _ReadOnlyField[int]("_seq")
    during_import = _ReadOnlyField[str]("_during_import")
    old_contents = _ReadOnlyField[tuple[ImportObjectRef, ...]]("_old_contents")
    new_contents = _ReadOnlyField[tuple[ImportObjectRef, ...]]("_new_contents")
    thread_name = _ReadOnlyField[str]("_thread_name")
    if TYPE_CHECKING:
        stack = _ReadOnlyField[StackSummary]("_stack")
    else:
        stack = _ReadOnlyField("_stack")

    def __init__(
        self,
        seq: int,
        during_import: str,
        old_contents: tuple[ImportObjectRef, ...],
        new_contents: tuple[ImportObjectRef, ...],
        thread_name: str,
        stack: "StackSummary",
    ) -> None:
        self._seq = seq
        self._during_import = during_import
        self._old_contents = old_contents
        self._new_contents = new_contents
        self._thread_name = thread_name
        self._stack = stack


class SpecSummary(_Record):
    """Import-safe semantic summary of a finder-produced module spec."""

    __slots__ = (
        "_cached",
        "_is_namespace",
        "_is_package",
        "_loader",
        "_locations_state",
        "_origin",
        "_spec",
        "_submodule_search_locations",
        "_unavailable_fields",
    )
    _fields = (
        "spec",
        "loader",
        "origin",
        "cached",
        "is_package",
        "is_namespace",
        "submodule_search_locations",
        "locations_state",
        "unavailable_fields",
    )
    spec = _ReadOnlyField[ImportObjectRef]("_spec")
    loader = _ReadOnlyField[ImportObjectRef | None]("_loader")
    origin = _ReadOnlyField[str | ImportObjectRef | None]("_origin")
    cached = _ReadOnlyField[str | ImportObjectRef | None]("_cached")
    is_package = _ReadOnlyField[bool | None]("_is_package")
    is_namespace = _ReadOnlyField[bool | None]("_is_namespace")
    submodule_search_locations = _ReadOnlyField[tuple[str | ImportObjectRef, ...] | None]("_submodule_search_locations")
    locations_state = _ReadOnlyField[str]("_locations_state")
    unavailable_fields = _ReadOnlyField[tuple[str, ...]]("_unavailable_fields")

    def __init__(
        self,
        spec: ImportObjectRef,
        loader: ImportObjectRef | None,
        origin: str | ImportObjectRef | None,
        cached: str | ImportObjectRef | None,
        is_package: bool | None,
        is_namespace: bool | None,
        submodule_search_locations: tuple[str | ImportObjectRef, ...] | None,
        locations_state: str,
        unavailable_fields: tuple[str, ...] = (),
    ) -> None:
        self._spec = spec
        self._loader = loader
        self._origin = origin
        self._cached = cached
        self._is_package = is_package
        self._is_namespace = is_namespace
        self._submodule_search_locations = submodule_search_locations
        self._locations_state = locations_state
        self._unavailable_fields = unavailable_fields


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
            ``sys.path`` for a top-level import. Used to replay ``PathFinder``
            against import-time state rather than mutable report-time state.
        exception_type_name: Type name of the exception if ``find_spec``
            raised instead of returning; ``found`` is False in that case.
        thread_name: Name of the thread that ran the import.
    """

    __slots__ = (
        "_exception_type_name",
        "_finder_id",
        "_finder_type_name",
        "_found",
        "_fullname",
        "_loader_type_name",
        "_module_state_after",
        "_module_state_before",
        "_origin",
        "_search_path",
        "_search_path_kind",
        "_seq",
        "_spec_summary",
        "_thread_id",
        "_thread_name",
    )
    _fields = (
        "seq",
        "fullname",
        "finder_type_name",
        "finder_id",
        "found",
        "loader_type_name",
        "origin",
        "module_state_before",
        "module_state_after",
        "search_path",
        "search_path_kind",
        "spec_summary",
        "exception_type_name",
        "thread_name",
        "thread_id",
    )
    seq = _ReadOnlyField[int]("_seq")
    fullname = _ReadOnlyField[str]("_fullname")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    finder_id = _ReadOnlyField[int]("_finder_id")
    found = _ReadOnlyField[bool]("_found")
    loader_type_name = _ReadOnlyField[str | None]("_loader_type_name")
    origin = _ReadOnlyField[str | None]("_origin")
    module_state_before = _ReadOnlyField[ModuleCacheState | None]("_module_state_before")
    module_state_after = _ReadOnlyField[ModuleCacheState | None]("_module_state_after")
    search_path = _ReadOnlyField[tuple[str, ...]]("_search_path")
    search_path_kind = _ReadOnlyField[str]("_search_path_kind")
    spec_summary = _ReadOnlyField[SpecSummary | None]("_spec_summary")
    exception_type_name = _ReadOnlyField[str | None]("_exception_type_name")
    thread_name = _ReadOnlyField[str]("_thread_name")
    thread_id = _ReadOnlyField[int]("_thread_id")

    def __init__(
        self,
        seq: int,
        fullname: str,
        finder_type_name: str,
        finder_id: int,
        found: bool,
        loader_type_name: str | None,
        origin: str | None,
        search_path: tuple[str, ...],
        search_path_kind: str,
        spec_summary: SpecSummary | None,
        exception_type_name: str | None,
        thread_name: str,
        thread_id: int,
        module_state_before: ModuleCacheState | None = None,
        module_state_after: ModuleCacheState | None = None,
    ) -> None:
        self._seq = seq
        self._fullname = fullname
        self._finder_type_name = finder_type_name
        self._finder_id = finder_id
        self._found = found
        self._loader_type_name = loader_type_name
        self._origin = origin
        self._module_state_before = module_state_before
        self._module_state_after = module_state_after
        self._search_path = search_path
        self._search_path_kind = search_path_kind
        self._spec_summary = spec_summary
        self._exception_type_name = exception_type_name
        self._thread_name = thread_name
        self._thread_id = thread_id


class DeepDiagnosticCall(_Record):
    """One call crossing an explicitly enabled deep-diagnostics boundary."""

    __slots__ = (
        "_boundary",
        "_exception_type_name",
        "_fullname",
        "_module_state_after",
        "_module_state_before",
        "_object_id",
        "_object_type_name",
        "_outcome",
        "_path",
        "_seq",
        "_target_state",
        "_thread_id",
        "_thread_name",
    )
    _fields = (
        "seq",
        "boundary",
        "object_id",
        "object_type_name",
        "fullname",
        "path",
        "outcome",
        "module_state_before",
        "module_state_after",
        "exception_type_name",
        "thread_name",
        "thread_id",
        "target_state",
    )
    seq = _ReadOnlyField[int]("_seq")
    boundary = _ReadOnlyField[str]("_boundary")
    object_id = _ReadOnlyField[int]("_object_id")
    object_type_name = _ReadOnlyField[str]("_object_type_name")
    fullname = _ReadOnlyField[str | None]("_fullname")
    path = _ReadOnlyField[str | None]("_path")
    outcome = _ReadOnlyField[str]("_outcome")
    module_state_before = _ReadOnlyField[ModuleCacheState | None]("_module_state_before")
    module_state_after = _ReadOnlyField[ModuleCacheState | None]("_module_state_after")
    exception_type_name = _ReadOnlyField[str | None]("_exception_type_name")
    thread_name = _ReadOnlyField[str]("_thread_name")
    thread_id = _ReadOnlyField[int]("_thread_id")
    target_state = _ReadOnlyField[ModuleCacheState | None]("_target_state")

    def __init__(
        self,
        seq: int,
        boundary: str,
        object_id: int,
        object_type_name: str,
        fullname: str | None,
        path: str | None,
        outcome: str,
        exception_type_name: str | None,
        thread_name: str,
        thread_id: int,
        module_state_before: ModuleCacheState | None = None,
        module_state_after: ModuleCacheState | None = None,
        target_state: ModuleCacheState | None = None,
    ) -> None:
        self._seq = seq
        self._boundary = boundary
        self._object_id = object_id
        self._object_type_name = object_type_name
        self._fullname = fullname
        self._path = path
        self._outcome = outcome
        self._module_state_before = module_state_before
        self._module_state_after = module_state_after
        self._exception_type_name = exception_type_name
        self._thread_name = thread_name
        self._thread_id = thread_id
        self._target_state = target_state


class DeepImportEvent(_Record):
    """Entry or exact completion observed at CPython's complete import boundary."""

    __slots__ = ("_attempt_id", "_fullname", "_outcome", "_seq", "_thread_id", "_thread_name")
    _fields = ("seq", "attempt_id", "fullname", "outcome", "thread_id", "thread_name")
    seq = _ReadOnlyField[int]("_seq")
    attempt_id = _ReadOnlyField[int]("_attempt_id")
    fullname = _ReadOnlyField[str]("_fullname")
    outcome = _ReadOnlyField[str]("_outcome")
    thread_id = _ReadOnlyField[int]("_thread_id")
    thread_name = _ReadOnlyField[str]("_thread_name")

    def __init__(
        self, seq: int, attempt_id: int, fullname: str, outcome: str, thread_id: int, thread_name: str
    ) -> None:
        self._seq = seq
        self._attempt_id = attempt_id
        self._fullname = fullname
        self._outcome = outcome
        self._thread_id = thread_id
        self._thread_name = thread_name


class StandardFinderCall(_Record):
    """A captured aggregate call to a shared standard finder."""

    __slots__ = (
        "_attempt_id",
        "_finder_type_name",
        "_fullname",
        "_seq",
        "_spec_summary",
        "_thread_id",
        "_thread_name",
    )
    _fields = (
        "seq",
        "attempt_id",
        "fullname",
        "finder_type_name",
        "spec_summary",
        "thread_id",
        "thread_name",
    )
    seq = _ReadOnlyField[int]("_seq")
    attempt_id = _ReadOnlyField[int]("_attempt_id")
    fullname = _ReadOnlyField[str]("_fullname")
    finder_type_name = _ReadOnlyField[str]("_finder_type_name")
    spec_summary = _ReadOnlyField[SpecSummary]("_spec_summary")
    thread_id = _ReadOnlyField[int]("_thread_id")
    thread_name = _ReadOnlyField[str]("_thread_name")

    def __init__(
        self,
        seq: int,
        attempt_id: int,
        fullname: str,
        finder_type_name: str,
        spec_summary: SpecSummary,
        thread_id: int,
        thread_name: str,
    ) -> None:
        self._seq = seq
        self._attempt_id = attempt_id
        self._fullname = fullname
        self._finder_type_name = finder_type_name
        self._spec_summary = spec_summary
        self._thread_id = thread_id
        self._thread_name = thread_name


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

    __slots__ = ("_exception_type_name", "_message", "_seq", "_where")
    _fields = ("seq", "where", "exception_type_name", "message")
    seq = _ReadOnlyField[int]("_seq")
    where = _ReadOnlyField[str]("_where")
    exception_type_name = _ReadOnlyField[str]("_exception_type_name")
    message = _ReadOnlyField[str | None]("_message")

    def __init__(self, seq: int, where: str, exception_type_name: str, message: str | None = None) -> None:
        self._seq = seq
        self._where = where
        self._exception_type_name = exception_type_name
        self._message = message


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
)
