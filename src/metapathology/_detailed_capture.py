"""Opt-in detailed import capture and its reversible shadows."""

import builtins
import functools
import importlib._bootstrap as _importlib_bootstrap
import sys
import threading
import types
from importlib.machinery import PathFinder

from metapathology._module_metadata import module_cache_state, safe_module_name, safe_spec_loader, safe_spec_name
from metapathology._monitor_model import _MISSING, _OwnedAttribute, _OwnedValue
from metapathology._records import (
    ImportCall,
    ImportMechanismCall,
    ImportResult,
    ModuleCacheState,
    ObjectIdentity,
    PathFinderCall,
    type_name,
)
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import FrameType, ModuleType
    from typing import cast as _cast

    from _typeshed.importlib import PathEntryFinderProtocol

    from metapathology._monitor import Monitor
    from metapathology._monitor_model import (
        ImportCallsCaptureStatus,
        ImportResultsCaptureStatus,
        PathFinderCaptureStatus,
    )
    from metapathology._records import ImportMechanismBoundary, ImportMechanismOutcome

    _PathHook = Callable[[str], PathEntryFinderProtocol]
    _ProfileFunction = Callable[[FrameType, str, object], object]
    _Import = Callable[..., ModuleType]
else:

    def _cast(_type: object, value: object) -> object:
        return value


class _DetailedThreadState(threading.local):
    """Per-thread stacks and re-entrancy state for detailed capture."""

    active = False
    import_searches: "tuple[tuple[int, str], ...]" = ()
    path_finder_calls: "tuple[tuple[int, str], ...]" = ()


