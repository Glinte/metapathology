"""Core monitor: audit hook, instrumented ``sys.meta_path`` list, finder shadowing.

Hot-path rules (see CLAUDE.md): everything needed here is imported at module
scope; recording code takes ``_record_lock`` only around plain-data appends;
foreign objects are reduced to type names and ids at capture time.
"""

import atexit
import contextlib
import copy
import functools
import sys
import threading
import traceback
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder

from metapathology import _report
from metapathology._records import (
    FindSpecCall,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
    type_name,
)

# Supported type checkers treat this conventional name as true without making
# the runtime import typing solely for annotations and casts.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from importlib.machinery import ModuleSpec
    from types import FrameType, ModuleType
    from typing import Any, SupportsIndex, TextIO, overload
    from typing import cast as _cast

    from _typeshed import SupportsRichComparison
    from _typeshed.importlib import MetaPathFinderProtocol

    _FindSpec = Callable[[str, Sequence[str] | None, ModuleType | None], ModuleSpec | None]
else:

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
        # One counter for all record types, so the report can interleave the
        # different event kinds chronologically.
        self._seq = 0
        self._events: list[MonitorEvent] = []
        self._search_path_cache: list[tuple[str, ...]] = []
        # Master switch: hooks stay registered after uninstall() (audit hooks
        # are irremovable) but become no-ops when this is False.
        self._enabled = False
        # sys.addaudithook is one-way; remember so a reinstall never stacks a second hook.
        self._audit_installed = False
        # Whether our atexit callback is currently registered (mirrors it for unregister).
        self._report_at_exit = False
        # The exact list object we last put into sys.meta_path. The audit hook
        # compares by identity: a mismatch means someone reassigned sys.meta_path.
        self._instrumented: _InstrumentedMetaPath | None = None
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

    @property
    def enabled(self) -> bool:
        """Whether the monitor is currently observing."""
        return self._enabled

    @property
    def initial_meta_path(self) -> tuple[str, ...]:
        """Finder names in ``sys.meta_path`` at install time."""
        return self._initial_meta_path

    @property
    def baseline_modules(self) -> frozenset[str]:
        """Names present in ``sys.modules`` at install time."""
        return self._baseline_modules

    def events(self) -> list[MonitorEvent]:
        """Return a snapshot of all recorded events, in capture order."""
        with self._record_lock:
            return list(self._events)

    def skipped_finders(self) -> list[tuple[str, str]]:
        """Return ``(finder name, reason)`` for meta-path entries that could not be instrumented."""
        with self._record_lock:
            return [(type_name(finder), reason) for finder, reason in self._skipped.values()]

    def install(self, *, report_at_exit: bool = True) -> None:
        """Instrument ``sys.meta_path`` and register the import audit hook (idempotent).

        A repeat call on an already-enabled monitor changes nothing except
        that ``report_at_exit=True`` still registers the exit report if it is
        not registered yet.

        Args:
            report_at_exit: Also register an atexit callback that writes the
                report to stderr.
        """
        with self._reinstall_lock:
            if not self._enabled:
                self._enabled = True
                self._baseline_modules = frozenset(sys.modules)
                current = sys.meta_path
                self._initial_meta_path = tuple(type_name(f) for f in current)
                instrumented = _InstrumentedMetaPath(current, self)
                for finder in list(instrumented):
                    self._instrument_finder(finder)
                self._instrumented = instrumented
                sys.meta_path = instrumented
                if not self._audit_installed:
                    # Audit hooks are irremovable; _audit goes inert when disabled.
                    sys.addaudithook(self._audit)
                    self._audit_installed = True
        if report_at_exit and not self._report_at_exit:
            self._report_at_exit = True
            atexit.register(self._report_atexit)

    def uninstall(self) -> None:
        """Restore a plain ``sys.meta_path`` list and unshadow all finders (idempotent)."""
        with self._reinstall_lock:
            if not self._enabled:
                return
            self._enabled = False
            current = sys.meta_path
            if isinstance(current, _InstrumentedMetaPath):
                sys.meta_path = list(current)
            self._instrumented = None
            for finder, _original, previous in list(self._patched.values()):
                with contextlib.suppress(AttributeError, KeyError, TypeError):
                    if previous is _MISSING:
                        del finder.__dict__["find_spec"]
                    else:
                        finder.__dict__["find_spec"] = previous
            with self._record_lock:
                self._patched.clear()
                self._skipped.clear()
                self._search_path_cache.clear()
        if self._report_at_exit:
            self._report_at_exit = False
            atexit.unregister(self._report_atexit)

    def _audit(self, event: str, args: tuple[object, ...]) -> None:
        """``sys.addaudithook`` callback: detect ``sys.meta_path`` reassignment on each ``import`` event.

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
            if sys.meta_path is self._instrumented:
                return
            self._reinstall(args)
        except Exception as exc:
            # An exception escaping an audit hook aborts the user's import.
            self._record_internal_error("audit_hook", exc)
        finally:
            self._local.active = False

    def _reinstall(self, args: tuple[object, ...]) -> None:
        """Wrap a foreign ``sys.meta_path`` in fresh instrumentation and record the reassignment.

        Args:
            args: The ``import`` audit event arguments, used to attribute the
                detection to the import that was in flight.
        """
        with self._reinstall_lock:
            # Re-check: another importing thread may have reinstalled already.
            current = sys.meta_path
            expected = self._instrumented
            if current is expected or expected is None or not self._enabled:
                return
            old_contents = tuple(type_name(f) for f in list(expected))
            new_contents = tuple(type_name(f) for f in list(current))
            replacement = _InstrumentedMetaPath(current, self)
            for finder in list(replacement):
                self._instrument_finder(finder)
            self._instrumented = replacement
            sys.meta_path = replacement
        fullname = args[0] if args and isinstance(args[0], str) else "<unknown>"
        stack = _capture_stack(sys._getframe())
        thread_name = threading.current_thread().name
        with self._record_lock:
            self._seq += 1
            self._events.append(
                MetaPathReassignment(
                    seq=self._seq,
                    during_import=fullname,
                    old_contents=old_contents,
                    new_contents=new_contents,
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
            instance_dict = finder.__dict__
            previous = instance_dict.get("find_spec", _MISSING)
        except (AttributeError, TypeError):
            previous = _MISSING
        wrapper = self._make_find_spec_wrapper(type_name(finder), finder_id, typed_original)
        try:
            setattr(finder, "find_spec", wrapper)
        except Exception:
            self._skip_finder(finder_id, finder, "find_spec not settable on the instance")
            return
        with self._record_lock:
            self._patched[finder_id] = (finder, typed_original, previous)

    def _skip_finder(self, finder_id: int, finder: object, reason: str) -> None:
        """Mark a finder as uninstrumentable, releasing any claim taken by ``_instrument_finder``.

        Args:
            finder_id: ``id(finder)``, the claim key.
            finder: The entry itself; kept referenced so its id stays unique.
            reason: Human-readable explanation shown in the report.
        """
        with self._record_lock:
            self._patched.pop(finder_id, None)
            self._skipped[finder_id] = (finder, reason)

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

        @functools.wraps(original)
        def find_spec(
            fullname: str,
            path: "Sequence[str] | None" = None,
            target: "ModuleType | None" = None,
        ) -> "ModuleSpec | None":
            if self._local.active:
                return original(fullname, path, target)
            self._local.active = True
            spec = None
            exception_type_name: str | None = None
            try:
                search_path = self._snapshot_search_path(path)
                try:
                    spec = original(fullname, path, target)
                except BaseException as exc:
                    exception_type_name = type(exc).__name__
                    raise
                finally:
                    self._record_find_spec(
                        finder_type_name,
                        finder_id,
                        fullname,
                        spec,
                        search_path,
                        exception_type_name,
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
            return tuple(entry for entry in source if isinstance(entry, str))
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
        exception_type_name: str | None,
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
            if spec is not None:
                loader = getattr(spec, "loader", None)
                if loader is not None:
                    loader_type_name = type_name(loader)
                raw_origin = getattr(spec, "origin", None)
                if isinstance(raw_origin, str):
                    origin = raw_origin
            found = spec is not None
            thread_name = threading.current_thread().name
            with self._record_lock:
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
                        exception_type_name=exception_type_name,
                        thread_name=thread_name,
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
            for finder in added:
                self._instrument_finder(finder)
            added_names = tuple(type_name(f) for f in added)
            removed_names = tuple(type_name(f) for f in removed)
            contents_after = tuple(type_name(f) for f in list(mutated))
            thread_name = threading.current_thread().name
            stack = _capture_stack(frame)
            with self._record_lock:
                self._seq += 1
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
        except Exception as exc:
            self._record_internal_error("meta_path_mutation", exc)
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
        """Write the report to stderr from atexit without ever breaking interpreter shutdown."""
        try:
            _report.write_report(self, sys.stderr)
        except Exception:
            traceback.print_exc()


class _InstrumentedMetaPath(list["MetaPathFinderProtocol"]):
    """Drop-in ``sys.meta_path`` replacement that records mutations.

    A real ``list`` subclass, never a proxy: third-party code that scans
    ``sys.meta_path`` with ``isinstance``, slices it, or iterates it keeps
    working unchanged. It records additions, removals, replacements, clearing,
    and reordering through the normal Python list operations. Mutations made
    from C via ``PyList_*`` bypass these overrides; that is an accepted blind
    spot.
    """

    __slots__ = ("_monitor",)

    def __init__(self, contents: "Iterable[MetaPathFinderProtocol]", monitor: Monitor) -> None:
        """Wrap ``contents`` without recording a mutation.

        Args:
            contents: The finders to start with, typically the previous
                ``sys.meta_path``.
            monitor: The monitor that receives mutation callbacks.
        """
        super().__init__(contents)
        self._monitor = monitor

    def append(self, item: "MetaPathFinderProtocol") -> None:
        """Delegate to ``list.append`` and record the addition."""
        super().append(item)
        self._monitor._on_meta_path_mutation(self, "append", (item,), (), sys._getframe(1))

    def insert(self, index: "SupportsIndex", item: "MetaPathFinderProtocol") -> None:
        """Delegate to ``list.insert`` and record the addition."""
        super().insert(index, item)
        self._monitor._on_meta_path_mutation(self, "insert", (item,), (), sys._getframe(1))

    def extend(self, items: "Iterable[MetaPathFinderProtocol]") -> None:
        """Record an ``extend``, materializing ``items`` first so one-shot iterators can be both stored and logged."""
        materialized = tuple(items)
        super().extend(materialized)
        self._monitor._on_meta_path_mutation(self, "extend", materialized, (), sys._getframe(1))

    def remove(self, item: "MetaPathFinderProtocol") -> None:
        """Delegate to ``list.remove`` and record the removal."""
        super().remove(item)
        self._monitor._on_meta_path_mutation(self, "remove", (), (item,), sys._getframe(1))

    def pop(self, index: "SupportsIndex" = -1) -> "MetaPathFinderProtocol":
        """Delegate to ``list.pop`` and record the removal.

        Returns:
            The removed finder, as ``list.pop`` would.
        """
        item = super().pop(index)
        self._monitor._on_meta_path_mutation(self, "pop", (), (item,), sys._getframe(1))
        return item

    def clear(self) -> None:
        """Record the removal of the entire contents, then delegate to ``list.clear``."""
        removed = tuple(self)
        super().clear()
        self._monitor._on_meta_path_mutation(self, "clear", (), removed, sys._getframe(1))

    def reverse(self) -> None:
        """Record an order-only mutation; finder precedence changes even though membership does not."""
        super().reverse()
        self._monitor._on_meta_path_mutation(self, "reverse", (), (), sys._getframe(1))

    def sort(
        self,
        *,
        key: "Callable[[MetaPathFinderProtocol], SupportsRichComparison] | None" = None,
        reverse: bool = False,
    ) -> None:
        """Record an order change made with ``list.sort``."""
        # Runtime list.sort accepts None; the two type checkers model its
        # overloads incompatibly for an unconstrained list element type.
        super().sort(key=key, reverse=reverse)  # type: ignore
        self._monitor._on_meta_path_mutation(self, "sort", (), (), sys._getframe(1))

    # TODO(Python >= 3.11): Return typing.Self once Python 3.10 support is dropped.
    def __imul__(self, value: "SupportsIndex") -> "_InstrumentedMetaPath":
        """Record an in-place repeat, including additions or a complete wipe."""
        before = tuple(self)
        super().__imul__(value)
        after = tuple(self)
        added = after[len(before) :] if len(after) > len(before) else ()
        removed = before[len(after) :] if len(after) < len(before) else ()
        self._monitor._on_meta_path_mutation(self, "__imul__", added, removed, sys._getframe(1))
        return self

    def __copy__(self) -> list["MetaPathFinderProtocol"]:
        """Match the plain-list interface exposed before instrumentation."""
        return list(self)

    # ``deepcopy``'s public protocol specifies Any for the heterogeneous memo values.
    def __deepcopy__(
        self,
        memo: "dict[int, Any]",  # pyright: ignore[reportExplicitAny]
    ) -> list["MetaPathFinderProtocol"]:
        """Deep-copy finders without traversing the monitor's locks and state."""
        return copy.deepcopy(list(self), memo)

    if TYPE_CHECKING:

        @overload
        def __setitem__(self, index: SupportsIndex, value: MetaPathFinderProtocol) -> None: ...

        @overload
        def __setitem__(self, index: slice, value: Iterable[MetaPathFinderProtocol]) -> None: ...

    def __setitem__(
        self,
        index: "SupportsIndex | slice",
        value: "MetaPathFinderProtocol | Iterable[MetaPathFinderProtocol]",
    ) -> None:
        """Record single-item or slice replacement (slice assignment is how many tools filter finders)."""
        if isinstance(index, slice):
            added = tuple(_cast("Iterable[MetaPathFinderProtocol]", value))
            removed = tuple(self[index])
            super().__setitem__(index, added)
        else:
            item = _cast("MetaPathFinderProtocol", value)
            added = (item,)
            removed = (self[index],)
            super().__setitem__(index, item)
        self._monitor._on_meta_path_mutation(self, "__setitem__", added, removed, sys._getframe(1))

    def __delitem__(self, index: "SupportsIndex | slice") -> None:
        """Delegate to ``list.__delitem__`` and record the removed finder(s)."""
        removed = tuple(self[index]) if isinstance(index, slice) else (self[index],)
        super().__delitem__(index)
        self._monitor._on_meta_path_mutation(self, "__delitem__", (), removed, sys._getframe(1))

    # TODO(Python >= 3.11): Return typing.Self once Python 3.10 support is dropped.
    def __iadd__(self, items: "Iterable[MetaPathFinderProtocol]") -> "_InstrumentedMetaPath":
        """Record ``+=``, which reaches ``list`` at the C level and would otherwise bypass the ``extend`` override."""
        materialized = tuple(items)
        super().extend(materialized)
        self._monitor._on_meta_path_mutation(self, "__iadd__", materialized, (), sys._getframe(1))
        return self


