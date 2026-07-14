"""Plain event records captured by the monitor.

Records hold only primitive data (type names, ids, precomputed strings)
extracted at capture time. Foreign objects are never repr()'d while an import
may be in flight; all formatting happens at report time in ``_report``.
"""

from dataclasses import dataclass
from traceback import StackSummary
from typing import TypeAlias


def type_name(obj: object) -> str:
    """Best-effort display name: ``__name__`` for class entries (e.g. ``PathFinder``), type name otherwise."""
    if isinstance(obj, type):
        return obj.__name__
    return type(obj).__name__


@dataclass(frozen=True, slots=True)
class MetaPathMutation:
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
    op: str
    added: tuple[str, ...]
    removed: tuple[str, ...]
    contents_after: tuple[str, ...]
    thread_name: str
    stack: StackSummary


@dataclass(frozen=True, slots=True)
class MetaPathReassignment:
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
    stack: StackSummary


@dataclass(frozen=True, slots=True)
class FindSpecCall:
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

    seq: int
    fullname: str
    finder_type_name: str
    finder_id: int
    found: bool
    loader_type_name: str | None
    origin: str | None
    search_path: tuple[str, ...]
    exception_type_name: str | None
    thread_name: str


@dataclass(frozen=True, slots=True)
class InternalError:
    """An exception raised inside metapathology's own instrumentation.

    Recorded instead of raised: an exception escaping a hook would abort the
    user's import, which a diagnostic tool must never do.

    Attributes:
        seq: Position in the monitor's single event log (shared counter, see
            :class:`MetaPathMutation`).
        where: Short label of the failing code path, e.g. ``"audit_hook"``.
        exception_type_name: Type name of the caught exception.
        message: ``str(exc)``, or a placeholder if stringification failed too.
    """

    seq: int
    where: str
    exception_type_name: str
    message: str


# Everything the monitor records goes into one chronological log; ``seq`` orders records across types.
MonitorEvent: TypeAlias = FindSpecCall | InternalError | MetaPathMutation | MetaPathReassignment
