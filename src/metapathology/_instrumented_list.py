"""Generic list mutation observation and import-system adapters."""

import copy
import operator
import sys

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import FrameType
    from typing import Any, SupportsIndex, TypeVar, overload
    from typing import cast as _cast

    from _typeshed import SupportsRichComparison
    from _typeshed.importlib import MetaPathFinderProtocol, PathEntryFinderProtocol

    from metapathology._monitor import Monitor
    from metapathology._records import MutationOp

    _ListItemT = TypeVar("_ListItemT")
    _MetaPathEntry = MetaPathFinderProtocol
    _PathHook = Callable[[str], PathEntryFinderProtocol]
else:

    def _cast(_type: object, value: object) -> object:
        """Return value unchanged; type checkers use typing.cast above."""
        return value


class _InstrumentedList(list["_ListItemT"]):
    """Shared list semantics for instrumented import-system lists.

    A real ``list`` subclass, never a proxy: third-party code that scans
    ``sys.meta_path`` with ``isinstance``, slices it, or iterates it keeps
    working unchanged. It records additions, removals, replacements, clearing,
    and reordering through the normal Python list operations. Mutations made
    from C via ``PyList_*`` bypass these overrides; that is an accepted blind
    spot.
    """

    __slots__ = ("_monitor",)

    def __init__(self, contents: "Iterable[_ListItemT]", monitor: "Monitor") -> None:
        """Wrap ``contents`` without recording a mutation.

        Args:
            contents: The finders to start with, typically the previous
                ``sys.meta_path``.
            monitor: The monitor that receives mutation callbacks.
        """
        super().__init__(contents)
        self._monitor = monitor

    def append(self, item: "_ListItemT") -> None:
        """Delegate to ``list.append`` and record the addition."""
        self._before_mutation(sys._getframe(1))
        super().append(item)
        self._notify("append", (item,), (), sys._getframe(1))

    def insert(self, index: "SupportsIndex", item: "_ListItemT") -> None:
        """Delegate to ``list.insert`` and record the addition."""
        self._before_mutation(sys._getframe(1))
        super().insert(index, item)
        self._notify("insert", (item,), (), sys._getframe(1))

    def extend(self, items: "Iterable[_ListItemT]") -> None:
        """Delegate to ``list.extend`` and record whatever was actually added.

        ``items`` is passed through unmaterialized so an iterator that raises
        mid-iteration keeps its already-yielded prefix, exactly matching plain
        ``list`` semantics; the partial addition is still recorded before the
        exception propagates.
        """
        self._extend_and_record("extend", items, sys._getframe(1))

    def _extend_and_record(self, op: "MutationOp", items: "Iterable[_ListItemT]", frame: "FrameType") -> None:
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

    def remove(self, item: "_ListItemT") -> None:
        """Delegate to ``list.remove`` and record the removal."""
        self._before_mutation(sys._getframe(1))
        super().remove(item)
        self._notify("remove", (), (item,), sys._getframe(1))

    def pop(self, index: "SupportsIndex" = -1) -> "_ListItemT":
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
        key: "Callable[[_ListItemT], SupportsRichComparison] | None" = None,
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
    def __imul__(self, value: "SupportsIndex") -> "_InstrumentedList[_ListItemT]":
        """Record an in-place repeat, including additions or a complete wipe."""
        self._before_mutation(sys._getframe(1))
        before = tuple(self)
        super().__imul__(value)
        after = tuple(self)
        added = after[len(before) :] if len(after) > len(before) else ()
        removed = before[len(after) :] if len(after) < len(before) else ()
        self._notify("__imul__", added, removed, sys._getframe(1))
        return self

    def __copy__(self) -> list["_ListItemT"]:
        """Match the plain-list interface exposed before instrumentation."""
        return list(self)

    # ``deepcopy``'s public protocol specifies Any for the heterogeneous memo values.
    def __deepcopy__(
        self,
        memo: "dict[int, Any]",  # pyright: ignore[reportExplicitAny]
    ) -> list["_ListItemT"]:
        """Deep-copy finders without traversing the monitor's locks and state."""
        return copy.deepcopy(list(self), memo)

    if TYPE_CHECKING:

        @overload
        def __setitem__(self, index: SupportsIndex, value: _ListItemT) -> None: ...

        @overload
        def __setitem__(self, index: slice, value: Iterable[_ListItemT]) -> None: ...

    def __setitem__(
        self,
        index: "SupportsIndex | slice",
        value: "_ListItemT | Iterable[_ListItemT]",
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
            added = tuple(_cast("Iterable[_ListItemT]", value))
            removed = tuple(self[index])
            super().__setitem__(index, added)
        else:
            # See the slice case above: the lookup and assignment must share
            # one normalized integer rather than invoking __index__ twice.
            index = operator.index(index)
            item = _cast("_ListItemT", value)
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
    def __iadd__(self, items: "Iterable[_ListItemT]") -> "_InstrumentedList[_ListItemT]":
        """Record ``+=``, which reaches ``list`` at the C level and would otherwise bypass the ``extend`` override.

        Like :meth:`extend`, ``items`` is not materialized up front, so a
        mid-iteration exception keeps (and records) the already-yielded prefix
        just as a plain ``list`` would.
        """
        self._extend_and_record("__iadd__", items, sys._getframe(1))
        return self

    def _notify(
        self,
        op: "MutationOp",
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Dispatch one completed mutation to the concrete import-list monitor."""
        raise NotImplementedError

    def _before_mutation(self, frame: "FrameType") -> None:
        """Dispatch a pre-mutation observation when the concrete list needs one."""


class _InstrumentedMetaPath(_InstrumentedList["_MetaPathEntry"]):
    """Drop-in ``sys.meta_path`` replacement with finder instrumentation."""

    __slots__ = ()

    def _notify(
        self,
        op: "MutationOp",
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        self._monitor._on_meta_path_mutation(self, op, added, removed, frame)


class _InstrumentedPathHooks(_InstrumentedList["_PathHook"]):
    """Drop-in ``sys.path_hooks`` replacement that never wraps hook factories."""

    __slots__ = ()

    def _before_mutation(self, frame: "FrameType") -> None:
        self._monitor._on_path_hooks_before_mutation(self)

    def _notify(
        self,
        op: "MutationOp",
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        self._monitor._on_path_hooks_mutation(self, op, added, removed, frame)


class _InstrumentedSysPath(_InstrumentedList[str]):
    """Opt-in drop-in ``sys.path`` replacement with mutation attribution."""

    __slots__ = ()

    def _notify(
        self,
        op: "MutationOp",
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        self._monitor._on_sys_path_mutation(self, op, added, removed, frame)
