"""Core monitor: audit hook, instrumented ``sys.meta_path`` list, finder shadowing.

Hot-path rules (see CLAUDE.md): everything needed here is imported at module
scope; recording code takes ``_record_lock`` only around plain-data appends;
foreign objects are reduced to type names and ids at capture time.
"""

import atexit
import copy
import functools
import importlib._bootstrap as _importlib_bootstrap
import operator
import os
import sys
import threading
import traceback
import types
import warnings
from contextlib import suppress
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder

from metapathology import _report
from metapathology._module_metadata import module_cache_state, safe_module_name, safe_spec_loader, safe_spec_name
from metapathology._records import (
    DeepDiagnosticCall,
    DeepImportEvent,
    FinderContract,
    FinderProtocol,
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
    SysPathMutation,
    SysPathReassignment,
    type_name,
)
from metapathology._spec import summarize_spec

# Supported type checkers treat this conventional name as true without making
# the runtime import typing solely for annotations and casts.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from importlib.machinery import ModuleSpec
    from os import PathLike
    from types import FrameType, ModuleType
    from typing import Any, Literal, Protocol, SupportsIndex, TextIO, TypeVar, overload
    from typing import cast as _cast

    from _typeshed import SupportsRichComparison
    from _typeshed.importlib import MetaPathFinderProtocol, PathEntryFinderProtocol

    _FindSpec = Callable[[str, Sequence[str] | None, ModuleType | None], ModuleSpec | None]
    _ImportListItemT = TypeVar("_ImportListItemT")
    _MetaPathEntry = MetaPathFinderProtocol
    _PathHook = Callable[[str], PathEntryFinderProtocol]

    class _ImportErrorNameDescriptor(Protocol):
        def __get__(self, instance: ImportError, owner: type[ImportError]) -> object: ...

else:
    _ImportErrorNameDescriptor = object

    def _cast(_type: object, value: object) -> object:
        """Return ``value`` unchanged; type checkers use ``typing.cast`` above."""
        return value


# Frames captured per event; the report trims further (and filters noise) at display time.
_STACK_CAPTURE_LIMIT = 20
# Reuse common immutable path snapshots without retaining an unbounded intern
# table. Eight entries cover the usual top-level path plus several package
# paths; least-recently used eviction keeps both work and auxiliary memory constant.
_SEARCH_PATH_CACHE_SIZE = 8
_MISSING = object()
_STANDARD_CLASS_FINDERS = (BuiltinImporter, FrozenImporter, PathFinder)
_STANDARD_CLASS_FINDER_REASON = "standard CPython class finder; expected and deliberately left unchanged"
_REPORT_DESTINATION_ENV = "METAPATHOLOGY_REPORT"
_REPORT_FORMAT_ENV = "METAPATHOLOGY_REPORT_FORMAT"
_REPORT_COLOR_ENV = "METAPATHOLOGY_COLOR"
_MONITOR_PATH_HOOKS_ENV = "METAPATHOLOGY_MONITOR_PATH_HOOKS"
_MONITOR_IMPORTER_CACHE_ENV = "METAPATHOLOGY_MONITOR_IMPORTER_CACHE"
_MONITOR_SYS_PATH_ENV = "METAPATHOLOGY_MONITOR_SYS_PATH"
_DEEP_ENV = "METAPATHOLOGY_DEEP"
_DEEP_PATH_HOOKS_ENV = "METAPATHOLOGY_DEEP_PATH_HOOKS"
_DEEP_PATH_ENTRY_FINDERS_ENV = "METAPATHOLOGY_DEEP_PATH_ENTRY_FINDERS"
_DEEP_LOADERS_ENV = "METAPATHOLOGY_DEEP_LOADERS"
_DEEP_IMPORT_OUTCOMES_ENV = "METAPATHOLOGY_DEEP_IMPORT_OUTCOMES"
_TRUE_ENV_VALUES = frozenset(("1", "true", "yes", "on"))
_FALSE_ENV_VALUES = frozenset(("0", "false", "no", "off"))
_IMPLEMENTATION_NAME = sys.implementation.name
_UNSUPPORTED_IMPLEMENTATION_WARNING = (
    f"metapathology supports CPython only; monitoring on {_IMPLEMENTATION_NAME!r} may be incomplete or inaccurate"
)


def _raw_protocol_value(value: object, evidence: str, defined_by: str) -> FinderProtocol:
    """Classify a dictionary value without binding or invoking a descriptor."""
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    elif not callable(value):
        try:
            for owner in type.__getattribute__(type(value), "__mro__"):
                if "__get__" in type.__getattribute__(owner, "__dict__"):
                    return FinderProtocol("indeterminate", evidence, defined_by)
        except BaseException:
            return FinderProtocol("indeterminate", "inspection_error", None)
    return FinderProtocol("callable" if callable(value) else "non_callable", evidence, defined_by)


def _inspect_finder_protocol(finder: object, name: str) -> FinderProtocol:
    """Inspect one protocol through raw dictionaries and the real type MRO."""
    try:
        if not issubclass(type(finder), type):
            try:
                instance_dict = object.__getattribute__(finder, "__dict__")
            except (AttributeError, TypeError):
                instance_dict = None
            if type(instance_dict) is dict and name in instance_dict:
                return _raw_protocol_value(instance_dict[name], "instance_dict", type_name(finder))
            cls = type(finder)
        else:
            cls = _cast("type[object]", finder)
        mro = type.__getattribute__(cls, "__mro__")
        for owner in mro:
            namespace = type.__getattribute__(owner, "__dict__")
            if name in namespace:
                owner_name = type.__getattribute__(owner, "__name__")
                return _raw_protocol_value(namespace[name], "class_dict", owner_name)
    except BaseException:
        return FinderProtocol("indeterminate", "inspection_error", None)
    return FinderProtocol("absent", "class_mro", None)


class _ImporterCacheReportState:
    """Plain cache evidence copied with the monitor's report cutoff."""

    __slots__ = (
        "coalesced",
        "enabled",
        "initial_entries",
        "initial_non_string_keys",
        "latest_entries",
        "latest_non_string_keys",
        "observations",
    )

    def __init__(
        self,
        *,
        enabled: bool,
        initial_entries: tuple[ImporterCacheEntry, ...],
        initial_non_string_keys: int,
        latest_entries: tuple[ImporterCacheEntry, ...] | None,
        latest_non_string_keys: int | None,
        observations: int,
        coalesced: int,
    ) -> None:
        self.enabled = enabled
        self.initial_entries = initial_entries
        self.initial_non_string_keys = initial_non_string_keys
        self.latest_entries = latest_entries
        self.latest_non_string_keys = latest_non_string_keys
        self.observations = observations
        self.coalesced = coalesced


class _EarlySiteBootstrapState:
    """Plain provenance for an active early site bootstrap."""

    __slots__ = ("activation_source", "earlier_pth_files", "path", "site_packages")

    def __init__(
        self,
        path: str,
        site_packages: str,
        activation_source: str,
        earlier_pth_files: tuple[str, ...],
    ) -> None:
        self.path = path
        self.site_packages = site_packages
        self.activation_source = activation_source
        self.earlier_pth_files = earlier_pth_files


class _FrozenBootstrapState:
    """Plain provenance for activation inside a frozen application."""

    __slots__ = ("boundary", "integration", "path")

    def __init__(self, integration: str, path: str, boundary: str) -> None:
        self.integration = integration
        self.path = path
        self.boundary = boundary


class _TargetOutcomeState:
    """Plain reduction of how the monitored target finished."""

    __slots__ = ("exception_type_name", "exit_code", "kind", "missing_module")

    def __init__(
        self,
        kind: str,
        exception_type_name: str | None,
        missing_module: str | None,
        exit_code: int | None,
    ) -> None:
        self.kind = kind
        self.exception_type_name = exception_type_name
        self.missing_module = missing_module
        self.exit_code = exit_code


class _ThreadState(threading.local):
    """Per-thread re-entrancy state with a default for newly seen threads."""

    active = False


def _capture_stack(frame: "FrameType") -> traceback.StackSummary:
    """Capture a stack summary from ``frame`` outward without reading source lines.

    ``lookup_lines=False`` keeps source reading (and any ``__loader__.get_source``
    side effects) out of the import hot path; lines resolve at format time.

    Args:
        frame: Innermost frame to start walking from.
    """
    return traceback.StackSummary.extract(traceback.walk_stack(frame), limit=_STACK_CAPTURE_LIMIT, lookup_lines=False)


def _import_object_ref(obj: object) -> ImportObjectRef:
    """Reduce an import object to safe identity metadata without foreign dispatch."""
    name: str | None = None
    if isinstance(obj, (types.FunctionType, types.BuiltinFunctionType, types.MethodType)):
        try:
            raw_name = object.__getattribute__(obj, "__name__")
        except (AttributeError, TypeError):
            pass
        else:
            if isinstance(raw_name, str):
                name = raw_name
    return ImportObjectRef(object_id=id(obj), type_name=type_name(obj), name=name)


def _cache_values_match(left: ImportObjectRef | None, right: ImportObjectRef | None) -> bool:
    """Compare captured cache values without relying on record identity equality."""
    if left is None or right is None:
        return left is right
    return left.object_id == right.object_id and left.type_name == right.type_name and left.name == right.name


def _diff_importer_cache(
    before: dict[str, ImportObjectRef | None],
    after: dict[str, ImportObjectRef | None],
) -> tuple[
    tuple[ImporterCacheEntry, ...],
    tuple[ImporterCacheEntry, ...],
    tuple[ImporterCacheReplacement, ...],
]:
    """Return deterministic additions, removals, and replacements."""
    before_paths = set(before)
    after_paths = set(after)
    added = tuple(ImporterCacheEntry(path, after[path]) for path in sorted(after_paths - before_paths))
    removed = tuple(ImporterCacheEntry(path, before[path]) for path in sorted(before_paths - after_paths))
    replaced = tuple(
        ImporterCacheReplacement(path, before[path], after[path])
        for path in sorted(before_paths & after_paths)
        if not _cache_values_match(before[path], after[path])
    )
    return added, removed, replaced


def _cache_entries(snapshot: dict[str, ImportObjectRef | None]) -> tuple[ImporterCacheEntry, ...]:
    """Project a cache snapshot into deterministic public entry records."""
    return tuple(ImporterCacheEntry(path, snapshot[path]) for path in sorted(snapshot))


def _audit_resolution_name(args: tuple[object, ...]) -> str | None:
    """Return the module name only for CPython's resolution-start event shape.

    Native extension loading emits a second ``import`` event whose filename is
    populated and whose import-state arguments are ``None``. It is a loading
    boundary, not a second resolution start.
    """
    if (
        len(args) < 5
        or type(args[0]) is not str
        or args[1] is not None
        or args[2] is None
        or args[3] is None
        or args[4] is None
    ):
        return None
    return args[0]


def _snapshot_importer_cache() -> tuple[
    dict[str, ImportObjectRef | None],
    int,
    dict[int, object],
    tuple[int, int],
]:
    """Copy string-keyed cache state once without stringifying foreign objects."""
    cache = sys.path_importer_cache
    raw_items = list(cache.items())
    entries: dict[str, ImportObjectRef | None] = {}
    finders: dict[int, object] = {}
    non_string_keys = 0
    for path, finder in raw_items:
        # A str subclass can override hashing and equality. Only exact strings
        # are safe keys for later plain-data diffing and sorting.
        if type(path) is not str:
            non_string_keys += 1
            continue
        if finder is None:
            entries[path] = None
            continue
        reference = _import_object_ref(finder)
        entries[path] = reference
        finders[reference.object_id] = finder
    return entries, non_string_keys, finders, (id(cache), len(cache))


