"""Explicitly unsafe, import-time exploration of skipped resolution branches."""

import sys
import threading
from importlib import _bootstrap as _importlib_bootstrap
from importlib.machinery import PathFinder

from metapathology._detailed_capture import original_path_hook
from metapathology._module_metadata import module_cache_state
from metapathology._records import (
    ImportBranchExplorationCall,
    ImportBranchExplorationStarted,
    ModuleCacheState,
    ModuleSpecSnapshot,
    ObjectIdentity,
    type_name,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType
    from typing import Literal

    from metapathology._monitor import Monitor
    from metapathology._monitor_model import UnsafeImportBranchExplorationStatus
    from metapathology._records import ImportBranchBoundary, ImportBranchExplorationOutcome


_MISSING = object()
_FIND_SPEC_CODE = getattr(getattr(_importlib_bootstrap, "_find_spec", None), "__code__", None)
_PATH_GET_SPEC_CODE = getattr(getattr(PathFinder, "_get_spec", None), "__code__", None)


class _PathContext:
    __slots__ = ("fullname", "path", "target")

    def __init__(self, fullname: str, path: object, target: object) -> None:
        self.fullname = fullname
        self.path = path
        self.target = target


class _ExplorationThreadState(threading.local):
    """Same-stack foreign objects and recursion state for one import thread."""

    active = False
    path_contexts: tuple[_PathContext, ...] = ()


class ImportBranchExplorer:
    """Call direct siblings skipped by actual import-resolution branches."""

    def __init__(self, monitor: "Monitor") -> None:
        self._monitor = monitor
        self._local = _ExplorationThreadState()
        self._enabled = False
        self._status: "UnsafeImportBranchExplorationStatus" = "disabled"  # noqa: UP037

    @property
    def enabled(self) -> bool:
        return self._enabled and self._monitor._enabled

    @property
    def active(self) -> bool:
        return self._local.active

    @property
    def status(self) -> "UnsafeImportBranchExplorationStatus":
        if not self._monitor._enabled and self._status.startswith("active_"):
            return "inactive_after_uninstall"
        return self._status

    def enable(self) -> None:
        self._enabled = True
        self._status = "active_exhaustive_direct_siblings"

    def set_capture_coverage(self, *, profiler: bool, prerequisites: bool) -> None:
        """Record whether every branch boundary required by the mode is available."""
        if not self._enabled:
            return
        if not profiler:
            self._status = "partial_profiler_unavailable"
        elif not prerequisites:
            self._status = "partial_capture_prerequisites_disabled"
        else:
            self._status = "active_exhaustive_direct_siblings"

    def uninstall(self) -> None:
        self._enabled = False
        self._status = "inactive_after_uninstall"

    def profile(self, frame: "FrameType", event: str, arg: object) -> None:
        """Observe aggregate importlib returns and explore their skipped siblings."""
        if not self.enabled or self.active:
            return
        if frame.f_code is _PATH_GET_SPEC_CODE:
            self._profile_path_get_spec(frame, event, arg)
        elif frame.f_code is _FIND_SPEC_CODE and event == "return" and arg is not None:
            self._profile_meta_path(frame, arg)

    def _profile_path_get_spec(self, frame: "FrameType", event: str, arg: object) -> None:
        if event == "call":
            fullname = frame.f_locals.get("fullname")
            if type(fullname) is str:
                context = _PathContext(fullname, frame.f_locals.get("path"), frame.f_locals.get("target"))
                self._local.path_contexts = (*self._local.path_contexts, context)
            return
        if event != "return":
            return
        contexts = self._local.path_contexts
        if contexts:
            self._local.path_contexts = contexts[:-1]
        if arg is None:
            return
        try:
            _summary, loader = summarize_spec(arg, iterate_foreign_locations=False)
        except Exception as exc:
            self._monitor._record_monitoring_error("unsafe_branch_path_spec", exc)
            return
        if loader is None:
            return
        fullname = frame.f_locals.get("fullname")
        entry = frame.f_locals.get("entry")
        path = frame.f_locals.get("path")
        finder = frame.f_locals.get("finder")
        target = frame.f_locals.get("target")
        if type(fullname) is not str or type(entry) is not str or finder is None:
            return
        entries = self._safe_string_sequence(path)
        index = self._identity_or_value_index(entries, entry)
        if index is None:
            return
        exploration_seq = self._start("path_entry", fullname, entry, finder, index, "found", None)
        self._run_later_path_entries(exploration_seq, fullname, entries[index + 1 :], index + 1, target)

    def _profile_meta_path(self, frame: "FrameType", spec: object) -> None:
        finder = frame.f_locals.get("finder")
        fullname = frame.f_locals.get("name")
        meta_path = frame.f_locals.get("meta_path")
        path = frame.f_locals.get("path")
        target = frame.f_locals.get("target")
        if finder is None or type(fullname) is not str:
            return
        candidates = self._safe_object_sequence(meta_path)
        index = self._identity_index(candidates, finder)
        if index is None:
            return
        exploration_seq = self._start("meta_path", fullname, None, finder, index, "found", None)
        self._run_meta_candidates(exploration_seq, fullname, path, target, candidates[index + 1 :], index + 1)

    def after_meta_path_exception(
        self,
        finder: object,
        fullname: str,
        path: object,
        target: object,
        trigger_event_seq: int | None,
    ) -> None:
        """Explore later meta-path entries before an actual finder exception escapes."""
        if not self.enabled or self.active:
            return
        candidates = self._safe_object_sequence(sys.meta_path)
        index = self._identity_index(candidates, finder)
        if index is None:
            return
        exploration_seq = self._start("meta_path", fullname, None, finder, index, "raised", trigger_event_seq)
        self._run_meta_candidates(exploration_seq, fullname, path, target, candidates[index + 1 :], index + 1)

    def after_path_hook(
        self,
        hook: object,
        path: str,
        trigger_outcome: "Literal['returned', 'raised']",
        trigger_event_seq: int | None,
    ) -> None:
        """Explore later path hooks after an actual hook terminates traversal."""
        if not self.enabled or self.active:
            return
        hooks = tuple(original_path_hook(item) for item in self._safe_object_sequence(sys.path_hooks))
        index = self._identity_index(hooks, hook)
        if index is None:
            return
        context = self._local.path_contexts[-1] if self._local.path_contexts else None
        fullname = None if context is None else context.fullname
        target = None if context is None else context.target
        exploration_seq = self._start("path_hook", fullname, path, hook, index, trigger_outcome, trigger_event_seq)
        self._run_hooks(exploration_seq, hooks[index + 1 :], index + 1, path, fullname, target)

    def after_path_entry_exception(
        self,
        finder: object,
        fullname: str,
        path_entry: str | None,
        target: object,
        trigger_event_seq: int | None,
    ) -> None:
        """Explore later search-path entries before an actual finder exception escapes."""
        if not self.enabled or self.active or path_entry is None:
            return
        context = self._local.path_contexts[-1] if self._local.path_contexts else None
        entries = () if context is None else self._safe_string_sequence(context.path)
        index = self._identity_or_value_index(entries, path_entry)
        if index is None:
            return
        exploration_seq = self._start("path_entry", fullname, path_entry, finder, index, "raised", trigger_event_seq)
        self._run_later_path_entries(exploration_seq, fullname, entries[index + 1 :], index + 1, target)

    def _run_meta_candidates(
        self,
        exploration_seq: int,
        fullname: str,
        path: object,
        target: object,
        candidates: tuple[object, ...],
        start_index: int,
    ) -> None:
        self._local.active = True
        try:
            for offset, candidate in enumerate(candidates):
                if not self.enabled:
                    break
                self._call_finder(
                    exploration_seq,
                    "meta_path",
                    candidate,
                    start_index + offset,
                    fullname,
                    None,
                    path,
                    target,
                )
        finally:
            self._local.active = False

    def _run_later_path_entries(
        self,
        exploration_seq: int,
        fullname: str,
        entries: tuple[str, ...],
        start_index: int,
        target: object,
    ) -> None:
        self._local.active = True
        try:
            hooks = tuple(original_path_hook(item) for item in self._safe_object_sequence(sys.path_hooks))
            cache = sys.path_importer_cache
            for offset, entry in enumerate(entries):
                if not self.enabled:
                    break
                index = start_index + offset
                cached = dict.get(cache, entry, _MISSING) if type(cache) is dict else _MISSING
                if cached is None:
                    self._record_call(
                        exploration_seq,
                        "path_entry",
                        None,
                        index,
                        fullname,
                        entry,
                        "negative_cache",
                    )
                elif cached is _MISSING:
                    self._run_hooks(exploration_seq, hooks, 0, entry, fullname, target)
                else:
                    self._call_finder(
                        exploration_seq,
                        "path_entry",
                        cached,
                        index,
                        fullname,
                        entry,
                        None,
                        target,
                    )
        finally:
            self._local.active = False

    def _run_hooks(
        self,
        exploration_seq: int,
        hooks: tuple[object, ...],
        start_index: int,
        path: str,
        fullname: str | None,
        target: object,
    ) -> None:
        was_active = self._local.active
        self._local.active = True
        try:
            for offset, hook in enumerate(hooks):
                if not self.enabled:
                    break
                index = start_index + offset
                before = None if fullname is None else module_cache_state(sys.modules, fullname)
                try:
                    finder = hook(path)  # type: ignore[operator]
                except ImportError:
                    self._record_call(
                        exploration_seq, "path_hook", hook, index, fullname, path, "declined", before=before
                    )
                    continue
                except Exception as exc:
                    self._record_call(
                        exploration_seq,
                        "path_hook",
                        hook,
                        index,
                        fullname,
                        path,
                        "raised",
                        exception_type_name=type_name(exc),
                        before=before,
                    )
                    continue
                self._record_call(
                    exploration_seq,
                    "path_hook",
                    hook,
                    index,
                    fullname,
                    path,
                    "returned_finder",
                    returned_finder=finder,
                    before=before,
                )
                if fullname is not None and finder is not None:
                    self._call_finder(
                        exploration_seq,
                        "path_entry",
                        finder,
                        index,
                        fullname,
                        path,
                        None,
                        target,
                    )
        finally:
            self._local.active = was_active

    def _call_finder(
        self,
        exploration_seq: int,
        boundary: "ImportBranchBoundary",
        finder: object,
        candidate_index: int,
        fullname: str,
        path_entry: str | None,
        search_path: object,
        target: object,
    ) -> None:
        before = module_cache_state(sys.modules, fullname)
        try:
            find_spec = getattr(finder, "find_spec", None)
            if not callable(find_spec):
                self._record_call(
                    exploration_seq,
                    boundary,
                    finder,
                    candidate_index,
                    fullname,
                    path_entry,
                    "unsupported",
                    before=before,
                )
                return
            spec = find_spec(fullname, search_path, target) if boundary == "meta_path" else find_spec(fullname, target)
        except Exception as exc:
            self._record_call(
                exploration_seq,
                boundary,
                finder,
                candidate_index,
                fullname,
                path_entry,
                "raised",
                exception_type_name=type_name(exc),
                before=before,
            )
            return
        if spec is None:
            self._record_call(
                exploration_seq,
                boundary,
                finder,
                candidate_index,
                fullname,
                path_entry,
                "not_found",
                before=before,
            )
            return
        try:
            summary, _loader = summarize_spec(spec, iterate_foreign_locations=False)
        except Exception as exc:
            self._record_call(
                exploration_seq,
                boundary,
                finder,
                candidate_index,
                fullname,
                path_entry,
                "raised",
                exception_type_name=type_name(exc),
                before=before,
            )
            return
        self._record_call(
            exploration_seq,
            boundary,
            finder,
            candidate_index,
            fullname,
            path_entry,
            "found",
            summary=summary,
            before=before,
        )

    def _start(
        self,
        boundary: "ImportBranchBoundary",
        fullname: str | None,
        path: str | None,
        trigger: object,
        trigger_index: int,
        trigger_outcome: "Literal['found', 'returned', 'raised']",
        trigger_event_seq: int | None,
    ) -> int:
        thread_name = threading.current_thread().name
        with self._monitor._record_lock:
            thread_id = self._monitor._thread_id_locked()
            self._monitor._seq += 1
            sequence = self._monitor._seq
            self._monitor._events.append(
                ImportBranchExplorationStarted(
                    sequence,
                    boundary,
                    fullname,
                    path,
                    ObjectIdentity.of(trigger),
                    trigger_index,
                    trigger_outcome,
                    trigger_event_seq,
                    thread_id,
                    thread_name,
                )
            )
        return sequence

    def _record_call(
        self,
        exploration_seq: int,
        boundary: "ImportBranchBoundary",
        candidate: object | None,
        candidate_index: int,
        fullname: str | None,
        path: str | None,
        outcome: "ImportBranchExplorationOutcome",
        *,
        summary: ModuleSpecSnapshot | None = None,
        returned_finder: object | None = None,
        exception_type_name: str | None = None,
        before: ModuleCacheState | None = None,
    ) -> None:
        after = None if fullname is None else module_cache_state(sys.modules, fullname)
        thread_name = threading.current_thread().name
        identity = ObjectIdentity.of(None if candidate is None else candidate)
        returned_identity = None if returned_finder is None else ObjectIdentity.of(returned_finder)
        with self._monitor._record_lock:
            if not self._monitor._enabled:
                return
            thread_id = self._monitor._thread_id_locked()
            self._monitor._seq += 1
            self._monitor._events.append(
                ImportBranchExplorationCall(
                    self._monitor._seq,
                    exploration_seq,
                    boundary,
                    fullname,
                    path,
                    identity,
                    candidate_index,
                    outcome,
                    summary,
                    returned_identity,
                    exception_type_name,
                    before,
                    after,
                    thread_id,
                    thread_name,
                )
            )

    @staticmethod
    def _safe_object_sequence(value: object) -> tuple[object, ...]:
        try:
            return tuple(value)  # type: ignore[arg-type]
        except Exception:
            return ()

    @staticmethod
    def _safe_string_sequence(value: object) -> tuple[str, ...]:
        try:
            return tuple(item for item in value if type(item) is str)  # type: ignore[union-attr]
        except Exception:
            return ()

    @staticmethod
    def _identity_index(values: "Sequence[object]", target: object) -> int | None:
        return next((index for index, value in enumerate(values) if value is target), None)

    @staticmethod
    def _identity_or_value_index(values: "Sequence[str]", target: str) -> int | None:
        return next((index for index, value in enumerate(values) if value is target or value == target), None)