class _DetailedCapture:
    """Own opt-in detailed mechanisms, state, wrappers, and cleanup."""

    def __init__(self, monitor: "Monitor") -> None:
        self._monitor = monitor
        self._detailed_local = _DetailedThreadState()
        self._detailed_path_hooks = False
        self._detailed_path_entry_finders = False
        self._detailed_loaders = False
        self._detailed_import_results = False
        self._import_results_capture_status: ImportResultsCaptureStatus = "disabled"
        self._detailed_import_calls = False
        self._import_calls_capture_status: ImportCallsCaptureStatus = "disabled"
        self._import_wrapper_lease: _OwnedValue | None = None
        self._path_finder_capture_status: PathFinderCaptureStatus = "disabled"
        self._detailed_import_code: types.CodeType | None = None
        self._path_finder_code: types.CodeType | None = None
        self._sys_profile_lease: _OwnedValue | None = None
        self._thread_profile_lease: _OwnedValue | None = None
        self._detailed_hook_wrappers: dict[int, _DetailedPathHook] = {}
        self._detailed_finder_patches: dict[int, _OwnedAttribute] = {}
        self._detailed_loader_patches: dict[int, tuple[_OwnedAttribute, ...]] = {}

    @property
    def _enabled(self) -> bool:
        return self._monitor._enabled

    @property
    def _local(self):
        return self._monitor._local

    @property
    def _record_lock(self):
        return self._monitor._record_lock

    @property
    def _events(self):
        return self._monitor._events

    @property
    def _seq(self) -> int:
        return self._monitor._seq

    @_seq.setter
    def _seq(self, value: int) -> None:
        self._monitor._seq = value

    @property
    def _search_id(self) -> int:
        return self._monitor._search_id

    @_search_id.setter
    def _search_id(self, value: int) -> None:
        self._monitor._search_id = value

    def _thread_id_locked(self) -> int:
        return self._monitor._thread_id_locked()

    def _record_monitoring_error(self, where: str, exc: BaseException) -> None:
        self._monitor._record_monitoring_error(where, exc)

    def _restore_owned_attribute(self, patch: _OwnedAttribute) -> None:
        self._monitor._restore_owned_attribute(patch)

    @property
    def path_hooks_enabled(self) -> bool:
        return self._detailed_path_hooks

    @property
    def import_results_enabled(self) -> bool:
        return self._detailed_import_results

    @property
    def import_calls_enabled(self) -> bool:
        return self._detailed_import_calls

    @property
    def import_results_capture_status(self) -> "ImportResultsCaptureStatus":
        return self._import_results_capture_status

    @property
    def import_calls_capture_status(self) -> "ImportCallsCaptureStatus":
        return self._import_calls_capture_status

    @property
    def path_finder_capture_status(self) -> "PathFinderCaptureStatus":
        return self._path_finder_capture_status

    def enabled_names(self, monitor_enabled: bool) -> tuple[str, ...]:
        if not monitor_enabled:
            return ()
        enabled: list[str] = []
        if self._detailed_path_hooks:
            enabled.append("path_hooks")
        if self._detailed_path_entry_finders:
            enabled.append("path_entry_finders")
        if self._detailed_loaders:
            enabled.append("loaders")
        if self._detailed_import_results:
            enabled.append("import_results")
        if self._detailed_import_calls:
            enabled.append("import_calls")
        return tuple(enabled)

    def enable_path_entry_finders(self) -> None:
        self._detailed_path_entry_finders = True
        for path, finder in list(sys.path_importer_cache.items()):
            if finder is not None:
                self.instrument_path_entry_finder(finder, path if type(path) is str else None)

    def enable_loaders(self) -> None:
        self._detailed_loaders = True

    def original_path_hook(self, hook: object) -> object:
        wrapper = self._detailed_hook_wrappers.get(id(hook))
        return hook if wrapper is None else wrapper.hook

    def wrap_added_path_hook(self, hook: object) -> "_PathHook":
        typed_hook = _cast("_PathHook", hook)
        if id(hook) in self._detailed_hook_wrappers:
            return typed_hook
        wrapper = _DetailedPathHook(self, typed_hook)
        self._detailed_hook_wrappers[id(wrapper)] = wrapper
        return wrapper

    def enable_import_results(self) -> None:
        """Install a reversible profiler for the captured CPython import boundary."""
        if sys.getprofile() is not None or threading.getprofile() is not None:
            self._import_results_capture_status = "refused_existing_profiler"
            self._path_finder_capture_status = "unavailable_existing_profiler"
            return
        boundary = getattr(_importlib_bootstrap, "_find_and_load", None)
        code = getattr(boundary, "__code__", None)
        if not isinstance(code, types.CodeType):
            self._import_results_capture_status = "unsupported_boundary"
            return
        self._detailed_import_code = code
        path_finder = getattr(PathFinder.find_spec, "__func__", None)
        path_finder_code = getattr(path_finder, "__code__", None)
        self._path_finder_code = path_finder_code if isinstance(path_finder_code, types.CodeType) else None
        self._path_finder_capture_status = (
            "active_path_finder_aggregate" if self._path_finder_code is not None else "unsupported_path_finder_boundary"
        )
        self._detailed_import_results = True
        self._import_results_capture_status = "active_current_and_future_threading_threads_cache_hits_not_observed"
        profile = self._profile_import_boundary
        previous_thread_profile = threading.getprofile()
        previous_sys_profile = sys.getprofile()
        threading.setprofile(profile)
        self._thread_profile_lease = _OwnedValue(previous_thread_profile, profile)
        sys.setprofile(profile)
        self._sys_profile_lease = _OwnedValue(previous_sys_profile, profile)

    def active_import_search_id(self, fullname: str) -> int | None:
        """Return this thread's active detailed search for ``fullname``.

        CPython 3.15 enters ``_find_and_load`` before emitting its import audit
        event. Earlier versions emit the audit event first. Exposing the active
        profiler search lets both orders converge on one search identity.
        """
        searches = self._detailed_local.import_searches
        if searches and searches[-1][1] == fullname:
            return searches[-1][0]
        return None

    def _profile_import_boundary(self, frame: "FrameType", event: str, arg: object) -> None:
        """Record paired entry and completion for the captured ``_find_and_load`` code."""
        if not self._enabled or not self._detailed_import_results or self._local.observation_suspended:
            return
        try:
            self._monitor._branch_exploration.profile(frame, event, arg)
            if frame.f_code is self._path_finder_code:
                self._profile_path_finder(frame, event, arg)
            elif frame.f_code is self._detailed_import_code and event == "call":
                fullname = frame.f_locals.get("name")
                if type(fullname) is not str:
                    return
                thread_name = threading.current_thread().name
                pending = self._local.pending_import_search
                self._local.pending_import_search = None
                with self._record_lock:
                    thread_id = self._thread_id_locked()
                    if pending is not None and pending[1] == fullname:
                        search_id = pending[0]
                    else:
                        self._search_id += 1
                        search_id = self._search_id
                    self._seq += 1
                    self._events.append(ImportResult(self._seq, search_id, fullname, "started", thread_id, thread_name))
                stack = self._detailed_local.import_searches
                self._detailed_local.import_searches = (*stack, (search_id, fullname))
            elif frame.f_code is self._detailed_import_code and event == "return":
                stack = self._detailed_local.import_searches
                if not stack:
                    return
                search_id, fullname = stack[-1]
                self._detailed_local.import_searches = stack[:-1]
                outcome = "failed" if arg is None else "loaded"
                thread_name = threading.current_thread().name
                with self._record_lock:
                    thread_id = self._thread_id_locked()
                    self._seq += 1
                    self._events.append(ImportResult(self._seq, search_id, fullname, outcome, thread_id, thread_name))
        except BaseException as exc:
            # Record even a KeyboardInterrupt/SystemExit for the report, but let
            # control-flow exceptions keep propagating; only our own bugs
            # (ordinary Exception) are swallowed to avoid perturbing the program.
            self._record_monitoring_error("detailed_import_results", exc)
            if not isinstance(exc, Exception):
                raise

    def _profile_path_finder(self, frame: "FrameType", event: str, arg: object) -> None:
        """Capture successful aggregate ``PathFinder`` results by code identity."""
        if event == "call":
            fullname = frame.f_locals.get("fullname")
            searches = self._detailed_local.import_searches
            if type(fullname) is str and searches:
                search_id, search_name = searches[-1]
                if fullname == search_name:
                    stack = self._detailed_local.path_finder_calls
                    self._detailed_local.path_finder_calls = (*stack, (search_id, fullname))
            return
        if event != "return":
            return
        stack = self._detailed_local.path_finder_calls
        if not stack:
            return
        search_id, fullname = stack[-1]
        self._detailed_local.path_finder_calls = stack[:-1]
        if arg is None:
            return
        summary, _loader = summarize_spec(arg, iterate_foreign_locations=False)
        thread_name = threading.current_thread().name
        with self._record_lock:
            thread_id = self._thread_id_locked()
            self._seq += 1
            self._events.append(
                PathFinderCall(
                    self._seq,
                    search_id,
                    fullname,
                    "PathFinder",
                    summary,
                    thread_id,
                    thread_name,
                )
            )

    def enable_import_calls(self) -> None:
        """Wrap ``builtins.__import__`` to observe every import, including cache hits.

        A plain function swap: chain-safe against other tools that also wrap
        ``__import__`` (we delegate to whatever is current and restore it only
        while our wrapper is still installed). ``importlib.import_module`` and
        lower-level importlib entry points bypass ``__import__`` and remain
        unobserved by this mechanism.
        """
        previous = builtins.__import__
        wrapper = self._make_import_wrapper(previous)
        self._detailed_import_calls = True
        self._import_calls_capture_status = "active_all_threads_including_cache_hits"
        self._import_wrapper_lease = _OwnedValue(previous, wrapper)
        builtins.__import__ = wrapper

    def _make_import_wrapper(self, original: "_Import") -> "_Import":
        """Build the delegating ``__import__`` recorder bound to ``original``."""

        @functools.wraps(original, assigned=(), updated=())
        def wrapped_import(
            name: str,
            globals: "dict[str, object] | None" = None,
            locals: "dict[str, object] | None" = None,
            fromlist: "Sequence[str]" = (),
            level: int = 0,
        ) -> "ModuleType":
            if not self._enabled or not self._detailed_import_calls or self._local.observation_suspended:
                return original(name, globals, locals, fromlist, level)
            # Reduce every argument to constant-size plain data before delegating;
            # never stringify foreign objects, and never import in this body.
            importing_module = None
            if type(globals) is dict:
                candidate = dict.get(globals, "__name__", None)
                if type(candidate) is str:
                    importing_module = candidate
            from_names = tuple(item for item in fromlist if type(item) is str) if fromlist else ()
            module_name = name if type(name) is str else ""
            import_level = level if type(level) is int else 0
            # A cache hit leaves no other trace; capture the pre-call state so the
            # report can distinguish it from a fresh resolution.
            state_before = (
                module_cache_state(sys.modules, module_name)
                if import_level == 0 and module_name
                else ModuleCacheState("unavailable")
            )
            try:
                result = original(name, globals, locals, fromlist, level)
            except BaseException as exc:
                self._record_import_call(
                    module_name, from_names, import_level, importing_module, state_before, "raised", type(exc).__name__
                )
                raise
            self._record_import_call(
                module_name, from_names, import_level, importing_module, state_before, "returned", None
            )
            return result

        return wrapped_import

    def _record_import_call(
        self,
        name: str,
        fromlist: tuple[str, ...],
        level: int,
        importing_module: str | None,
        state_before: ModuleCacheState,
        outcome: "ImportMechanismOutcome",
        exception_type_name: str | None,
    ) -> None:
        """Append one primitive-only ``__import__`` record."""
        thread_name = threading.current_thread().name
        try:
            with self._record_lock:
                thread_id = self._thread_id_locked()
                self._seq += 1
                self._events.append(
                    ImportCall(
                        self._seq,
                        name,
                        fromlist,
                        level,
                        importing_module,
                        state_before,
                        outcome,
                        exception_type_name,
                        thread_id,
                        thread_name,
                    )
                )
        except Exception as exc:
            self._record_monitoring_error("detailed_import_calls", exc)

    def enable_path_hooks(self) -> None:
        """Replace current path hooks with wrappers only after explicit opt-in."""
        self._detailed_path_hooks = True
        for index, hook in enumerate(list(sys.path_hooks)):
            wrapper = _DetailedPathHook(self, hook)
            self._detailed_hook_wrappers[id(wrapper)] = wrapper
            list.__setitem__(sys.path_hooks, index, wrapper)

    def instrument_path_entry_finder(self, finder: object, path: str | None = None) -> None:
        """Shadow one mutable path-entry finder's find_spec when requested."""
        if not self._detailed_path_entry_finders or id(finder) in self._detailed_finder_patches:
            return
        try:
            instance_dict = finder.__dict__
            original = getattr(finder, "find_spec")
            previous = instance_dict.get("find_spec", _MISSING)
            finder_id = id(finder)
            finder_name = type_name(finder)

            @functools.wraps(original, assigned=(), updated=())
            def wrapped(fullname: str, target: object = None) -> object:
                if not self._enabled or self._local.observation_suspended:
                    return original(fullname, target)
                if self._monitor._branch_exploration.active:
                    return original(fullname, target)
                target_state = None if target is None else ModuleCacheState("object", id(target), type_name(target))
                if self._detailed_local.active:
                    self._record_detailed_call(
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
                self._detailed_local.active = True
                try:
                    try:
                        spec = original(fullname, target)
                    except BaseException as exc:
                        event_seq = self._record_detailed_call(
                            "path_entry_finder",
                            finder_id,
                            finder_name,
                            fullname,
                            path,
                            "raised",
                            type(exc).__name__,
                            target_state=target_state,
                        )
                        if isinstance(exc, Exception):
                            self._monitor._branch_exploration.after_path_entry_exception(
                                finder, fullname, path, target, event_seq
                            )
                        raise
                    self._record_detailed_call(
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
                        self.instrument_loader(safe_spec_loader(spec))
                    return spec
                finally:
                    self._detailed_local.active = False

            instance_dict["find_spec"] = wrapped
            self._detailed_finder_patches[id(finder)] = _OwnedAttribute(finder, "find_spec", previous, wrapped)
        except Exception as exc:
            self._record_monitoring_error("detailed_path_entry_finder", exc)

    def instrument_loader(self, loader: object) -> None:
        """Shadow one mutable loader's modern lifecycle methods when requested."""
        if loader is None or not self._detailed_loaders or id(loader) in self._detailed_loader_patches:
            return
        patches: list[_OwnedAttribute] = []
        try:
            raw_instance_dict = object.__getattribute__(loader, "__dict__")
            if type(raw_instance_dict) is not dict:
                raise TypeError("loader instance dictionary is unavailable")
            mechanisms: tuple[tuple[str, ImportMechanismBoundary], ...] = (
                ("create_module", "loader_create_module"),
                ("exec_module", "loader_exec_module"),
            )
            for name, boundary in mechanisms:
                patch = self._install_loader_patch(loader, raw_instance_dict, name, boundary)
                if patch is not None:
                    patches.append(patch)
            if patches:
                self._detailed_loader_patches[id(loader)] = tuple(patches)
        except BaseException as exc:
            self._rollback_loader_patches(patches)
            # Record even a KeyboardInterrupt/SystemExit, but reraise control-flow
            # exceptions after undoing the partial patches; only swallow our own
            # bugs (ordinary Exception) so instrumentation never perturbs the program.
            self._record_monitoring_error("detailed_loader", exc)
            if not isinstance(exc, Exception):
                raise

    def _install_loader_patch(
        self,
        loader: object,
        instance_dict: dict[str, object],
        name: str,
        boundary: "ImportMechanismBoundary",
    ) -> _OwnedAttribute | None:
        try:
            original = getattr(loader, name)
        except AttributeError:
            return None
        if not callable(original):
            return None
        previous = instance_dict.get(name, _MISSING)
        wrapped = self._make_loader_wrapper(id(loader), type_name(loader), boundary, original)
        instance_dict[name] = wrapped
        return _OwnedAttribute(loader, name, previous, wrapped)

    def _make_loader_wrapper(
        self,
        loader_id: int,
        loader_name: str,
        boundary: "ImportMechanismBoundary",
        original: "Callable[[object], object]",
    ) -> "Callable[[object], object]":
        @functools.wraps(original, assigned=(), updated=())
        def wrapped(argument: object) -> object:
            fullname = safe_spec_name(argument) if boundary == "loader_create_module" else safe_module_name(argument)
            target_state = (
                None
                if boundary == "loader_create_module"
                else ModuleCacheState("object", id(argument), type_name(argument))
            )
            if not self._enabled or self._local.observation_suspended:
                return original(argument)
            if self._detailed_local.active:
                self._record_detailed_call(
                    boundary,
                    loader_id,
                    loader_name,
                    fullname,
                    None,
                    "unobserved_reentrant",
                    None,
                    target_state=target_state,
                )
                return original(argument)
            return self._run_loader_wrapper(
                boundary, loader_id, loader_name, fullname, target_state, original, argument
            )

        return wrapped

    def _run_loader_wrapper(
        self,
        boundary: "ImportMechanismBoundary",
        loader_id: int,
        loader_name: str,
        fullname: str | None,
        target_state: ModuleCacheState | None,
        original: "Callable[[object], object]",
        argument: object,
    ) -> object:
        state_before = None if fullname is None else module_cache_state(sys.modules, fullname)
        self._detailed_local.active = True
        try:
            try:
                result = original(argument)
            except BaseException as exc:
                state_after = None if fullname is None else module_cache_state(sys.modules, fullname)
                self._record_detailed_call(
                    boundary,
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
            self._record_detailed_call(
                boundary,
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
            self._detailed_local.active = False

    def _rollback_loader_patches(self, patches: list[_OwnedAttribute]) -> None:
        for patch in reversed(patches):
            self._restore_owned_attribute(patch)

    def _record_detailed_call(
        self,
        boundary: "ImportMechanismBoundary",
        object_id: int,
        object_type_name: str,
        fullname: str | None,
        path: str | None,
        outcome: "ImportMechanismOutcome",
        exception_type_name: str | None,
        module_state_before: ModuleCacheState | None = None,
        module_state_after: ModuleCacheState | None = None,
        target_state: ModuleCacheState | None = None,
        returned_finder: ObjectIdentity | None = None,
    ) -> int:
        """Append primitive-only detailed evidence without holding a lock across delegation."""
        thread_name = threading.current_thread().name
        with self._record_lock:
            thread_id = self._thread_id_locked()
            self._seq += 1
            self._events.append(
                ImportMechanismCall(
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
                    returned_finder,
                )
            )
            return self._seq

    def uninstall(self) -> None:
        """Release every detailed mechanism still owned by this monitor."""
        self._uninstall_import_results()
        self._uninstall_import_calls()
        self._uninstall_callable_patches()
        self._detailed_path_hooks = False
        self._detailed_path_entry_finders = False
        self._detailed_loaders = False

    def _uninstall_import_results(self) -> None:
        if self._detailed_import_results:
            sys_profile_lease = self._sys_profile_lease
            if sys_profile_lease is not None and sys.getprofile() is sys_profile_lease.installed:
                sys.setprofile(_cast("_ProfileFunction | None", sys_profile_lease.previous))
            thread_profile_lease = self._thread_profile_lease
            if thread_profile_lease is not None and threading.getprofile() is thread_profile_lease.installed:
                threading.setprofile(_cast("_ProfileFunction | None", thread_profile_lease.previous))
            self._sys_profile_lease = None
            self._thread_profile_lease = None
            self._detailed_import_results = False
            self._detailed_import_code = None
            self._path_finder_code = None
            self._import_results_capture_status = "inactive_after_uninstall"
            self._path_finder_capture_status = "inactive_after_uninstall"

    def _uninstall_import_calls(self) -> None:
        if self._detailed_import_calls:
            import_lease = self._import_wrapper_lease
            # Chain-safe restore: only unwind if our wrapper is still current, so
            # a tool that wrapped __import__ after us keeps its wrapper intact.
            if import_lease is not None and builtins.__import__ is import_lease.installed:
                builtins.__import__ = _cast("_Import", import_lease.previous)
            self._import_wrapper_lease = None
            self._detailed_import_calls = False
            self._import_calls_capture_status = "inactive_after_uninstall"

    def _uninstall_callable_patches(self) -> None:
        for patch in list(self._detailed_finder_patches.values()):
            self._restore_owned_attribute(patch)
        for patches in list(self._detailed_loader_patches.values()):
            for patch in reversed(patches):
                self._restore_owned_attribute(patch)
        if self._detailed_hook_wrappers:
            try:
                current_hooks = sys.path_hooks
                for index, hook in enumerate(list(current_hooks)):
                    wrapper = self._detailed_hook_wrappers.get(id(hook))
                    if wrapper is not None and wrapper is hook:
                        list.__setitem__(current_hooks, index, wrapper.hook)
            except Exception as exc:
                self._record_monitoring_error("uninstall_detailed_path_hooks", exc)
        self._detailed_hook_wrappers.clear()
        self._detailed_finder_patches.clear()
        self._detailed_loader_patches.clear()


def original_path_hook(hook: object) -> object:
    """Return the foreign hook beneath one of this module's wrappers."""
    return hook.hook if isinstance(hook, _DetailedPathHook) else hook


class _DetailedPathHook:
    """Exact-delegating ``sys.path_hooks`` wrapper for detailed path-hook tracing.

    Callable like the hook it shadows, but also forwards equality and hashing
    to that hook so third-party code scanning ``sys.path_hooks`` by ``==``
    (e.g. PyInstaller's ``PyiFrozenFinder.fallback_finder``) still recognizes
    its own hook through the wrapper. Identity (``is``) comparisons cannot be
    intercepted and still see the wrapper — an inherent limitation of shadowing
    at this layer.
    """

    __slots__ = ("__wrapped__", "_hook", "_hook_id", "_hook_name", "_monitor")

    def __init__(self, monitor: "_DetailedCapture", hook: "_PathHook") -> None:
        self._monitor = monitor
        self._hook = hook
        self._hook_id = id(hook)
        self._hook_name = type_name(hook)
        # __wrapped__ link used by inspect.unwrap for introspection.
        self.__wrapped__ = hook

    @property
    def hook(self) -> "_PathHook":
        """The foreign path hook this wrapper delegates to."""
        return self._hook

    def __call__(self, path: str) -> "PathEntryFinderProtocol":
        monitor = self._monitor
        hook = self._hook
        if not monitor._enabled or monitor._local.observation_suspended:
            return hook(path)
        if monitor._monitor._branch_exploration.active:
            return hook(path)
        if monitor._detailed_local.active:
            monitor._record_detailed_call(
                "path_hook", self._hook_id, self._hook_name, None, path, "unobserved_reentrant", None
            )
            return hook(path)
        monitor._detailed_local.active = True
        try:
            try:
                finder = hook(path)
            except BaseException as exc:
                event_seq = monitor._record_detailed_call(
                    "path_hook", self._hook_id, self._hook_name, None, path, "raised", type(exc).__name__
                )
                if isinstance(exc, Exception) and not isinstance(exc, ImportError):
                    monitor._monitor._branch_exploration.after_path_hook(hook, path, "raised", event_seq)
                raise
            event_seq = monitor._record_detailed_call(
                "path_hook",
                self._hook_id,
                self._hook_name,
                None,
                path,
                "returned",
                None,
                returned_finder=ObjectIdentity.of(finder),
            )
            monitor._monitor._branch_exploration.after_path_hook(hook, path, "returned", event_seq)
            monitor.instrument_path_entry_finder(finder, path)
            return finder
        finally:
            monitor._detailed_local.active = False

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _DetailedPathHook):
            return self._hook == other._hook
        return self._hook == other

    def __hash__(self) -> int:
        return hash(self._hook)