def _path_item_name(item: object) -> str:
    """Reduce a path item without invoking a foreign string conversion."""
    return item if type(item) is str else f"<{type_name(item)}>"


class Monitor:
    """Records import-machinery activity. Use the module-level :func:`install`."""

    def __init__(self) -> None:
        # Guards _events/_seq/_patched/_skipped/_search_path_cache. Held only
        # around plain-data reads/writes, never across foreign code.
        self._record_lock = threading.Lock()
        # Serializes install/uninstall/reinstall. Lock order: _reinstall_lock
        # may be held while taking _record_lock, never the other way around.
        self._reinstall_lock = threading.Lock()
        # Per-thread re-entrancy flag around every hook and wrapper. Nested
        # imports still run normally, but our instrumentation delegates without
        # trying to observe itself recursively.
        self._local = _ThreadState()
        self._deep_local = _ThreadState()
        # One counter for all record types, so the report can interleave the
        # different event kinds chronologically.
        self._seq = 0
        self._attempt_id = 0
        self._thread_id = 0
        self._events: list[MonitorEvent] = []
        self._search_path_cache: list[tuple[str, ...]] = []
        # Master switch: hooks stay registered after uninstall() (audit hooks
        # are irremovable) but become no-ops when this is False.
        self._enabled = False
        # sys.addaudithook is one-way; remember so a reinstall never stacks a second hook.
        self._audit_installed = False
        # Whether our atexit callback is currently registered (mirrors it for unregister).
        self._report_at_exit = False
        # Resolved automatic report destination. None means stderr. The PID is
        # added only when an automatic file report is written; explicit paths
        # passed to write_report() remain unchanged.
        self._report_destination: str | None = None
        self._report_format: Literal["text", "json"] = "text"
        self._report_color: Literal["auto", "always", "never"] = "auto"
        # Set by the generated .pth activation path. The first activation wins
        # because a later site directory cannot move the evidence cutoff earlier.
        self._early_site_bootstrap: _EarlySiteBootstrapState | None = None
        self._frozen_bootstrap: _FrozenBootstrapState | None = None
        # The exact list object we last put into sys.meta_path. The audit hook
        # compares by identity: a mismatch means someone reassigned sys.meta_path.
        self._instrumented: _InstrumentedMetaPath | None = None
        # T1 is independently enableable. Observed hooks remain strongly
        # referenced while active so recorded ids cannot be reused mid-capture.
        self._path_hooks_enabled = False
        self._instrumented_path_hooks: _InstrumentedPathHooks | None = None
        self._initial_path_hooks: tuple[ImportObjectRef, ...] = ()
        self._observed_path_hooks: dict[int, object] = {}
        # Opt-in because replacing sys.path has a wider compatibility surface
        # than observing the import-specific lists enabled by default.
        self._sys_path_enabled = False
        self._instrumented_sys_path: _InstrumentedSysPath | None = None
        # T2 retains one install snapshot and one rolling comparison snapshot.
        # Full observations are coalesced when one is already in progress;
        # diff events themselves remain exhaustive and unbounded.
        self._importer_cache_enabled = False
        self._initial_importer_cache: tuple[ImporterCacheEntry, ...] = ()
        self._initial_importer_cache_non_string_keys = 0
        self._latest_importer_cache: dict[str, ImportObjectRef | None] | None = None
        self._latest_importer_cache_non_string_keys: int | None = None
        self._importer_cache_fingerprint: tuple[int, int] | None = None
        self._importer_cache_dirty = False
        self._importer_cache_observation_active = False
        self._importer_cache_observations = 0
        self._importer_cache_coalesced = 0
        self._observed_cache_finders: dict[int, object] = {}
        # Both dicts key by id(finder) and hold strong finder references, keeping ids
        # unique for the process lifetime.
        # id -> (finder, callable find_spec, previous instance-dict value). A
        # None callable marks an in-progress claim so callers cannot double-wrap.
        self._patched: dict[int, tuple[object, _FindSpec | None, object]] = {}
        # id -> (finder, human-readable reason it could not be instrumented).
        self._skipped: dict[int, tuple[object, str]] = {}
        # sys.modules names at install time; the report analyzes only modules
        # imported afterwards.
        self._baseline_modules: frozenset[str] = frozenset()
        # Finder display names at install time, for the report header.
        self._initial_meta_path: tuple[str, ...] = ()
        # T12 observations are exhaustive over distinct finder identities and
        # retain the finder through the existing patched/skipped maps.
        self._finder_contracts: dict[int, FinderContract] = {}
        self._deep_path_hooks = False
        self._deep_path_entry_finders = False
        self._deep_loaders = False
        self._deep_import_outcomes = False
        self._deep_import_outcomes_status = "disabled"
        self._standard_finder_status = "disabled"
        self._deep_import_code: types.CodeType | None = None
        self._path_finder_code: types.CodeType | None = None
        self._deep_hook_wrappers: dict[int, tuple[object, object]] = {}
        self._deep_finder_patches: dict[int, tuple[object, object]] = {}
        self._deep_loader_patches: dict[int, tuple[object, tuple[tuple[str, object], ...]]] = {}
        # How the monitored target finished, recorded by the CLI (or any
        # embedder) before the report is written. Reduced to plain data at
        # record time; exception messages are never captured.
        self._target_outcome: _TargetOutcomeState | None = None

    @property
    def enabled(self) -> bool:
        """Whether the monitor is currently observing."""
        return self._enabled

    @property
    def initial_meta_path(self) -> tuple[str, ...]:
        """Finder names in ``sys.meta_path`` at install time."""
        return self._initial_meta_path

    @property
    def path_hooks_enabled(self) -> bool:
        """Whether ``sys.path_hooks`` mutation monitoring is currently active."""
        return self._enabled and self._path_hooks_enabled

    @property
    def initial_path_hooks(self) -> tuple[ImportObjectRef, ...]:
        """Safe hook identities captured when path-hook monitoring was enabled."""
        return self._initial_path_hooks

    @property
    def importer_cache_enabled(self) -> bool:
        """Whether passive ``sys.path_importer_cache`` monitoring is active."""
        return self._enabled and self._importer_cache_enabled

    @property
    def sys_path_enabled(self) -> bool:
        """Whether opt-in ``sys.path`` mutation monitoring is active."""
        return self._enabled and self._sys_path_enabled

    @property
    def deep_diagnostics(self) -> tuple[str, ...]:
        """Names of explicitly enabled inline delegation mechanisms."""
        enabled: list[str] = []
        if self._enabled and self._deep_path_hooks:
            enabled.append("path_hooks")
        if self._enabled and self._deep_path_entry_finders:
            enabled.append("path_entry_finders")
        if self._enabled and self._deep_loaders:
            enabled.append("loaders")
        if self._enabled and self._deep_import_outcomes:
            enabled.append("import_outcomes")
        return tuple(enabled)

    @property
    def deep_import_outcomes_status(self) -> str:
        """Activation state and thread scope of exact import outcomes."""
        return self._deep_import_outcomes_status

    @property
    def standard_finder_status(self) -> str:
        """Availability of exact aggregate standard-finder evidence."""
        return self._standard_finder_status

    @property
    def initial_importer_cache(self) -> tuple[ImporterCacheEntry, ...]:
        """String-keyed importer-cache entries captured when T2 was enabled."""
        return self._initial_importer_cache

    def _current_path_hook_refs(self) -> tuple[ImportObjectRef, ...]:
        """Copy current path-hook identities for report capture."""
        return tuple(_import_object_ref(self._original_path_hook(hook)) for hook in list(sys.path_hooks))

    def _original_path_hook(self, hook: object) -> object:
        """Normalize one deep wrapper to the foreign hook it delegates to."""
        wrapped = self._deep_hook_wrappers.get(id(hook))
        return hook if wrapped is None else wrapped[1]

    @property
    def baseline_modules(self) -> frozenset[str]:
        """Names present in ``sys.modules`` at install time."""
        return self._baseline_modules

    @property
    def target_outcome(self) -> "_TargetOutcomeState | None":
        """Plain reduction of the target's completion, if one was recorded."""
        return self._target_outcome

    def record_target_outcome(self, exception: BaseException | None = None, exit_code: int | None = None) -> None:
        """Record how the monitored target finished, reduced to plain data.

        Only the exception's type name and, for ``ImportError`` subclasses,
        its ``name`` attribute are read; the message is never stringified.

        Args:
            exception: The exception that ended the target, or None.
            exit_code: The process exit status the target run produces.
        """
        if exception is None:
            self._target_outcome = _TargetOutcomeState("completed", None, None, exit_code)
            return
        missing: str | None = None
        if isinstance(exception, ImportError):
            # Read the builtin descriptor directly so a subclass property
            # cannot run foreign code during recording.
            try:
                descriptor = _cast(_ImportErrorNameDescriptor, ImportError.__dict__["name"])
                name = descriptor.__get__(exception, type(exception))
            except Exception:
                name = None
            missing = name if type(name) is str else None
        self._target_outcome = _TargetOutcomeState("raised", type_name(exception), missing, exit_code)

    def events(self) -> list[MonitorEvent]:
        """Return a snapshot of all recorded events, in capture order."""
        with self._record_lock:
            return list(self._events)

    def skipped_finders(self) -> list[tuple[str, str]]:
        """Return ``(finder name, reason)`` for meta-path entries that could not be instrumented."""
        with self._record_lock:
            return [(type_name(finder), reason) for finder, reason in self._skipped.values()]

    def _report_state(
        self,
    ) -> tuple[
        int,
        list[MonitorEvent],
        list[tuple[object, str]],
        list[FinderContract],
        _ImporterCacheReportState,
        _EarlySiteBootstrapState | None,
        _FrozenBootstrapState | None,
    ]:
        """Copy chronological evidence and skipped finders at one sequence cutoff."""
        self._observe_importer_cache("report")
        with self._record_lock:
            latest = self._latest_importer_cache
            cache_state = _ImporterCacheReportState(
                enabled=self._enabled and self._importer_cache_enabled,
                initial_entries=self._initial_importer_cache,
                initial_non_string_keys=self._initial_importer_cache_non_string_keys,
                latest_entries=None if latest is None else _cache_entries(latest),
                latest_non_string_keys=self._latest_importer_cache_non_string_keys,
                observations=self._importer_cache_observations,
                coalesced=self._importer_cache_coalesced,
            )
            return (
                self._seq,
                list(self._events),
                list(self._skipped.values()),
                list(self._finder_contracts.values()),
                cache_state,
                self._early_site_bootstrap,
                self._frozen_bootstrap,
            )

    def _set_early_site_bootstrap(
        self,
        path: str,
        site_packages: str,
        activation_source: str,
        earlier_pth_files: tuple[str, ...],
    ) -> None:
        """Attach generated bootstrap provenance to this capture."""
        state = _EarlySiteBootstrapState(path, site_packages, activation_source, earlier_pth_files)
        with self._record_lock:
            if self._early_site_bootstrap is None:
                self._early_site_bootstrap = state

    def _set_frozen_bootstrap(self, integration: str, path: str, boundary: str) -> None:
        """Attach frozen-application activation provenance to this capture."""
        state = _FrozenBootstrapState(integration, path, boundary)
        with self._record_lock:
            if self._frozen_bootstrap is None:
                self._frozen_bootstrap = state

    def _begin_report_analysis(self) -> tuple[bool, bool]:
        """Make every monitor producer inert during report-time foreign calls."""
        previously_active = self._local.active
        previously_reporting = getattr(self._local, "report_analysis", False)
        self._local.active = True
        self._local.report_analysis = True
        return previously_active, previously_reporting

    def _end_report_analysis(self, previous: tuple[bool, bool]) -> None:
        """Restore this thread's re-entrancy state after report analysis."""
        self._local.active, self._local.report_analysis = previous

    def install(
        self,
        *,
        report_at_exit: bool = True,
        report_destination: "str | PathLike[str] | None" = None,
        report_format: "Literal['text', 'json'] | None" = None,
        report_color: "Literal['auto', 'always', 'never'] | None" = None,
        monitor_path_hooks: bool | None = None,
        monitor_importer_cache: bool | None = None,
        monitor_sys_path: bool | None = None,
        deep: bool | None = None,
        deep_path_hooks: bool | None = None,
        deep_path_entry_finders: bool | None = None,
        deep_loaders: bool | None = None,
        deep_import_outcomes: bool | None = None,
    ) -> None:
        """Instrument ``sys.meta_path`` and register the import audit hook (idempotent).

        A repeat call on an already-enabled monitor changes nothing except
        that ``report_at_exit=True`` still registers the exit report if it is
        not registered yet.

        Args:
            report_at_exit: Register an atexit callback for the resolved output.
            report_destination: Automatic report path. When omitted, consult
                ``METAPATHOLOGY_REPORT`` and then default to stderr.
            report_format: ``"text"`` or ``"json"``. When omitted, consult
                ``METAPATHOLOGY_REPORT_FORMAT`` and default according to the
                destination (text for stderr, JSON for a file).
            report_color: ``"auto"``, ``"always"``, or ``"never"`` for
                automatic text reports. When omitted, consult
                ``METAPATHOLOGY_COLOR`` and default to ``"auto"``.
            monitor_path_hooks: Instrument and observe ``sys.path_hooks``.
                A later true value enables the mechanism; false never disables
                an already-active mechanism.
            monitor_importer_cache: Passively observe
                ``sys.path_importer_cache``. A later true value enables the
                mechanism; false never disables an already-active mechanism.
            monitor_sys_path: Instrument and observe ``sys.path``. This is
                opt-in unless all deep diagnostics are enabled.
            deep: Enable every deep mechanism not explicitly configured.
            deep_path_hooks: Replace path hooks with exact-delegating call wrappers.
            deep_path_entry_finders: Shadow mutable path-entry finder calls.
            deep_loaders: Shadow mutable loader lifecycle methods.
            deep_import_outcomes: Profile CPython's complete import boundary.
        """
        deep = self._resolve_bool(deep, _DEEP_ENV, False)
        monitor_path_hooks = self._resolve_bool(monitor_path_hooks, _MONITOR_PATH_HOOKS_ENV, True)
        monitor_importer_cache = self._resolve_bool(monitor_importer_cache, _MONITOR_IMPORTER_CACHE_ENV, True)
        monitor_sys_path = self._resolve_bool(monitor_sys_path, _MONITOR_SYS_PATH_ENV, deep)
        deep_path_hooks = self._resolve_bool(deep_path_hooks, _DEEP_PATH_HOOKS_ENV, deep)
        deep_path_entry_finders = self._resolve_bool(deep_path_entry_finders, _DEEP_PATH_ENTRY_FINDERS_ENV, deep)
        deep_loaders = self._resolve_bool(deep_loaders, _DEEP_LOADERS_ENV, deep)
        deep_import_outcomes = self._resolve_bool(deep_import_outcomes, _DEEP_IMPORT_OUTCOMES_ENV, deep)
        with self._reinstall_lock:
            was_enabled = self._enabled
            # Validate explicit output configuration before touching import
            # state so a caller error cannot leave a partially installed monitor.
            if not was_enabled:
                self._configure_report(report_destination, report_format, report_color, use_environment=True)
            elif report_destination is not None or report_format is not None or report_color is not None:
                self._configure_report(report_destination, report_format, report_color, use_environment=False)
            if not self._enabled:
                if _IMPLEMENTATION_NAME != "cpython":
                    warnings.warn(_UNSUPPORTED_IMPLEMENTATION_WARNING, RuntimeWarning, stacklevel=3)
                self._enabled = True
                self._baseline_modules = frozenset(sys.modules)
                current = sys.meta_path
                self._initial_meta_path = tuple(type_name(f) for f in current)
                instrumented = _InstrumentedMetaPath(current, self)
                # Instrumenting a finder runs foreign code (its find_spec
                # attribute access can import); on a reinstall the audit hook
                # is already live and would see a stale self._instrumented and
                # deadlock re-entering _reinstall_lock. The active flag makes
                # the hook a no-op on this thread, like every other caller of
                # _instrument_finder.
                self._local.active = True
                try:
                    for position, finder in enumerate(list(instrumented)):
                        self._observe_finder_contract(finder, position, "install", None)
                        self._instrument_finder(finder)
                finally:
                    self._local.active = False
                self._instrumented = instrumented
                sys.meta_path = instrumented
                if not self._audit_installed:
                    # Audit hooks are irremovable; _audit goes inert when disabled.
                    sys.addaudithook(self._audit)
                    self._audit_installed = True
            if monitor_path_hooks and not self._path_hooks_enabled:
                self._enable_path_hooks()
            if monitor_importer_cache and not self._importer_cache_enabled:
                self._enable_importer_cache()
            if monitor_sys_path and not self._sys_path_enabled:
                self._enable_sys_path()
            if deep_path_entry_finders:
                self._deep_path_entry_finders = True
                for path, finder in list(sys.path_importer_cache.items()):
                    if finder is not None:
                        self._instrument_deep_path_entry_finder(finder, path if type(path) is str else None)
            if deep_loaders:
                self._deep_loaders = True
            if deep_path_hooks and not self._deep_path_hooks:
                self._enable_deep_path_hooks()
            if deep_import_outcomes and not self._deep_import_outcomes:
                self._enable_deep_import_outcomes()
        if report_at_exit and not self._report_at_exit:
            self._report_at_exit = True
            atexit.register(self._report_atexit)

    def _resolve_bool(self, explicit: bool | None, environment_name: str, default: bool) -> bool:
        """Resolve one explicit/environment/default boolean without raising."""
        if explicit is not None:
            return explicit
        raw = os.environ.get(environment_name)
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in _TRUE_ENV_VALUES:
            return True
        if normalized in _FALSE_ENV_VALUES:
            return False
        self._record_internal_error(f"environment_configuration.{environment_name}", ValueError())
        return default

    def _configure_report(
        self,
        destination: "str | PathLike[str] | None",
        format: "Literal['text', 'json'] | None",
        color: "Literal['auto', 'always', 'never'] | None",
        *,
        use_environment: bool,
    ) -> None:
        """Resolve explicit and environment-backed automatic report settings."""
        resolved_destination: str | None
        if destination is not None:
            path = os.fspath(destination)
            if not isinstance(path, str):
                raise TypeError("report destinations must resolve to str, not bytes")
            resolved_destination = path
        elif use_environment:
            environment_path = os.environ.get(_REPORT_DESTINATION_ENV)
            resolved_destination = environment_path or None
        else:
            resolved_destination = self._report_destination

        resolved_format: str | None = format
        from_environment = False
        if resolved_format is None and use_environment:
            resolved_format = os.environ.get(_REPORT_FORMAT_ENV) or None
            from_environment = resolved_format is not None
        if resolved_format is None:
            resolved_format = "json" if resolved_destination is not None else "text"
        try:
            validated_format = _report.validate_format(resolved_format)
        except ValueError as exc:
            if not from_environment:
                raise
            validated_format: Literal["text", "json"] = "json" if resolved_destination is not None else "text"
            self._record_internal_error("report_configuration", exc)

        resolved_color: str | None = color
        color_from_environment = False
        if resolved_color is None and use_environment:
            resolved_color = os.environ.get(_REPORT_COLOR_ENV) or None
            color_from_environment = resolved_color is not None
        if resolved_color is None:
            resolved_color = "auto" if use_environment else self._report_color
        try:
            validated_color = _report.validate_color(resolved_color)
        except ValueError as exc:
            if not color_from_environment:
                raise
            validated_color: Literal["auto", "always", "never"] = "auto"
            self._record_internal_error(f"environment_configuration.{_REPORT_COLOR_ENV}", exc)
        self._report_destination = resolved_destination
        self._report_format = validated_format
        self._report_color = validated_color

    def _enable_path_hooks(self) -> None:
        """Install the T1 list around the current ``sys.path_hooks`` contents."""
        contents = list(sys.path_hooks)
        initial = tuple(_import_object_ref(hook) for hook in contents)
        instrumented = _InstrumentedPathHooks(contents, self)
        with self._record_lock:
            self._observed_path_hooks.update((reference.object_id, hook) for reference, hook in zip(initial, contents))
        self._initial_path_hooks = initial
        self._instrumented_path_hooks = instrumented
        self._path_hooks_enabled = True
        sys.path_hooks = instrumented

    def _enable_sys_path(self) -> None:
        """Install the opt-in mutation observer around current ``sys.path``."""
        instrumented = _InstrumentedSysPath(sys.path, self)
        self._instrumented_sys_path = instrumented
        self._sys_path_enabled = True
        sys.path = instrumented

    def _enable_deep_import_outcomes(self) -> None:
        """Install a reversible profiler for the captured CPython import boundary."""
        if sys.getprofile() is not None or threading.getprofile() is not None:
            self._deep_import_outcomes_status = "refused_existing_profiler"
            self._standard_finder_status = "unavailable_existing_profiler"
            return
        boundary = getattr(_importlib_bootstrap, "_find_and_load", None)
        code = getattr(boundary, "__code__", None)
        if not isinstance(code, types.CodeType):
            self._deep_import_outcomes_status = "unsupported_boundary"
            return
        self._deep_import_code = code
        path_finder = getattr(PathFinder.find_spec, "__func__", None)
        path_finder_code = getattr(path_finder, "__code__", None)
        self._path_finder_code = path_finder_code if isinstance(path_finder_code, types.CodeType) else None
        self._standard_finder_status = (
            "active_path_finder_aggregate" if self._path_finder_code is not None else "unsupported_path_finder_boundary"
        )
        self._deep_import_outcomes = True
        self._deep_import_outcomes_status = "active_current_and_future_threading_threads_cache_hits_not_observed"
        threading.setprofile(self._profile_import_boundary)
        sys.setprofile(self._profile_import_boundary)

    def _profile_import_boundary(self, frame: "FrameType", event: str, arg: object) -> None:
        """Record paired entry and completion for the captured ``_find_and_load`` code."""
        if not self._enabled or not self._deep_import_outcomes or getattr(self._local, "report_analysis", False):
            return
        try:
            if frame.f_code is self._path_finder_code:
                self._profile_path_finder(frame, event, arg)
            elif frame.f_code is self._deep_import_code and event == "call":
                fullname = frame.f_locals.get("name")
                if type(fullname) is not str:
                    return
                thread_name = threading.current_thread().name
                pending = getattr(self._local, "pending_import_attempt", None)
                self._local.pending_import_attempt = None
                with self._record_lock:
                    thread_id = self._thread_id_locked()
                    if pending is not None and pending[1] == fullname:
                        attempt_id = pending[0]
                    else:
                        self._attempt_id += 1
                        attempt_id = self._attempt_id
                    self._seq += 1
                    self._events.append(
                        DeepImportEvent(self._seq, attempt_id, fullname, "started", thread_id, thread_name)
                    )
                stack = getattr(self._deep_local, "import_attempts", ())
                self._deep_local.import_attempts = (*stack, (attempt_id, fullname))
            elif frame.f_code is self._deep_import_code and event == "return":
                stack = getattr(self._deep_local, "import_attempts", ())
                if not stack:
                    return
                attempt_id, fullname = stack[-1]
                self._deep_local.import_attempts = stack[:-1]
                outcome = "failed" if arg is None else "loaded"
                thread_name = threading.current_thread().name
                with self._record_lock:
                    thread_id = self._thread_id_locked()
                    self._seq += 1
                    self._events.append(
                        DeepImportEvent(self._seq, attempt_id, fullname, outcome, thread_id, thread_name)
                    )
        except BaseException as exc:
            self._record_internal_error("deep_import_outcomes", exc)

    def _profile_path_finder(self, frame: "FrameType", event: str, arg: object) -> None:
        """Capture successful aggregate ``PathFinder`` results by code identity."""
        if event == "call":
            fullname = frame.f_locals.get("fullname")
            attempts = getattr(self._deep_local, "import_attempts", ())
            if type(fullname) is str and attempts:
                attempt_id, attempt_name = attempts[-1]
                if fullname == attempt_name:
                    stack = getattr(self._deep_local, "path_finder_calls", ())
                    self._deep_local.path_finder_calls = (*stack, (attempt_id, fullname))
            return
        if event != "return":
            return
        stack = getattr(self._deep_local, "path_finder_calls", ())
        if not stack:
            return
        attempt_id, fullname = stack[-1]
        self._deep_local.path_finder_calls = stack[:-1]
        if arg is None:
            return
        summary, _loader = summarize_spec(arg, iterate_foreign_locations=False)
        thread_name = threading.current_thread().name
        with self._record_lock:
            thread_id = self._thread_id_locked()
            self._seq += 1
            self._events.append(
                StandardFinderCall(
                    self._seq,
                    attempt_id,
                    fullname,
                    "PathFinder",
                    summary,
                    thread_id,
                    thread_name,
                )
            )

    def _enable_importer_cache(self) -> None:
        """Capture the T2 baseline without replacing the interpreter cache."""
        self._importer_cache_enabled = True
        self._observe_importer_cache("install", initial=True)

    def _enable_deep_path_hooks(self) -> None:
        """Replace current path hooks with wrappers only after explicit opt-in."""
        self._deep_path_hooks = True
        for index, hook in enumerate(list(sys.path_hooks)):
            wrapper = self._make_deep_path_hook(hook)
            self._deep_hook_wrappers[id(wrapper)] = (wrapper, hook)
            list.__setitem__(sys.path_hooks, index, wrapper)

    def _make_deep_path_hook(self, hook: "_PathHook") -> "_PathHook":
        """Return an exact-delegating path-hook wrapper.

        The wrapper compares equal to (and hashes as) the hook it shadows so
        that third parties scanning ``sys.path_hooks`` by equality still find
        their own hook. PyInstaller's ``PyiFrozenFinder.fallback_finder`` does
        exactly this (``hook == self.path_hook``) to locate the hooks after its
        own and build a fallback ``FileFinder`` for on-disk extension modules;
        a wrapper that did not compare equal silently disabled that fallback.
        """
        return _DeepPathHook(self, hook)

    def _instrument_deep_path_entry_finder(self, finder: object, path: str | None = None) -> None:
        """Shadow one mutable path-entry finder's find_spec when requested."""
        if not self._deep_path_entry_finders or id(finder) in self._deep_finder_patches:
            return
        try:
            instance_dict = finder.__dict__
            original = getattr(finder, "find_spec")
            previous = instance_dict.get("find_spec", _MISSING)
            finder_id = id(finder)
            finder_name = type_name(finder)

            @functools.wraps(original, assigned=(), updated=())
            def wrapped(fullname: str, target: object = None) -> object:
                if not self._enabled or getattr(self._local, "report_analysis", False):
                    return original(fullname, target)
                target_state = None if target is None else ModuleCacheState("object", id(target), type_name(target))
                if self._deep_local.active:
                    self._record_deep_call(
                        "path_entry_finder",
                        finder_id,
                        finder_name,
                        fullname,
                        path,
                        "unobserved_reentrant",
                        None,
                        target_state=target_state,
                    )
                    return original(fullname, target)
                self._deep_local.active = True
                try:
                    try:
                        spec = original(fullname, target)
                    except BaseException as exc:
                        self._record_deep_call(
                            "path_entry_finder",
                            finder_id,
                            finder_name,
                            fullname,
                            path,
                            "raised",
                            type(exc).__name__,
                            target_state=target_state,
                        )
                        raise
                    self._record_deep_call(
                        "path_entry_finder",
                        finder_id,
                        finder_name,
                        fullname,
                        path,
                        "found" if spec is not None else "not_found",
                        None,
                        target_state=target_state,
                    )
                    if spec is not None:
                        self._instrument_deep_loader(safe_spec_loader(spec))
                    return spec
                finally:
                    self._deep_local.active = False

            instance_dict["find_spec"] = wrapped
            self._deep_finder_patches[id(finder)] = (finder, previous)
        except Exception as exc:
            self._record_internal_error("deep_path_entry_finder", exc)

    def _instrument_deep_loader(self, loader: object) -> None:
        """Shadow one mutable loader's modern lifecycle methods when requested."""
        if loader is None or not self._deep_loaders or id(loader) in self._deep_loader_patches:
            return
        patches: list[tuple[str, object]] = []
        instance_dict: dict[str, object] | None = None
        try:
            raw_instance_dict = object.__getattribute__(loader, "__dict__")
            if type(raw_instance_dict) is not dict:
                raise TypeError("loader instance dictionary is unavailable")
            instance_dict = raw_instance_dict
            loader_id = id(loader)
            loader_name = type_name(loader)

            try:
                original_create = getattr(loader, "create_module")
            except AttributeError:
                original_create = None
            if callable(original_create):
                previous_create = instance_dict.get("create_module", _MISSING)

                @functools.wraps(original_create, assigned=(), updated=())
                def wrapped_create(spec: object) -> object:
                    fullname = safe_spec_name(spec)
                    if not self._enabled or getattr(self._local, "report_analysis", False):
                        return original_create(spec)
                    if self._deep_local.active:
                        self._record_deep_call(
                            "loader_create_module",
                            loader_id,
                            loader_name,
                            fullname,
                            None,
                            "unobserved_reentrant",
                            None,
                        )
                        return original_create(spec)
                    state_before = None if fullname is None else module_cache_state(sys.modules, fullname)
                    self._deep_local.active = True
                    try:
                        try:
                            result = original_create(spec)
                        except BaseException as exc:
                            state_after = None if fullname is None else module_cache_state(sys.modules, fullname)
                            self._record_deep_call(
                                "loader_create_module",
                                loader_id,
                                loader_name,
                                fullname,
                                None,
                                "raised",
                                type(exc).__name__,
                                state_before,
                                state_after,
                            )
                            raise
                        state_after = None if fullname is None else module_cache_state(sys.modules, fullname)
                        self._record_deep_call(
                            "loader_create_module",
                            loader_id,
                            loader_name,
                            fullname,
                            None,
                            "returned",
                            None,
                            state_before,
                            state_after,
                        )
                        return result
                    finally:
                        self._deep_local.active = False

                instance_dict["create_module"] = wrapped_create
                patches.append(("create_module", previous_create))

            try:
                original_exec = getattr(loader, "exec_module")
            except AttributeError:
                original_exec = None
            if callable(original_exec):
                previous_exec = instance_dict.get("exec_module", _MISSING)

                @functools.wraps(original_exec, assigned=(), updated=())
                def wrapped_exec(module: object) -> object:
                    fullname = safe_module_name(module)
                    target_state = ModuleCacheState("object", id(module), type_name(module))
                    if not self._enabled or getattr(self._local, "report_analysis", False):
                        return original_exec(module)
                    if self._deep_local.active:
                        self._record_deep_call(
                            "loader_exec_module",
                            loader_id,
                            loader_name,
                            fullname,
                            None,
                            "unobserved_reentrant",
                            None,
                            target_state=target_state,
                        )
                        return original_exec(module)
                    state_before = None if fullname is None else module_cache_state(sys.modules, fullname)
                    self._deep_local.active = True
                    try:
                        try:
                            result = original_exec(module)
                        except BaseException as exc:
                            state_after = None if fullname is None else module_cache_state(sys.modules, fullname)
                            self._record_deep_call(
                                "loader_exec_module",
                                loader_id,
                                loader_name,
                                fullname,
                                None,
                                "raised",
                                type(exc).__name__,
                                state_before,
                                state_after,
                                target_state,
                            )
                            raise
                        state_after = None if fullname is None else module_cache_state(sys.modules, fullname)
                        self._record_deep_call(
                            "loader_exec_module",
                            loader_id,
                            loader_name,
                            fullname,
                            None,
                            "returned",
                            None,
                            state_before,
                            state_after,
                            target_state,
                        )
                        return result
                    finally:
                        self._deep_local.active = False

                instance_dict["exec_module"] = wrapped_exec
                patches.append(("exec_module", previous_exec))

            if patches:
                self._deep_loader_patches[id(loader)] = (loader, tuple(patches))
        except BaseException as exc:
            if instance_dict is not None:
                for name, previous in reversed(patches):
                    if previous is _MISSING:
                        instance_dict.pop(name, None)
                    else:
                        instance_dict[name] = previous
            self._record_internal_error("deep_loader", exc)

    def _record_deep_call(
        self,
        boundary: str,
        object_id: int,
        object_type_name: str,
        fullname: str | None,
        path: str | None,
        outcome: str,
        exception_type_name: str | None,
        module_state_before: ModuleCacheState | None = None,
        module_state_after: ModuleCacheState | None = None,
        target_state: ModuleCacheState | None = None,
    ) -> None:
        """Append primitive-only deep evidence without holding a lock across delegation."""
        thread_name = threading.current_thread().name
        with self._record_lock:
            thread_id = self._thread_id_locked()
            self._seq += 1
            self._events.append(
                DeepDiagnosticCall(
                    self._seq,
                    boundary,
                    object_id,
                    object_type_name,
                    fullname,
                    path,
                    outcome,
                    exception_type_name,
                    thread_name,
                    thread_id,
                    module_state_before,
                    module_state_after,
                    target_state,
                )
            )

    def _thread_id_locked(self) -> int:
        """Return this thread's monitor identity while ``_record_lock`` is held."""
        thread_id = getattr(self._local, "thread_id", None)
        if thread_id is None:
            self._thread_id += 1
            thread_id = self._thread_id
            self._local.thread_id = thread_id
        return thread_id

    def uninstall(self) -> None:
        """Restore plain import lists and unshadow all finders (idempotent)."""
        with self._reinstall_lock:
            if not self._enabled:
                return
            self._observe_importer_cache("uninstall")
            self._enabled = False
            if self._deep_import_outcomes:
                sys.setprofile(None)
                threading.setprofile(None)
                self._deep_import_outcomes = False
                self._deep_import_code = None
                self._path_finder_code = None
                self._deep_import_outcomes_status = "inactive_after_uninstall"
                self._standard_finder_status = "inactive_after_uninstall"
            current = sys.meta_path
            if isinstance(current, _InstrumentedMetaPath):
                sys.meta_path = list(current)
            self._instrumented = None
            if self._path_hooks_enabled:
                try:
                    sys.path_hooks = list(sys.path_hooks)
                except Exception as exc:
                    self._record_internal_error("uninstall_path_hooks", exc)
                self._path_hooks_enabled = False
                self._instrumented_path_hooks = None
            if self._sys_path_enabled:
                try:
                    sys.path = list(sys.path)
                except Exception as exc:
                    self._record_internal_error("uninstall_sys_path", exc)
                self._sys_path_enabled = False
                self._instrumented_sys_path = None
            for finder, previous in list(self._deep_finder_patches.values()):
                self._restore_attribute(finder, "find_spec", previous)
            for loader, patches in list(self._deep_loader_patches.values()):
                for name, previous in reversed(patches):
                    self._restore_attribute(loader, name, previous)
            if self._deep_hook_wrappers:
                try:
                    originals = self._deep_hook_wrappers
                    restored = [originals.get(id(hook), (None, hook))[1] for hook in list(sys.path_hooks)]
                    sys.path_hooks = _cast("list[_PathHook]", restored)
                except Exception as exc:
                    self._record_internal_error("uninstall_deep_path_hooks", exc)
            self._deep_hook_wrappers.clear()
            self._deep_finder_patches.clear()
            self._deep_loader_patches.clear()
            self._deep_path_hooks = False
            self._deep_path_entry_finders = False
            self._deep_loaders = False
            for finder, _original, previous in list(self._patched.values()):
                self._restore_find_spec(finder, previous)
            with self._record_lock:
                self._patched.clear()
                self._skipped.clear()
                self._finder_contracts.clear()
                self._search_path_cache.clear()
                self._observed_path_hooks.clear()
                self._observed_cache_finders.clear()
                self._importer_cache_enabled = False
                self._importer_cache_observation_active = False
        if self._report_at_exit:
            self._report_at_exit = False
            atexit.unregister(self._report_atexit)

    @staticmethod
    def _restore_attribute(obj: object, name: str, previous: object) -> None:
        """Restore an instance-dict shadow without invoking foreign setters."""
        try:
            instance_dict = object.__getattribute__(obj, "__dict__")
            if type(instance_dict) is not dict:
                return
            if previous is _MISSING:
                del instance_dict[name]
            else:
                instance_dict[name] = previous
        except BaseException:
            pass

    def _audit(self, event: str, args: tuple[object, ...]) -> None:
        """Record resolution starts and detect direct import-list reassignment.

        Audit hooks are irremovable, so this must stay a cheap no-op once the
        monitor is disabled. Exceptions are diverted into the event log because
        an exception escaping an audit hook would abort the user's import.

        Args:
            event: Audit event name; everything but ``"import"`` is ignored.
            args: Audit event arguments; for ``import`` the first item is the
                name of the module being resolved.
        """
        if event != "import" or not self._enabled:
            return
        if self._local.active:
            return
        self._local.active = True
        try:
            fullname = _audit_resolution_name(args)
            if fullname is None:
                self._check_importer_cache_fingerprint()
            else:
                current_meta_path = sys.meta_path
                meta_path_id = id(current_meta_path)
                meta_path_type_names = tuple(type_name(finder) for finder in list(current_meta_path))
                path_hooks_id = id(sys.path_hooks) if self._path_hooks_enabled else None
                cache_fingerprint: tuple[int, int] | None = None
                if self._importer_cache_enabled:
                    cache = sys.path_importer_cache
                    cache_fingerprint = (id(cache), len(cache))
                self._record_import_audit_start(
                    fullname,
                    meta_path_id,
                    meta_path_type_names,
                    path_hooks_id,
                    cache_fingerprint,
                )
            meta_path_current = sys.meta_path is self._instrumented
            path_hooks_current = not self._path_hooks_enabled or sys.path_hooks is self._instrumented_path_hooks
            sys_path_current = not self._sys_path_enabled or sys.path is self._instrumented_sys_path
            if meta_path_current and path_hooks_current and sys_path_current:
                return
            self._recover_reassignments(args)
        except Exception as exc:
            # An exception escaping an audit hook aborts the user's import.
            self._record_internal_error("audit_hook", exc)
        finally:
            self._local.active = False

    def _record_import_audit_start(
        self,
        fullname: str,
        meta_path_id: int,
        meta_path_type_names: tuple[str, ...],
        path_hooks_id: int | None,
        cache_fingerprint: tuple[int, int] | None,
    ) -> None:
        """Append one plain-data audit record and update T2's cheap fingerprint."""
        thread_name = threading.current_thread().name
        importer_cache_id = None if cache_fingerprint is None else cache_fingerprint[0]
        importer_cache_size = None if cache_fingerprint is None else cache_fingerprint[1]
        with self._record_lock:
            if cache_fingerprint is not None and cache_fingerprint != self._importer_cache_fingerprint:
                self._importer_cache_fingerprint = cache_fingerprint
                self._importer_cache_dirty = True
            thread_id = self._thread_id_locked()
            self._attempt_id += 1
            attempt_id = self._attempt_id
            self._seq += 1
            self._events.append(
                ImportAuditStart(
                    seq=self._seq,
                    attempt_id=attempt_id,
                    fullname=fullname,
                    meta_path_id=meta_path_id,
                    meta_path_type_names=meta_path_type_names,
                    path_hooks_id=path_hooks_id,
                    importer_cache_id=importer_cache_id,
                    importer_cache_size=importer_cache_size,
                    thread_name=thread_name,
                    thread_id=thread_id,
                )
            )
        if self._deep_import_outcomes:
            self._local.pending_import_attempt = (attempt_id, fullname)

    def _check_importer_cache_fingerprint(self) -> None:
        """Mark T2 dirty using only dictionary identity and length."""
        if not self._importer_cache_enabled:
            return
        cache = sys.path_importer_cache
        current = (id(cache), len(cache))
        with self._record_lock:
            if current != self._importer_cache_fingerprint:
                self._importer_cache_fingerprint = current
                self._importer_cache_dirty = True

    def _observe_importer_cache(self, observation: str, *, initial: bool = False) -> None:
        """Take one passive full snapshot and append a diff when it changed."""
        with self._record_lock:
            if not self._enabled or not self._importer_cache_enabled:
                return
            if self._importer_cache_observation_active:
                self._importer_cache_coalesced += 1
                self._importer_cache_dirty = True
                return
            self._importer_cache_observation_active = True
            # Audit events or another boundary during the copy set this back
            # to true, preserving the need for a later observation.
            self._importer_cache_dirty = False
        try:
            previously_active = self._local.active
            self._local.active = True
            try:
                snapshot, non_string_keys, finders, fingerprint = _snapshot_importer_cache()
            finally:
                self._local.active = previously_active
        except Exception as exc:
            with self._record_lock:
                self._importer_cache_observation_active = False
                self._importer_cache_dirty = True
            self._record_internal_error("importer_cache_snapshot", exc)
            return

        thread_name = threading.current_thread().name
        with self._record_lock:
            self._importer_cache_observation_active = False
            if not self._enabled or not self._importer_cache_enabled:
                return
            before = self._latest_importer_cache
            before_non_string = self._latest_importer_cache_non_string_keys
            self._latest_importer_cache = snapshot
            self._latest_importer_cache_non_string_keys = non_string_keys
            self._importer_cache_fingerprint = fingerprint
            self._importer_cache_observations += 1
            self._observed_cache_finders.update(finders)
            if initial or before is None:
                self._initial_importer_cache = _cache_entries(snapshot)
                self._initial_importer_cache_non_string_keys = non_string_keys
                return
            added, removed, replaced = _diff_importer_cache(before, snapshot)
            if not added and not removed and not replaced and before_non_string == non_string_keys:
                return
            self._seq += 1
            self._events.append(
                ImporterCacheDiff(
                    seq=self._seq,
                    observation=observation,
                    added=added,
                    removed=removed,
                    replaced=replaced,
                    non_string_keys_before=0 if before_non_string is None else before_non_string,
                    non_string_keys_after=non_string_keys,
                    thread_name=thread_name,
                )
            )

    def _recover_reassignments(self, args: tuple[object, ...]) -> None:
        """Wrap directly replaced import lists and record detection evidence.

        This is a copy-and-swap: a plain list cannot be instrumented in place,
        so the foreign list is left untouched and goes stale. A reassigner that
        kept a reference to its own list and mutates it later no longer
        affects the live ``sys.meta_path``; this is a known, unavoidable
        perturbation (see docs/limitations.md).

        Args:
            args: The ``import`` audit event arguments, used to attribute the
                detection to the import that was in flight.
        """
        meta_data: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        path_hooks_data: tuple[tuple[ImportObjectRef, ...], tuple[ImportObjectRef, ...]] | None = None
        sys_path_data: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        with self._reinstall_lock:
            if not self._enabled:
                return
            current_meta_path = sys.meta_path
            expected_meta_path = self._instrumented
            if current_meta_path is not expected_meta_path and expected_meta_path is not None:
                old_contents = tuple(type_name(finder) for finder in list(expected_meta_path))
                new_contents = tuple(type_name(finder) for finder in list(current_meta_path))
                replacement = _InstrumentedMetaPath(current_meta_path, self)
                for position, finder in enumerate(list(replacement)):
                    self._observe_finder_contract(finder, position, "reassignment", None)
                    self._instrument_finder(finder)
                self._instrumented = replacement
                sys.meta_path = replacement
                meta_data = (old_contents, new_contents)

            current_path_hooks = sys.path_hooks
            expected_path_hooks = self._instrumented_path_hooks
            if (
                self._path_hooks_enabled
                and current_path_hooks is not expected_path_hooks
                and expected_path_hooks is not None
            ):
                old_hooks = tuple(expected_path_hooks)
                new_hooks = tuple(current_path_hooks)
                old_hook_contents = tuple(_import_object_ref(self._original_path_hook(hook)) for hook in old_hooks)
                new_hook_contents = tuple(_import_object_ref(self._original_path_hook(hook)) for hook in new_hooks)
                path_replacement = _InstrumentedPathHooks(new_hooks, self)
                self._instrumented_path_hooks = path_replacement
                sys.path_hooks = path_replacement
                with self._record_lock:
                    self._observed_path_hooks.update((id(hook), hook) for hook in (*old_hooks, *new_hooks))
                path_hooks_data = (old_hook_contents, new_hook_contents)

            current_sys_path = sys.path
            expected_sys_path = self._instrumented_sys_path
            if self._sys_path_enabled and current_sys_path is not expected_sys_path and expected_sys_path is not None:
                old_paths = tuple(_path_item_name(item) for item in list(expected_sys_path))
                new_paths = tuple(_path_item_name(item) for item in list(current_sys_path))
                sys_path_replacement = _InstrumentedSysPath(current_sys_path, self)
                self._instrumented_sys_path = sys_path_replacement
                sys.path = sys_path_replacement
                sys_path_data = (old_paths, new_paths)
        if meta_data is None and path_hooks_data is None and sys_path_data is None:
            return
        fullname = args[0] if args and isinstance(args[0], str) else "<unknown>"
        stack = _capture_stack(sys._getframe())
        thread_name = threading.current_thread().name
        with self._record_lock:
            if meta_data is not None:
                self._seq += 1
                self._events.append(
                    MetaPathReassignment(
                        seq=self._seq,
                        during_import=fullname,
                        old_contents=meta_data[0],
                        new_contents=meta_data[1],
                        thread_name=thread_name,
                        stack=stack,
                    )
                )
            if path_hooks_data is not None:
                self._seq += 1
                self._events.append(
                    PathHooksReassignment(
                        seq=self._seq,
                        during_import=fullname,
                        old_contents=path_hooks_data[0],
                        new_contents=path_hooks_data[1],
                        thread_name=thread_name,
                        stack=stack,
                    )
                )
            if sys_path_data is not None:
                self._seq += 1
                self._events.append(
                    SysPathReassignment(
                        seq=self._seq,
                        during_import=fullname,
                        old_contents=sys_path_data[0],
                        new_contents=sys_path_data[1],
                        thread_name=thread_name,
                        stack=stack,
                    )
                )

    def _instrument_finder(self, finder: object) -> None:
        """Shadow ``finder.find_spec`` with a recording wrapper in the instance dict (layer 3).

        Instance-dict shadowing keeps third-party ``isinstance`` scans of
        ``sys.meta_path`` working. Entries that cannot be shadowed (classes,
        ``__slots__`` instances, objects without ``find_spec``) are remembered
        as skipped and attributed by elimination at report time.

        Args:
            finder: Any ``sys.meta_path`` entry; safe to pass repeatedly.
        """
        finder_id = id(finder)
        with self._record_lock:
            if finder_id in self._patched or finder_id in self._skipped:
                return
            # Claim the id before touching the finder so concurrent callers don't double-wrap.
            self._patched[finder_id] = (finder, None, _MISSING)
        if isinstance(finder, type):
            # Class entries (PathFinder and friends) are shared stdlib state; never mutate them.
            reason = (
                _STANDARD_CLASS_FINDER_REASON
                if finder in _STANDARD_CLASS_FINDERS
                else "class entry; instance-dict shadowing not applicable"
            )
            self._skip_finder(finder_id, finder, reason)
            return
        try:
            original = getattr(finder, "find_spec", None)
        except Exception as exc:
            self._skip_finder(finder_id, finder, "find_spec lookup raised")
            self._record_internal_error("instrument_finder", exc)
            return
        if not callable(original):
            self._skip_finder(finder_id, finder, "no callable find_spec")
            return
        typed_original = _cast("_FindSpec", original)
        try:
            # Deliberately bypass custom __getattribute__ only for __dict__:
            # finder.__dict__ could import, raise, or re-enter us while merely
            # preparing reversible cleanup. The find_spec lookup and write use
            # normal getattr/setattr above and below, preserving descriptor and
            # custom attribute semantics that affect actual finder calls.
            instance_dict = object.__getattribute__(finder, "__dict__")
            previous = instance_dict.get("find_spec", _MISSING)
        except (AttributeError, TypeError):
            instance_dict = None
            previous = _MISSING
        with self._record_lock:
            # The foreign attribute lookups above can block arbitrarily long
            # (imports, __getattr__); a vanished claim means uninstall() ran
            # meanwhile, so stop before touching the finder. Otherwise refresh
            # the claim with the real pre-shadowing value so a concurrent
            # uninstall() restores the same state our own undo would.
            if not self._enabled or finder_id not in self._patched:
                return
            self._patched[finder_id] = (finder, None, previous)
        wrapper = self._make_find_spec_wrapper(type_name(finder), finder_id, typed_original)
        try:
            setattr(finder, "find_spec", wrapper)
        except Exception:
            self._skip_finder(finder_id, finder, "find_spec not settable on the instance")
            return
        try:
            landed = instance_dict is not None and instance_dict.get("find_spec") is wrapper
        except Exception:
            landed = False
        if not landed:
            # setattr succeeded but wrote through a slot or data descriptor,
            # not the instance dict, so uninstall()'s __dict__-based restore
            # could never undo it. Write the original back through the same
            # writable path and leave the finder unwrapped.
            try:
                setattr(finder, "find_spec", typed_original)
            except Exception as exc:
                self._record_internal_error("instrument_finder", exc)
            self._skip_finder(finder_id, finder, "find_spec assignment bypasses the instance dict (slot or descriptor)")
            return
        with self._record_lock:
            committed = self._enabled and finder_id in self._patched
            if committed:
                self._patched[finder_id] = (finder, typed_original, previous)
        if not committed:
            # Lost the race with uninstall(): undo the shadow ourselves, outside
            # _record_lock. Idempotent against uninstall()'s own restore in
            # either order.
            self._restore_find_spec(finder, previous)

    def _skip_finder(self, finder_id: int, finder: object, reason: str) -> None:
        """Mark a finder as uninstrumentable, releasing any claim taken by ``_instrument_finder``.

        Args:
            finder_id: ``id(finder)``, the claim key.
            finder: The entry itself; kept referenced so its id stays unique.
            reason: Human-readable explanation shown in the report.
        """
        with self._record_lock:
            self._patched.pop(finder_id, None)
            # After uninstall() cleared _skipped, a late skip must not
            # repopulate it; just release the claim.
            if self._enabled:
                self._skipped[finder_id] = (finder, reason)

    def _observe_finder_contract(
        self,
        finder: object,
        position: int,
        observation: str,
        observation_seq: int | None,
    ) -> None:
        """Inventory raw finder protocols without invoking attribute lookup."""
        finder_id = id(finder)
        with self._record_lock:
            if finder_id in self._finder_contracts:
                return
        contract = FinderContract(
            finder_id=finder_id,
            finder_type_name=type_name(finder),
            position=position,
            observation=observation,
            observation_seq=observation_seq,
            find_spec=_inspect_finder_protocol(finder, "find_spec"),
            find_module=_inspect_finder_protocol(finder, "find_module"),
        )
        with self._record_lock:
            if self._enabled:
                self._finder_contracts.setdefault(finder_id, contract)

    @staticmethod
    def _restore_find_spec(finder: object, previous: object) -> None:
        """Reset a finder's instance-dict ``find_spec`` to its pre-shadowing value.

        Idempotent: ``uninstall()`` and a racing ``_instrument_finder`` may both
        call this for the same finder, in either order, converging on the same
        end state. Tolerates the entry already being gone (or the finder having
        no ``__dict__`` at all, as with in-progress claims).

        Args:
            finder: The shadowed (or claimed) finder.
            previous: The prior instance-dict value, or ``_MISSING`` if absent.
        """
        try:
            # Match installation's raw-dict access: cleanup must remove the
            # exact shadow even if custom attribute dispatch is hostile or has
            # changed since installation.
            instance_dict = object.__getattribute__(finder, "__dict__")
            if previous is _MISSING:
                del instance_dict["find_spec"]
            else:
                instance_dict["find_spec"] = previous
        except (AttributeError, KeyError, TypeError):
            pass

    def _make_find_spec_wrapper(self, finder_type_name: str, finder_id: int, original: "_FindSpec") -> "_FindSpec":
        """Build the ``find_spec`` replacement that records each call and delegates to the original.

        Args:
            finder_type_name: Display name captured once while instrumenting
                the finder.
            finder_id: ``id()`` captured once while instrumenting the finder.
            original: The bound ``find_spec`` to delegate to.

        Returns:
            A plain function suitable for the finder's instance dict; functions
            stored there are not bound, so it takes no ``self``.
        """

        # Exact Python functions and bound methods expose interpreter-owned
        # metadata, so the usual wraps behavior is safe and preserves normal
        # introspection. An arbitrary callable can implement every metadata
        # attribute dynamically; for those, retain only the safely assigned
        # __wrapped__ link used by inspect.unwrap/signature.
        copy_metadata = isinstance(original, (types.FunctionType, types.MethodType))
        assigned = functools.WRAPPER_ASSIGNMENTS if copy_metadata else ()
        updated = functools.WRAPPER_UPDATES if copy_metadata else ()

        @functools.wraps(original, assigned=assigned, updated=updated)
        def find_spec(
            fullname: str,
            path: "Sequence[str] | None" = None,
            target: "ModuleType | None" = None,
        ) -> "ModuleSpec | None":
            # The _enabled check makes any wrapper that briefly outlives
            # uninstall() (instrumentation racing uninstall) delegate silently.
            if self._local.active or not self._enabled:
                return original(fullname, path, target)
            self._local.active = True
            spec = None
            exception_type_name: str | None = None
            module_state_before = module_cache_state(sys.modules, fullname)
            try:
                search_path = self._snapshot_search_path(path)
                try:
                    spec = original(fullname, path, target)
                except BaseException as exc:
                    exception_type_name = type(exc).__name__
                    raise
                finally:
                    module_state_after = module_cache_state(sys.modules, fullname)
                    self._record_find_spec(
                        finder_type_name,
                        finder_id,
                        fullname,
                        spec,
                        search_path,
                        "sys_path" if path is None else "parent_path",
                        exception_type_name,
                        module_state_before,
                        module_state_after,
                        None if target is None else ModuleCacheState("object", id(target), type_name(target)),
                    )
            finally:
                self._local.active = False
            return spec

        return find_spec

    @staticmethod
    def _snapshot_search_path(path: "Sequence[str] | None") -> tuple[str, ...]:
        """Copy the effective finder path while the import is in progress."""
        source = sys.path if path is None else path
        try:
            if path is not None:
                return tuple(entry for entry in source if type(entry) is str)
            snapshot: list[str] = []
            for entry in source:
                if type(entry) is not str:
                    continue
                # PathFinder resolves the special empty top-level entry to the
                # cwd before consulting its importer cache. Preserve that
                # import-time meaning if the process changes cwd before report.
                snapshot.append(os.getcwd() if entry == "" else entry)
            return tuple(snapshot)
        except Exception:
            return ()

    def _reuse_search_path(self, snapshot: tuple[str, ...]) -> tuple[str, ...]:
        """Return a recent identity-equal snapshot; caller holds ``_record_lock``."""
        for index, cached in enumerate(self._search_path_cache):
            if len(cached) != len(snapshot):
                continue
            if any(left is not right for left, right in zip(cached, snapshot, strict=True)):
                continue
            # Keep frequently reused paths resident when package imports cycle
            # among several distinct __path__ values.
            self._search_path_cache.append(self._search_path_cache.pop(index))
            return cached
        self._search_path_cache.append(snapshot)
        if len(self._search_path_cache) > _SEARCH_PATH_CACHE_SIZE:
            del self._search_path_cache[0]
        return snapshot

    def _record_find_spec(
        self,
        finder_type_name: str,
        finder_id: int,
        fullname: str,
        spec: "ModuleSpec | None",
        search_path: tuple[str, ...],
        search_path_kind: str,
        exception_type_name: str | None,
        module_state_before: ModuleCacheState,
        module_state_after: ModuleCacheState,
        target_state: ModuleCacheState | None,
    ) -> None:
        """Record one ``find_spec`` call, reducing the spec to primitives before taking the lock.

        Args:
            finder_type_name: Display name captured when the finder was instrumented.
            finder_id: ``id()`` captured when the finder was instrumented.
            fullname: The module name that was probed.
            spec: Whatever ``find_spec`` returned; ``None`` means the finder passed.
            search_path: Effective import search path captured before the call.
            exception_type_name: Set when the call raised instead of returning.
        """
        try:
            loader_type_name: str | None = None
            origin: str | None = None
            spec_summary: SpecSummary | None = None
            if spec is not None:
                spec_summary, loader = summarize_spec(spec, iterate_foreign_locations=False)
                if loader is not None:
                    loader_type_name = type_name(loader)
                    self._instrument_deep_loader(loader)
                if type(spec_summary.origin) is str:
                    origin = spec_summary.origin
            found = spec is not None
            thread_name = threading.current_thread().name
            with self._record_lock:
                thread_id = self._thread_id_locked()
                search_path = self._reuse_search_path(search_path)
                self._seq += 1
                self._events.append(
                    FindSpecCall(
                        seq=self._seq,
                        fullname=fullname,
                        finder_type_name=finder_type_name,
                        finder_id=finder_id,
                        found=found,
                        loader_type_name=loader_type_name,
                        origin=origin,
                        search_path=search_path,
                        search_path_kind=search_path_kind,
                        spec_summary=spec_summary,
                        exception_type_name=exception_type_name,
                        thread_name=thread_name,
                        thread_id=thread_id,
                        module_state_before=module_state_before,
                        module_state_after=module_state_after,
                        target_state=target_state,
                    )
                )
        except Exception as exc:
            self._record_internal_error("record_find_spec", exc)

    def _on_meta_path_mutation(
        self,
        mutated: "_InstrumentedMetaPath",
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Record a mutation of the instrumented list and instrument any newly added finders.

        Called by every overridden mutator of ``_InstrumentedMetaPath``.

        Args:
            mutated: The list that was mutated (not necessarily the live
                ``sys.meta_path`` anymore, after a reassignment).
            op: Name of the mutating method, e.g. ``"insert"``.
            added: Finders the mutation added.
            removed: Finders the mutation removed.
            frame: The mutator's caller, where the stack capture starts.
        """
        if self._local.active:
            return
        self._local.active = True
        try:
            if not self._enabled:
                return
            added_names = tuple(type_name(f) for f in added)
            removed_names = tuple(type_name(f) for f in removed)
            contents_after = tuple(type_name(f) for f in list(mutated))
            thread_name = threading.current_thread().name
            stack = _capture_stack(frame)
            with self._record_lock:
                self._seq += 1
                mutation_seq = self._seq
                self._events.append(
                    MetaPathMutation(
                        seq=self._seq,
                        op=op,
                        added=added_names,
                        removed=removed_names,
                        contents_after=contents_after,
                        thread_name=thread_name,
                        stack=stack,
                    )
                )
            positions = {id(finder): position for position, finder in enumerate(list(mutated))}
            for finder in added:
                self._observe_finder_contract(
                    finder,
                    positions.get(id(finder), -1),
                    "mutation",
                    mutation_seq,
                )
                self._instrument_finder(finder)
        except Exception as exc:
            self._record_internal_error("meta_path_mutation", exc)
        finally:
            self._local.active = False

    def _on_path_hooks_mutation(
        self,
        mutated: "_InstrumentedPathHooks",
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Record a ``sys.path_hooks`` mutation without invoking any hook."""
        if self._local.active:
            return
        self._local.active = True
        try:
            if not self._enabled or not self._path_hooks_enabled or mutated is not self._instrumented_path_hooks:
                return
            added_refs = tuple(_import_object_ref(self._original_path_hook(hook)) for hook in added)
            removed_refs = tuple(_import_object_ref(self._original_path_hook(hook)) for hook in removed)
            if self._deep_path_hooks:
                for added_hook in added:
                    if id(added_hook) in self._deep_hook_wrappers:
                        continue
                    wrapper = self._make_deep_path_hook(_cast("_PathHook", added_hook))
                    self._deep_hook_wrappers[id(wrapper)] = (wrapper, added_hook)
                    for index, current in enumerate(mutated):
                        if current is added_hook:
                            list.__setitem__(mutated, index, wrapper)
            contents_after = tuple(_import_object_ref(self._original_path_hook(hook)) for hook in list(mutated))
            observed_hooks = (*added, *removed, *mutated)
            thread_name = threading.current_thread().name
            stack = _capture_stack(frame)
            with self._record_lock:
                # Uninstall may have completed while this callback was
                # reducing plain data. Do not retain hooks or append a late
                # record after cleanup.
                if not self._enabled or not self._path_hooks_enabled:
                    return
                self._observed_path_hooks.update((id(hook), hook) for hook in observed_hooks)
                self._seq += 1
                self._events.append(
                    PathHooksMutation(
                        seq=self._seq,
                        op=op,
                        added=added_refs,
                        removed=removed_refs,
                        contents_after=contents_after,
                        thread_name=thread_name,
                        stack=stack,
                    )
                )
            self._observe_importer_cache("after_path_hooks_mutation")
        except Exception as exc:
            self._record_internal_error("path_hooks_mutation", exc)
        finally:
            self._local.active = False

    def _on_path_hooks_before_mutation(self, mutated: "_InstrumentedPathHooks") -> None:
        """Observe T2 immediately before a live path-hook list operation."""
        if self._local.active:
            return
        self._local.active = True
        try:
            if not self._enabled or not self._path_hooks_enabled or mutated is not self._instrumented_path_hooks:
                return
            self._observe_importer_cache("before_path_hooks_mutation")
        except Exception as exc:
            self._record_internal_error("importer_cache_before_path_hooks_mutation", exc)
        finally:
            self._local.active = False

    def _on_sys_path_mutation(
        self,
        mutated: "_InstrumentedSysPath",
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Record one opt-in ``sys.path`` list mutation using plain values."""
        if self._local.active:
            return
        self._local.active = True
        try:
            if not self._enabled or not self._sys_path_enabled or mutated is not self._instrumented_sys_path:
                return
            added_paths = tuple(_path_item_name(item) for item in added)
            removed_paths = tuple(_path_item_name(item) for item in removed)
            contents_after = tuple(_path_item_name(item) for item in list(mutated))
            thread_name = threading.current_thread().name
            stack = _capture_stack(frame)
            with self._record_lock:
                if not self._enabled or not self._sys_path_enabled:
                    return
                self._seq += 1
                self._events.append(
                    SysPathMutation(
                        self._seq,
                        op,
                        added_paths,
                        removed_paths,
                        contents_after,
                        thread_name,
                        stack,
                    )
                )
        except Exception as exc:
            self._record_internal_error("sys_path_mutation", exc)
        finally:
            self._local.active = False

    def _record_internal_error(self, where: str, exc: BaseException) -> None:
        """Record an exception raised by our own instrumentation instead of letting it escape.

        Args:
            where: Short label of the code path that failed.
            exc: The caught exception. Only its type name is captured; calling
                ``str(exc)`` here could execute foreign code during an import.
        """
        exception_type_name = type(exc).__name__
        with self._record_lock:
            self._seq += 1
            self._events.append(InternalError(seq=self._seq, where=where, exception_type_name=exception_type_name))

    def _report_atexit(self) -> None:
        """Write the configured report without breaking interpreter shutdown."""
        with suppress(Exception):
            self._write_configured_report()

    def _write_configured_report(self) -> None:
        """Write the configured report, adding this process's ID to file paths."""
        with self._reinstall_lock:
            destination = self._report_destination
            format = self._report_format
            color = self._report_color
        if destination is not None:
            destination = _pid_destination(destination, os.getpid())
        _report.write_report(self, destination, format=format, color=color)


class _InstrumentedImportList(list["_ImportListItemT"]):
    """Shared list semantics for instrumented import-system lists.

    A real ``list`` subclass, never a proxy: third-party code that scans
    ``sys.meta_path`` with ``isinstance``, slices it, or iterates it keeps
    working unchanged. It records additions, removals, replacements, clearing,
    and reordering through the normal Python list operations. Mutations made
    from C via ``PyList_*`` bypass these overrides; that is an accepted blind
    spot.
    """

    __slots__ = ("_monitor",)

    def __init__(self, contents: "Iterable[_ImportListItemT]", monitor: Monitor) -> None:
        """Wrap ``contents`` without recording a mutation.

        Args:
            contents: The finders to start with, typically the previous
                ``sys.meta_path``.
            monitor: The monitor that receives mutation callbacks.
        """
        super().__init__(contents)
        self._monitor = monitor

    def append(self, item: "_ImportListItemT") -> None:
        """Delegate to ``list.append`` and record the addition."""
        self._before_mutation(sys._getframe(1))
        super().append(item)
        self._notify("append", (item,), (), sys._getframe(1))

    def insert(self, index: "SupportsIndex", item: "_ImportListItemT") -> None:
        """Delegate to ``list.insert`` and record the addition."""
        self._before_mutation(sys._getframe(1))
        super().insert(index, item)
        self._notify("insert", (item,), (), sys._getframe(1))

    def extend(self, items: "Iterable[_ImportListItemT]") -> None:
        """Delegate to ``list.extend`` and record whatever was actually added.

        ``items`` is passed through unmaterialized so an iterator that raises
        mid-iteration keeps its already-yielded prefix, exactly matching plain
        ``list`` semantics; the partial addition is still recorded before the
        exception propagates.
        """
        self._extend_and_record("extend", items, sys._getframe(1))

    def _extend_and_record(self, op: str, items: "Iterable[_ImportListItemT]", frame: "FrameType") -> None:
        """Shared ``extend``/``__iadd__`` body: extend in place, then record what landed.

        Args:
            op: Recorded operation name, ``"extend"`` or ``"__iadd__"``.
            items: Iterable of finders; may raise mid-iteration.
            frame: The mutator's caller, where the stack capture starts.
        """
        # The post-call slice is best-effort: a concurrent thread could mutate
        # the list in between, but the class is already best-effort against
        # C-level mutation, and locking here would risk import deadlocks.
        self._before_mutation(frame)
        before = len(self)
        completed = False
        try:
            super().extend(items)
            completed = True
        finally:
            added = tuple(self[before:])
            # A successful zero-item extend is still a recorded call; a raise
            # that added nothing left the list unmutated and records nothing.
            if completed or added:
                self._notify(op, added, (), frame)

    def remove(self, item: "_ImportListItemT") -> None:
        """Delegate to ``list.remove`` and record the removal."""
        self._before_mutation(sys._getframe(1))
        super().remove(item)
        self._notify("remove", (), (item,), sys._getframe(1))

    def pop(self, index: "SupportsIndex" = -1) -> "_ImportListItemT":
        """Delegate to ``list.pop`` and record the removal.

        Returns:
            The removed finder, as ``list.pop`` would.
        """
        self._before_mutation(sys._getframe(1))
        item = super().pop(index)
        self._notify("pop", (), (item,), sys._getframe(1))
        return item

    def clear(self) -> None:
        """Record the removal of the entire contents, then delegate to ``list.clear``."""
        self._before_mutation(sys._getframe(1))
        removed = tuple(self)
        super().clear()
        self._notify("clear", (), removed, sys._getframe(1))

    def reverse(self) -> None:
        """Record an order-only mutation; finder precedence changes even though membership does not."""
        self._before_mutation(sys._getframe(1))
        super().reverse()
        self._notify("reverse", (), (), sys._getframe(1))

    def sort(
        self,
        *,
        key: "Callable[[_ImportListItemT], SupportsRichComparison] | None" = None,
        reverse: bool = False,
    ) -> None:
        """Record an order change made with ``list.sort``."""
        self._before_mutation(sys._getframe(1))
        before = tuple(self)
        completed = False
        # Runtime list.sort accepts None; the two type checkers model its
        # overloads incompatibly for an unconstrained list element type.
        try:
            super().sort(key=key, reverse=reverse)  # type: ignore
            completed = True
        finally:
            # CPython may leave a list partially reordered when a comparison
            # raises. The mutation still changed finder precedence and must be
            # reported even though list.sort itself did not return normally.
            changed = len(before) != len(self) or any(old is not new for old, new in zip(before, self))
            if completed or changed:
                self._notify("sort", (), (), sys._getframe(1))

    # TODO(Python >= 3.11): Return typing.Self once Python 3.10 support is dropped.
    def __imul__(self, value: "SupportsIndex") -> "_InstrumentedImportList[_ImportListItemT]":
        """Record an in-place repeat, including additions or a complete wipe."""
        self._before_mutation(sys._getframe(1))
        before = tuple(self)
        super().__imul__(value)
        after = tuple(self)
        added = after[len(before) :] if len(after) > len(before) else ()
        removed = before[len(after) :] if len(after) < len(before) else ()
        self._notify("__imul__", added, removed, sys._getframe(1))
        return self

    def __copy__(self) -> list["_ImportListItemT"]:
        """Match the plain-list interface exposed before instrumentation."""
        return list(self)

    # ``deepcopy``'s public protocol specifies Any for the heterogeneous memo values.
    def __deepcopy__(
        self,
        memo: "dict[int, Any]",  # pyright: ignore[reportExplicitAny]
    ) -> list["_ImportListItemT"]:
        """Deep-copy finders without traversing the monitor's locks and state."""
        return copy.deepcopy(list(self), memo)

    if TYPE_CHECKING:

        @overload
        def __setitem__(self, index: SupportsIndex, value: _ImportListItemT) -> None: ...

        @overload
        def __setitem__(self, index: slice, value: Iterable[_ImportListItemT]) -> None: ...

    def __setitem__(
        self,
        index: "SupportsIndex | slice",
        value: "_ImportListItemT | Iterable[_ImportListItemT]",
    ) -> None:
        """Record single-item or slice replacement (slice assignment is how many tools filter finders)."""
        self._before_mutation(sys._getframe(1))
        if isinstance(index, slice):
            # We inspect the old value before mutating. Normalize custom index
            # objects once so their __index__ methods are not called again by
            # the subsequent list operation, matching plain-list semantics.
            index = slice(
                None if index.start is None else operator.index(index.start),
                None if index.stop is None else operator.index(index.stop),
                None if index.step is None else operator.index(index.step),
            )
            added = tuple(_cast("Iterable[_ImportListItemT]", value))
            removed = tuple(self[index])
            super().__setitem__(index, added)
        else:
            # See the slice case above: the lookup and assignment must share
            # one normalized integer rather than invoking __index__ twice.
            index = operator.index(index)
            item = _cast("_ImportListItemT", value)
            added = (item,)
            removed = (self[index],)
            super().__setitem__(index, item)
        self._notify("__setitem__", added, removed, sys._getframe(1))

    def __delitem__(self, index: "SupportsIndex | slice") -> None:
        """Delegate to ``list.__delitem__`` and record the removed finder(s)."""
        self._before_mutation(sys._getframe(1))
        # Capturing the removed value and then deleting would otherwise invoke
        # a stateful __index__ twice and could observe/delete different items.
        if isinstance(index, slice):
            index = slice(
                None if index.start is None else operator.index(index.start),
                None if index.stop is None else operator.index(index.stop),
                None if index.step is None else operator.index(index.step),
            )
        else:
            index = operator.index(index)
        removed = tuple(self[index]) if isinstance(index, slice) else (self[index],)
        super().__delitem__(index)
        self._notify("__delitem__", (), removed, sys._getframe(1))

    # TODO(Python >= 3.11): Return typing.Self once Python 3.10 support is dropped.
    def __iadd__(self, items: "Iterable[_ImportListItemT]") -> "_InstrumentedImportList[_ImportListItemT]":
        """Record ``+=``, which reaches ``list`` at the C level and would otherwise bypass the ``extend`` override.

        Like :meth:`extend`, ``items`` is not materialized up front, so a
        mid-iteration exception keeps (and records) the already-yielded prefix
        just as a plain ``list`` would.
        """
        self._extend_and_record("__iadd__", items, sys._getframe(1))
        return self

    def _notify(
        self,
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Dispatch one completed mutation to the concrete import-list monitor."""
        raise NotImplementedError

    def _before_mutation(self, frame: "FrameType") -> None:
        """Dispatch a pre-mutation observation when the concrete list needs one."""


class _InstrumentedMetaPath(_InstrumentedImportList["_MetaPathEntry"]):
    """Drop-in ``sys.meta_path`` replacement with finder instrumentation."""

    __slots__ = ()

    def _notify(
        self,
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        self._monitor._on_meta_path_mutation(self, op, added, removed, frame)


class _DeepPathHook:
    """Exact-delegating ``sys.path_hooks`` wrapper for deep path-hook tracing.

    Callable like the hook it shadows, but also forwards equality and hashing
    to that hook so third-party code scanning ``sys.path_hooks`` by ``==``
    (e.g. PyInstaller's ``PyiFrozenFinder.fallback_finder``) still recognizes
    its own hook through the wrapper. Identity (``is``) comparisons cannot be
    intercepted and still see the wrapper — an inherent limitation of shadowing
    at this layer.
    """

    __slots__ = ("__wrapped__", "_hook", "_hook_id", "_hook_name", "_monitor")

    def __init__(self, monitor: "Monitor", hook: "_PathHook") -> None:
        self._monitor = monitor
        self._hook = hook
        self._hook_id = id(hook)
        self._hook_name = type_name(hook)
        # __wrapped__ link used by inspect.unwrap for introspection.
        self.__wrapped__ = hook

    def __call__(self, path: str) -> "PathEntryFinderProtocol":
        monitor = self._monitor
        hook = self._hook
        if not monitor._enabled or getattr(monitor._local, "report_analysis", False):
            return hook(path)
        if monitor._deep_local.active:
            monitor._record_deep_call(
                "path_hook", self._hook_id, self._hook_name, None, path, "unobserved_reentrant", None
            )
            return hook(path)
        monitor._deep_local.active = True
        try:
            try:
                finder = hook(path)
            except BaseException as exc:
                monitor._record_deep_call(
                    "path_hook", self._hook_id, self._hook_name, None, path, "raised", type(exc).__name__
                )
                raise
            monitor._record_deep_call("path_hook", self._hook_id, self._hook_name, None, path, "returned", None)
            monitor._instrument_deep_path_entry_finder(finder, path)
            return finder
        finally:
            monitor._deep_local.active = False

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _DeepPathHook):
            return self._hook == other._hook
        return self._hook == other

    def __hash__(self) -> int:
        return hash(self._hook)


class _InstrumentedPathHooks(_InstrumentedImportList["_PathHook"]):
    """Drop-in ``sys.path_hooks`` replacement that never wraps hook factories."""

    __slots__ = ()

    def _before_mutation(self, frame: "FrameType") -> None:
        self._monitor._on_path_hooks_before_mutation(self)

    def _notify(
        self,
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        self._monitor._on_path_hooks_mutation(self, op, added, removed, frame)


class _InstrumentedSysPath(_InstrumentedImportList[str]):
    """Opt-in drop-in ``sys.path`` replacement with mutation attribution."""

    __slots__ = ()

    def _notify(
        self,
        op: str,
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        self._monitor._on_sys_path_mutation(self, op, added, removed, frame)


# One Monitor per process: the instrumentation targets process-global state
# (sys.meta_path, the audit hook), so multiple instances would fight each other.
_monitor_singleton: Monitor | None = None
_singleton_lock = threading.Lock()


def install(
    *,
    report_at_exit: bool = True,
    report_destination: "str | PathLike[str] | None" = None,
    report_format: "Literal['text', 'json'] | None" = None,
    report_color: "Literal['auto', 'always', 'never'] | None" = None,
    monitor_path_hooks: bool | None = None,
    monitor_importer_cache: bool | None = None,
    monitor_sys_path: bool | None = None,
    deep: bool | None = None,
    deep_path_hooks: bool | None = None,
    deep_path_entry_finders: bool | None = None,
    deep_loaders: bool | None = None,
    deep_import_outcomes: bool | None = None,
) -> Monitor:
    """Install the import-machinery monitor (idempotent) and return it.

    Call as early as possible: only imports and mutations that happen after
    this call are observed.

    Args:
        report_at_exit: Register an atexit callback for the resolved output.
        report_destination: Automatic report path, or None to consult the
            environment and then default to stderr.
        report_format: ``"text"`` or ``"json"``; the destination determines
            the default when neither the API nor environment configures it.
        report_color: ``"auto"``, ``"always"``, or ``"never"`` for the
            automatic text report; None consults ``METAPATHOLOGY_COLOR``.
        monitor_path_hooks: Observe ``sys.path_hooks`` mutations and direct
            reassignment. A later true value enables this mechanism.
        monitor_importer_cache: Passively observe
            ``sys.path_importer_cache``. A later true value enables this
            mechanism.
        monitor_sys_path: Opt in to reversible ``sys.path`` mutation and
            reassignment observation.
        deep: Enable every deep mechanism not explicitly configured.
        deep_path_hooks: Replace path hooks with call-capturing delegates.
        deep_path_entry_finders: Shadow path-entry finder calls as they appear.
        deep_loaders: Shadow modern loader creation and execution reached through captured finders.
        deep_import_outcomes: Observe exact CPython import invocation outcomes.
    """
    global _monitor_singleton
    with _singleton_lock:
        if _monitor_singleton is None:
            _monitor_singleton = Monitor()
        monitor = _monitor_singleton
    monitor.install(
        report_at_exit=report_at_exit,
        report_destination=report_destination,
        report_format=report_format,
        report_color=report_color,
        monitor_path_hooks=monitor_path_hooks,
        monitor_importer_cache=monitor_importer_cache,
        monitor_sys_path=monitor_sys_path,
        deep=deep,
        deep_path_hooks=deep_path_hooks,
        deep_path_entry_finders=deep_path_entry_finders,
        deep_loaders=deep_loaders,
        deep_import_outcomes=deep_import_outcomes,
    )
    return monitor


def uninstall() -> None:
    """Undo :func:`install`: restore a plain ``sys.meta_path`` and unshadow finders."""
    monitor = get_monitor()
    if monitor is not None:
        monitor.uninstall()


def get_monitor() -> Monitor | None:
    """Return the process-wide monitor, or None if :func:`install` was never called."""
    with _singleton_lock:
        return _monitor_singleton


def write_report(
    destination: "TextIO | str | PathLike[str] | None" = None,
    *,
    format: "Literal['text', 'json']" = "text",
    color: "Literal['auto', 'always', 'never']" = "auto",
) -> None:
    """Write a text or JSON report to stderr, a stream, or an atomic file.

    Args:
        destination: Output stream or exact file path; None selects stderr.
        format: Text or stable JSON output.
        color: ANSI color policy for text output. Auto detects a TTY and
            honors ``NO_COLOR`` and ``TERM=dumb``.

    Raises:
        RuntimeError: If :func:`install` was never called.
        ValueError: If ``format`` is unsupported.
        OSError: If a file or stream write fails.
    """
    _report.write_report(_require_monitor(), destination, format=format, color=color)


def render_report(*, format: "Literal['text', 'json']" = "text", color: bool = False) -> str:
    """Return the diagnostic report as text or versioned JSON.

    Args:
        format: Text or stable JSON output.
        color: Include ANSI styling in text output. JSON is never styled.

    Raises:
        RuntimeError: If :func:`install` was never called.
        ValueError: If ``format`` is unsupported.
    """
    return _report.render_report(_require_monitor(), format=format, color=color)


def _pid_destination(path: str, pid: int) -> str:
    """Add ``pid`` to an automatic filename, replacing ``{pid}`` when supplied."""
    if "{pid}" in path:
        return path.replace("{pid}", str(pid))
    stem, suffix = os.path.splitext(path)
    return f"{stem}.{pid}{suffix}"


def _require_monitor() -> Monitor:
    """Return the singleton monitor.

    Raises:
        RuntimeError: If :func:`install` was never called.
    """
    monitor = get_monitor()
    if monitor is None:
        raise RuntimeError("metapathology.install() has not been called")
    return monitor
