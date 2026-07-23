"""Finder inspection, instance shadowing, attribution, and cleanup."""

import functools
import os
import sys
import threading
import types
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder

from metapathology._module_metadata import module_cache_state
from metapathology._monitor_model import _MISSING
from metapathology._record import _Record
from metapathology._records import (
    FinderAPIObservation,
    FinderProtocol,
    MetaPathFinderCall,
    ModuleCacheState,
    ModuleSpecSnapshot,
    MonitorEvent,
    type_name,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from _thread import LockType
    from collections.abc import Callable, Sequence
    from importlib.machinery import ModuleSpec
    from types import ModuleType
    from typing import Protocol
    from typing import cast as _cast

    from metapathology._records import FinderAPIObservationKind, SearchPathKind

    _FindSpec = Callable[[str, Sequence[str] | None, ModuleType | None], ModuleSpec | None]

    class _ReentrancyState(Protocol):
        active: bool

    class _LoaderInstrumentation(Protocol):
        def instrument_loader(self, loader: object) -> None: ...

    class _FinderAttributionHost(Protocol):
        """Shared monitor services required by finder attribution."""

        _record_lock: LockType
        _enabled: bool
        _seq: int
        _events: list[MonitorEvent]

        @property
        def _local(self) -> _ReentrancyState: ...

        @property
        def _detailed(self) -> _LoaderInstrumentation: ...

        def _thread_id_locked(self) -> int: ...

        def _record_monitoring_error(self, where: str, exc: BaseException) -> None: ...

        @staticmethod
        def _restore_attribute(obj: object, name: str, previous: object, installed: object) -> None: ...
else:

    def _cast(_type: object, value: object) -> object:
        return value


_SEARCH_PATH_CACHE_SIZE = 8
_STANDARD_CLASS_FINDERS = (BuiltinImporter, FrozenImporter, PathFinder)
_STANDARD_CLASS_FINDER_REASON = "standard CPython class finder; expected and deliberately left unchanged"


class _FinderPatch(_Record):
    """A finder claim or committed instance-dict shadow."""

    finder: object
    original: "_FindSpec | None"
    previous: object
    installed: object


class _FinderAttribution:
    """Coordinate finder inspection, instance shadows, wrappers, and restoration."""

    def __init__(self) -> None:
        # Both maps retain finders strongly so an id cannot be reused during
        # capture. A patch with original=None is an in-progress claim.
        self._patches: dict[int, _FinderPatch] = {}
        self._skipped: dict[int, tuple[object, str]] = {}
        self._apis: dict[int, FinderAPIObservation] = {}
        self._search_path_cache: list[tuple[str, ...]] = []

    def instrument(self, finder: object, monitor: "_FinderAttributionHost") -> None:
        """Shadow one finder's ``find_spec`` in its instance dictionary."""
        finder_id = id(finder)
        if not self._claim(finder_id, finder, monitor):
            return
        class_reason = self._class_skip_reason(finder)
        if class_reason is not None:
            self._skip(finder_id, finder, class_reason, monitor)
            return
        original = self._find_spec(finder_id, finder, monitor)
        if original is None:
            return
        instance_dict, previous = self._instance_shadow_state(finder)
        if not self._prepare_claim(finder_id, finder, previous, monitor):
            return
        wrapper = self._make_wrapper(type_name(finder), finder_id, original, monitor)
        if not self._install_shadow(finder_id, finder, original, instance_dict, previous, wrapper, monitor):
            return
        self._commit_shadow(finder_id, finder, original, previous, wrapper, monitor)

    def observe_api(
        self,
        finder: object,
        position: int,
        observation_kind: "FinderAPIObservationKind",
        sequence: int | None,
        monitor: "_FinderAttributionHost",
    ) -> None:
        """Inventory raw finder protocols without invoking attribute lookup."""
        finder_id = id(finder)
        with monitor._record_lock:
            if finder_id in self._apis:
                return
        observation = FinderAPIObservation(
            finder_id=finder_id,
            finder_type_name=type_name(finder),
            position=position,
            observation=observation_kind,
            observation_seq=sequence,
            find_spec=self._inspect_protocol(finder, "find_spec"),
            find_module=self._inspect_protocol(finder, "find_module"),
        )
        with monitor._record_lock:
            if monitor._enabled:
                self._apis.setdefault(finder_id, observation)

    def uninstall(self, monitor: "_FinderAttributionHost") -> None:
        """Restore owned shadows, then discard all capture-lifetime state."""
        for patch in list(self._patches.values()):
            self._restore_patch(patch, monitor)
        with monitor._record_lock:
            self._patches.clear()
            self._skipped.clear()
            self._apis.clear()
            self._search_path_cache.clear()

    def skipped_names(self, monitor: "_FinderAttributionHost") -> list[tuple[str, str]]:
        """Return display-safe skipped-finder descriptions under the shared lock."""
        with monitor._record_lock:
            return [(type_name(finder), reason) for finder, reason in self._skipped.values()]

    def snapshot_locked(self) -> tuple[tuple[tuple[object, str], ...], tuple[FinderAPIObservation, ...]]:
        """Copy report state while the caller holds the monitor record lock."""
        return tuple(self._skipped.values()), tuple(self._apis.values())

    def reuse_search_path_locked(self, snapshot: tuple[str, ...]) -> tuple[str, ...]:
        """Return a recent identity-equal snapshot while the shared lock is held."""
        for index, cached in enumerate(self._search_path_cache):
            if len(cached) != len(snapshot):
                continue
            if any(left is not right for left, right in zip(cached, snapshot, strict=True)):
                continue
            self._search_path_cache.append(self._search_path_cache.pop(index))
            return cached
        self._search_path_cache.append(snapshot)
        if len(self._search_path_cache) > _SEARCH_PATH_CACHE_SIZE:
            del self._search_path_cache[0]
        return snapshot

    def _claim(self, finder_id: int, finder: object, monitor: "_FinderAttributionHost") -> bool:
        with monitor._record_lock:
            if finder_id in self._patches or finder_id in self._skipped:
                return False
            self._patches[finder_id] = _FinderPatch(finder, None, _MISSING, _MISSING)
        return True

    def _prepare_claim(
        self, finder_id: int, finder: object, previous: object, monitor: "_FinderAttributionHost"
    ) -> bool:
        with monitor._record_lock:
            if not monitor._enabled or finder_id not in self._patches:
                return False
            self._patches[finder_id] = _FinderPatch(finder, None, previous, _MISSING)
        return True

    def _commit_shadow(
        self,
        finder_id: int,
        finder: object,
        original: "_FindSpec",
        previous: object,
        wrapper: "_FindSpec",
        monitor: "_FinderAttributionHost",
    ) -> None:
        with monitor._record_lock:
            committed = monitor._enabled and finder_id in self._patches
            if committed:
                self._patches[finder_id] = _FinderPatch(finder, original, previous, wrapper)
        if not committed:
            monitor._restore_attribute(finder, "find_spec", previous, wrapper)

    def _install_shadow(
        self,
        finder_id: int,
        finder: object,
        original: "_FindSpec",
        instance_dict: dict[object, object] | None,
        previous: object,
        wrapper: "_FindSpec",
        monitor: "_FinderAttributionHost",
    ) -> bool:
        try:
            setattr(finder, "find_spec", wrapper)
        except Exception:
            self._skip(finder_id, finder, "find_spec not settable on the instance", monitor)
            return False
        try:
            landed = instance_dict is not None and instance_dict.get("find_spec") is wrapper
        except Exception:
            landed = False
        if landed:
            return True
        try:
            setattr(finder, "find_spec", original)
        except Exception as exc:
            monitor._record_monitoring_error("instrument_finder", exc)
        self._skip(
            finder_id,
            finder,
            "find_spec assignment bypasses the instance dict (slot or descriptor)",
            monitor,
        )
        return False

    @staticmethod
    def _instance_shadow_state(finder: object) -> tuple[dict[object, object] | None, object]:
        try:
            instance_dict = object.__getattribute__(finder, "__dict__")
            return instance_dict, instance_dict.get("find_spec", _MISSING)
        except (AttributeError, TypeError):
            return None, _MISSING

    @staticmethod
    def _class_skip_reason(finder: object) -> str | None:
        if not isinstance(finder, type):
            return None
        if finder in _STANDARD_CLASS_FINDERS:
            return _STANDARD_CLASS_FINDER_REASON
        return "class entry; instance-dict shadowing not applicable"

    def _find_spec(self, finder_id: int, finder: object, monitor: "_FinderAttributionHost") -> "_FindSpec | None":
        try:
            original = getattr(finder, "find_spec", None)
        except Exception as exc:
            self._skip(finder_id, finder, "find_spec lookup raised", monitor)
            monitor._record_monitoring_error("instrument_finder", exc)
            return None
        if callable(original):
            return _cast("_FindSpec", original)
        self._skip(finder_id, finder, "no callable find_spec", monitor)
        return None

    def _skip(self, finder_id: int, finder: object, reason: str, monitor: "_FinderAttributionHost") -> None:
        with monitor._record_lock:
            self._patches.pop(finder_id, None)
            if monitor._enabled:
                self._skipped[finder_id] = (finder, reason)

    def _make_wrapper(
        self, finder_type_name: str, finder_id: int, original: "_FindSpec", monitor: "_FinderAttributionHost"
    ) -> "_FindSpec":
        copy_metadata = isinstance(original, (types.FunctionType, types.MethodType))
        assigned = functools.WRAPPER_ASSIGNMENTS if copy_metadata else ()
        updated = functools.WRAPPER_UPDATES if copy_metadata else ()

        @functools.wraps(original, assigned=assigned, updated=updated)
        def find_spec(
            fullname: str,
            path: "Sequence[str] | None" = None,
            target: "ModuleType | None" = None,
        ) -> "ModuleSpec | None":
            if monitor._local.active or not monitor._enabled:
                return original(fullname, path, target)
            monitor._local.active = True
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
                    self._record_find_spec(
                        finder_type_name,
                        finder_id,
                        fullname,
                        spec,
                        search_path,
                        "sys_path" if path is None else "parent_path",
                        exception_type_name,
                        module_state_before,
                        module_cache_state(sys.modules, fullname),
                        None if target is None else ModuleCacheState("object", id(target), type_name(target)),
                        monitor,
                    )
            finally:
                monitor._local.active = False
            return spec

        return find_spec

    def _record_find_spec(
        self,
        finder_type_name: str,
        finder_id: int,
        fullname: str,
        spec: "ModuleSpec | None",
        search_path: tuple[str, ...],
        search_path_kind: "SearchPathKind",
        exception_type_name: str | None,
        module_state_before: ModuleCacheState,
        module_state_after: ModuleCacheState,
        target_state: ModuleCacheState | None,
        monitor: "_FinderAttributionHost",
    ) -> None:
        """Reduce and append one finder-attribution event."""
        try:
            loader_type_name: str | None = None
            origin: str | None = None
            spec_summary: ModuleSpecSnapshot | None = None
            if spec is not None:
                spec_summary, loader = summarize_spec(spec, iterate_foreign_locations=False)
                if loader is not None:
                    loader_type_name = type_name(loader)
                    monitor._detailed.instrument_loader(loader)
                if type(spec_summary.origin) is str:
                    origin = spec_summary.origin
            thread_name = threading.current_thread().name
            with monitor._record_lock:
                thread_id = monitor._thread_id_locked()
                search_path = self.reuse_search_path_locked(search_path)
                monitor._seq += 1
                monitor._events.append(
                    MetaPathFinderCall(
                        sequence=monitor._seq,
                        fullname=fullname,
                        finder_type_name=finder_type_name,
                        finder_id=finder_id,
                        found=spec is not None,
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
            monitor._record_monitoring_error("record_find_spec", exc)

    @staticmethod
    def _snapshot_search_path(path: "Sequence[str] | None") -> tuple[str, ...]:
        source = sys.path if path is None else path
        try:
            if path is not None:
                return tuple(entry for entry in source if type(entry) is str)
            return tuple(os.getcwd() if entry == "" else entry for entry in source if type(entry) is str)
        except Exception:
            return ()

    @classmethod
    def _inspect_protocol(cls, finder: object, name: str) -> FinderProtocol:
        try:
            if not issubclass(type(finder), type):
                try:
                    instance_dict = object.__getattribute__(finder, "__dict__")
                except (AttributeError, TypeError):
                    instance_dict = None
                if type(instance_dict) is dict and name in instance_dict:
                    return cls._raw_protocol_value(instance_dict[name], "instance_dict", type_name(finder))
                finder_class = type(finder)
            else:
                finder_class = _cast("type[object]", finder)
            for owner in type.__getattribute__(finder_class, "__mro__"):
                namespace = type.__getattribute__(owner, "__dict__")
                if name in namespace:
                    owner_name = type.__getattribute__(owner, "__name__")
                    return cls._raw_protocol_value(namespace[name], "class_dict", owner_name)
        except Exception:
            return FinderProtocol("indeterminate", "inspection_error", None)
        return FinderProtocol("absent", "class_mro", None)

    @staticmethod
    def _raw_protocol_value(value: object, evidence: str, defined_by: str) -> FinderProtocol:
        if isinstance(value, (staticmethod, classmethod)):
            value = value.__func__
        elif not callable(value):
            try:
                for owner in type.__getattribute__(type(value), "__mro__"):
                    if "__get__" in type.__getattribute__(owner, "__dict__"):
                        return FinderProtocol("indeterminate", evidence, defined_by)
            except Exception:
                return FinderProtocol("indeterminate", "inspection_error", None)
        return FinderProtocol("callable" if callable(value) else "non_callable", evidence, defined_by)

    @staticmethod
    def _restore_patch(patch: _FinderPatch, monitor: "_FinderAttributionHost") -> None:
        if patch.installed is not _MISSING:
            monitor._restore_attribute(patch.finder, "find_spec", patch.previous, patch.installed)