# One Monitor per process: the instrumentation targets process-global state
# (sys.meta_path, the audit hook), so multiple instances would fight each other.
_monitor_singleton: Monitor | None = None
_singleton_lock = threading.Lock()


def install(*, report_at_exit: bool = True) -> Monitor:
    """Install the import-machinery monitor (idempotent) and return it.

    Call as early as possible: only imports and mutations that happen after
    this call are observed.

    Args:
        report_at_exit: Also register an atexit callback that writes the
            report to stderr.
    """
    global _monitor_singleton
    with _singleton_lock:
        if _monitor_singleton is None:
            _monitor_singleton = Monitor()
        monitor = _monitor_singleton
    monitor.install(report_at_exit=report_at_exit)
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


def report(file: "TextIO | None" = None) -> None:
    """Write the diagnostic report to ``file`` (default ``sys.stderr``).

    Raises:
        RuntimeError: If :func:`install` was never called.
    """
    _report.write_report(_require_monitor(), file)


def render_report() -> str:
    """Return the diagnostic report as text.

    Raises:
        RuntimeError: If :func:`install` was never called.
    """
    return _report.render_report(_require_monitor())


def _require_monitor() -> Monitor:
    """Return the singleton monitor.

    Raises:
        RuntimeError: If :func:`install` was never called.
    """
    monitor = get_monitor()
    if monitor is None:
        raise RuntimeError("metapathology.install() has not been called")
    return monitor
