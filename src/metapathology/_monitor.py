"""Core monitor: audit hook, instrumented ``sys.meta_path`` list, finder shadowing.

Hot-path rules (see CLAUDE.md): everything needed here is imported at module
scope; recording code takes ``_record_lock`` only around plain-data appends;
foreign objects are reduced to type names and ids at capture time.
"""

import atexit
import functools
import os
import sys
import threading
import traceback
import warnings
from contextlib import contextmanager, suppress

from metapathology import _report
from metapathology._config import normalize_report_destination, resolve_install_request
from metapathology._deep_diagnostics import _DeepDiagnostics
from metapathology._finder_attribution import _FinderAttribution
from metapathology._importer_cache import _ImporterCacheObserver
from metapathology._instrumented_list import _InstrumentedMetaPath, _InstrumentedPathHooks, _InstrumentedSysPath
from metapathology._monitor_model import (
    _MISSING,
    MonitorSnapshot,
    _EarlySiteBootstrapState,
    _FrozenBootstrapState,
    _OwnedAttribute,
    _OwnedValue,
    _TargetOutcomeState,
)
from metapathology._records import (
    ImportAuditStart,
    ImporterCacheEntry,
    InternalError,
    MetaPathMutation,
    MetaPathReassignment,
    MonitorEvent,
    ObjectRef,
    PathHooksMutation,
    PathHooksReassignment,
    SysPathMutation,
    SysPathReassignment,
    type_name,
)

# Supported type checkers treat this conventional name as true without making
# the runtime import typing solely for annotations and casts.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from importlib.machinery import ModuleSpec
    from os import PathLike
    from types import FrameType, ModuleType
    from typing import Concatenate, Literal, ParamSpec, Protocol, TextIO
    from typing import cast as _cast

    from _typeshed.importlib import PathEntryFinderProtocol

    from metapathology._config import InstallRequest
    from metapathology._monitor_model import DeepImportCallsStatus, DeepImportOutcomesStatus, StandardFinderStatus
    from metapathology._records import MutationOp

    _FindSpec = Callable[[str, Sequence[str] | None, ModuleType | None], ModuleSpec | None]
    _ProfileFunction = Callable[[FrameType, str, object], object]
    _CallbackP = ParamSpec("_CallbackP")
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
_IMPLEMENTATION_NAME = sys.implementation.name
_UNSUPPORTED_IMPLEMENTATION_WARNING = (
    f"metapathology supports CPython only; monitoring on {_IMPLEMENTATION_NAME!r} may be incomplete or inaccurate"
)


class _ThreadState(threading.local):
    """Per-thread re-entrancy flag with a default for newly seen threads."""

    active = False


class _ImportThreadState(_ThreadState):
    """Thread-local state for the core import-observation hooks.

    Class attributes are the per-thread defaults; each thread shadows them with
    instance attributes on first assignment (``threading.local`` semantics).
    """

    report_analysis = False
    thread_id: "int | None" = None
    pending_import_attempt: "tuple[int, str] | None" = None


def _capture_stack(frame: "FrameType") -> traceback.StackSummary:
    """Capture a stack summary from ``frame`` outward without reading source lines.

    ``lookup_lines=False`` keeps source reading (and any ``__loader__.get_source``
    side effects) out of the import hot path; lines resolve at format time.

    Args:
        frame: Innermost frame to start walking from.
    """
    return traceback.StackSummary.extract(traceback.walk_stack(frame), limit=_STACK_CAPTURE_LIMIT, lookup_lines=False)


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


def _path_item_name(item: object) -> str:
    """Reduce a path item without invoking a foreign string conversion."""
    return item if type(item) is str else f"<{type_name(item)}>"


def _guard_monitor_callback(
    error_where: str,
) -> "Callable[[Callable[Concatenate[Monitor, _CallbackP], None]], Callable[Concatenate[Monitor, _CallbackP], None]]":
    """Guard a monitor callback against re-entry and ordinary failures."""

    def decorate(
        callback: "Callable[Concatenate[Monitor, _CallbackP], None]",
    ) -> "Callable[Concatenate[Monitor, _CallbackP], None]":
        @functools.wraps(callback)
        def guarded(self: "Monitor", *args: "_CallbackP.args", **kwargs: "_CallbackP.kwargs") -> None:
            if self._local.active:
                return
            self._local.active = True
            try:
                callback(self, *args, **kwargs)
            except Exception as exc:
                self._record_internal_error(error_where, exc)
            finally:
                self._local.active = False

        return guarded

    return decorate


class Monitor:
    """Records import-machinery activity. Use the module-level :func:`install`."""

    def __init__(self) -> None:
        # Guards event state and component-owned attribution/cache state. Held only
        # around plain-data reads/writes, never across foreign code.
        self._record_lock = threading.Lock()
        # Serializes install/uninstall/reinstall. Lock order: _reinstall_lock
        # may be held while taking _record_lock, never the other way around.
        self._reinstall_lock = threading.Lock()
        self._lifecycle_condition = threading.Condition(self._reinstall_lock)
        self._lifecycle_generation = 0
        self._transition_generation: int | None = None
        # Per-thread re-entrancy flag around every hook and wrapper. Nested
        # imports still run normally, but our instrumentation delegates without
        # trying to observe itself recursively.
        self._local = _ImportThreadState()
        self._finder_attribution = _FinderAttribution()
        self._deep = _DeepDiagnostics(self)
        self._importer_cache = _ImporterCacheObserver(self)
        # Opt-in, independent of --deep: enables report-time replay of displaced
        # importer-cache finders. Off by default; never invokes foreign code at
        # capture time.
        self._speculative_replay = False
        # One counter for all record types, so the report can interleave the
        # different event kinds chronologically.
        self._seq = 0
        self._attempt_id = 0
        self._thread_id = 0
        self._events: list[MonitorEvent] = []
        # Master switch: hooks stay registered after uninstall() (audit hooks
        # are irremovable) but become no-ops when this is False.
        self._enabled = False
        # sys.addaudithook is one-way; remember so a reinstall never stacks a second hook.
        self._audit_installed = False
        # Whether our atexit callback is currently registered (mirrors it for unregister).
        self._report_at_exit = False
        self._atexit_callback = self._report_atexit
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
        self._meta_path_lease: _OwnedValue | None = None
        # Path-hooks monitoring is independently enableable. Observed hooks remain strongly
        # referenced while active so recorded ids cannot be reused mid-capture.
        self._path_hooks_enabled = False
        self._instrumented_path_hooks: _InstrumentedPathHooks | None = None
        self._path_hooks_lease: _OwnedValue | None = None
        self.initial_path_hooks: tuple[ObjectRef, ...] = ()
        self._observed_path_hooks: dict[int, object] = {}
        # Opt-in because replacing sys.path has a wider compatibility surface
        # than observing the import-specific lists enabled by default.
        self._sys_path_enabled = False
        self._instrumented_sys_path: _InstrumentedSysPath | None = None
        self._sys_path_lease: _OwnedValue | None = None
        # Importer-cache diffing retains one install snapshot and one rolling
        # comparison snapshot.
        # Full observations are coalesced when one is already in progress;
        # diff events themselves remain exhaustive and unbounded.
        self.initial_importer_cache: tuple[ImporterCacheEntry, ...] = ()
        # sys.modules names at install time; the report analyzes only modules
        # imported afterwards.
        self.baseline_modules: frozenset[str] = frozenset()
        # Finder display names at install time, for the report header.
        self.initial_meta_path: tuple[str, ...] = ()
        # How the monitored target finished, recorded by the CLI (or any
        # embedder) before the report is written. Reduced to plain data at
        # record time; exception messages are never captured.
        self._target_outcome: _TargetOutcomeState | None = None

    @property
    def enabled(self) -> bool:
        """Whether the monitor is currently observing."""
        return self._enabled

    @property
    def path_hooks_enabled(self) -> bool:
        """Whether ``sys.path_hooks`` mutation monitoring is currently active."""
        return self._enabled and self._path_hooks_enabled

    @property
    def importer_cache_enabled(self) -> bool:
        """Whether passive ``sys.path_importer_cache`` monitoring is active."""
        return self._enabled and self._importer_cache.enabled

    @property
    def sys_path_enabled(self) -> bool:
        """Whether opt-in ``sys.path`` mutation monitoring is active."""
        return self._enabled and self._sys_path_enabled

    @property
    def deep_diagnostics(self) -> tuple[str, ...]:
        """Names of explicitly enabled inline delegation mechanisms."""
        return self._deep.enabled_names(self._enabled)

    @property
    def deep_import_outcomes_status(self) -> "DeepImportOutcomesStatus":
        """Activation state and thread scope of exact import outcomes."""
        return self._deep.deep_import_outcomes_status

    @property
    def deep_import_calls_status(self) -> "DeepImportCallsStatus":
        """Activation state of ``builtins.__import__`` call observation."""
        return self._deep.deep_import_calls_status

    @property
    def standard_finder_status(self) -> "StandardFinderStatus":
        """Availability of exact aggregate standard-finder evidence."""
        return self._deep.standard_finder_status

    def _current_path_hook_refs(self) -> tuple[ObjectRef, ...]:
        """Copy current path-hook identities for report capture."""
        return tuple(ObjectRef.of(self._original_path_hook(hook)) for hook in list(sys.path_hooks))

    def _original_path_hook(self, hook: object) -> object:
        """Normalize one deep wrapper to the foreign hook it delegates to."""
        return self._deep.original_path_hook(hook)

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
        return self._finder_attribution.skipped_names(self)

    def _report_state(self) -> MonitorSnapshot:
        """Copy chronological evidence and skipped finders at one sequence cutoff."""
        self._importer_cache.observe("report")
        with self._record_lock:
            cache_state = self._importer_cache.report_state(self._enabled)
            skipped_finders, finder_contracts = self._finder_attribution.snapshot_locked()
            return MonitorSnapshot(
                cutoff_seq=self._seq,
                events=tuple(self._events),
                skipped_finders=skipped_finders,
                finder_contracts=finder_contracts,
                importer_cache=cache_state,
                early_site_bootstrap=self._early_site_bootstrap,
                frozen_bootstrap=self._frozen_bootstrap,
                enabled=self._enabled,
                baseline_modules=self.baseline_modules,
                initial_meta_path=self.initial_meta_path,
                path_hooks_enabled=self._enabled and self._path_hooks_enabled,
                initial_path_hooks=self.initial_path_hooks,
                sys_path_enabled=self._enabled and self._sys_path_enabled,
                deep_diagnostics=self.deep_diagnostics,
                deep_import_outcomes_status=self._deep.deep_import_outcomes_status,
                deep_import_calls_status=self._deep.deep_import_calls_status,
                standard_finder_status=self._deep.standard_finder_status,
                speculative_replay_enabled=self._enabled and self._speculative_replay,
                target_outcome=self._target_outcome,
            )

    def _retained_cache_finder(self, finder_id: int) -> object | None:
        """Return the live importer-cache finder the observer retained, if any."""
        return self._importer_cache.retained_finder(finder_id)

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

    @contextmanager
    def _report_analysis(self) -> "Iterator[None]":
        """Make every monitor producer inert during report-time foreign calls."""
        previously_active = self._local.active
        previously_reporting = self._local.report_analysis
        self._local.active = True
        self._local.report_analysis = True
        try:
            yield
        finally:
            self._local.active = previously_active
            self._local.report_analysis = previously_reporting

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
        deep_import_calls: bool | None = None,
        speculative_replay: bool | None = None,
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
            deep_import_calls: Wrap ``builtins.__import__`` to observe every
                import statement, including ``sys.modules`` cache hits.
            speculative_replay: At report time, replay a displaced
                importer-cache finder against a module that later failed to
                resolve on its path, to report whether that finder returns a
                spec now. Independent of ``deep``; consult
                ``METAPATHOLOGY_SPECULATIVE_REPLAY`` when omitted.
        """
        # os.fspath() may execute foreign code. Reduce it before taking the
        # lifecycle lock; the resolver receives plain values only.
        normalized_destination = normalize_report_destination(report_destination)
        report_configuration_explicit = (
            report_destination is not None or report_format is not None or report_color is not None
        )
        with self._lifecycle_condition:
            while self._transition_generation is not None:
                self._lifecycle_condition.wait()
            was_enabled = self._enabled
            request = resolve_install_request(
                report_at_exit=report_at_exit,
                report_destination=normalized_destination,
                report_destination_explicit=report_destination is not None,
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
                deep_import_calls=deep_import_calls,
                speculative_replay=speculative_replay,
                use_environment=not was_enabled,
                configure_report=not was_enabled or report_configuration_explicit,
                current_report_destination=self._report_destination,
                current_report_format=self._report_format,
                current_report_color=self._report_color,
            )
            self._lifecycle_generation += 1
            generation = self._lifecycle_generation
            self._transition_generation = generation
            activate_monitor = not self._enabled
            if activate_monitor:
                self._enabled = True
            self._report_destination = request.report_destination
            self._report_format = request.report_format
            self._report_color = request.report_color
        try:
            # Explicit errors were raised by the resolver before this point.
            # Invalid environment values are retained as plain diagnostics.
            for issue in request.issues:
                self._record_internal_error(issue, ValueError())
            if activate_monitor:
                self._activate_core_monitor(generation)
            self._activate_requested_mechanisms(request)
        finally:
            with self._lifecycle_condition:
                if self._transition_generation == generation:
                    self._transition_generation = None
                    self._lifecycle_condition.notify_all()

    def _activate_core_monitor(self, generation: int) -> None:
        """Prepare foreign finder shadows, then commit the core installation."""
        if _IMPLEMENTATION_NAME != "cpython":
            warnings.warn(_UNSUPPORTED_IMPLEMENTATION_WARNING, RuntimeWarning, stacklevel=3)
        self.baseline_modules = frozenset(sys.modules)
        current = sys.meta_path
        self.initial_meta_path = tuple(type_name(finder) for finder in current)
        instrumented = _InstrumentedMetaPath(current, self)
        # Finder attribute access is foreign code and can import. Preparation
        # therefore runs outside the lifecycle lock.
        self._local.active = True
        try:
            for position, finder in enumerate(list(instrumented)):
                self._finder_attribution.observe_contract(finder, position, "install", None, self)
                self._finder_attribution.instrument(finder, self)
        finally:
            self._local.active = False
        if not self._audit_installed:
            sys.addaudithook(self._audit)
            self._audit_installed = True
        with self._lifecycle_condition:
            if self._transition_generation != generation:
                raise RuntimeError("install transition superseded before commit")
            self._instrumented = instrumented
            self._meta_path_lease = _OwnedValue(current, instrumented)
            sys.meta_path = instrumented

    def _activate_requested_mechanisms(self, request: "InstallRequest") -> None:
        """Enable requested independent mechanisms without disabling active ones."""
        if request.monitor_path_hooks and not self._path_hooks_enabled:
            self._enable_path_hooks()
        if request.monitor_importer_cache and not self._importer_cache.enabled:
            self._importer_cache.enable()
        if request.monitor_sys_path and not self._sys_path_enabled:
            self._enable_sys_path()
        if request.deep_path_entry_finders:
            self._deep.enable_path_entry_finders()
        if request.deep_loaders:
            self._deep.enable_loaders()
        if request.deep_path_hooks and not self._deep.path_hooks_enabled:
            self._deep.enable_path_hooks()
        if request.deep_import_outcomes and not self._deep.import_outcomes_enabled:
            self._deep.enable_import_outcomes()
        if request.deep_import_calls and not self._deep.import_calls_enabled:
            self._deep.enable_import_calls()
        if request.speculative_replay:
            self._speculative_replay = True
        if request.report_at_exit and not self._report_at_exit:
            self._report_at_exit = True
            atexit.register(self._atexit_callback)

    def _enable_path_hooks(self) -> None:
        """Install the instrumented list around the current ``sys.path_hooks`` contents."""
        contents = list(sys.path_hooks)
        initial = tuple(ObjectRef.of(hook) for hook in contents)
        instrumented = _InstrumentedPathHooks(contents, self)
        with self._record_lock:
            self._observed_path_hooks.update((reference.object_id, hook) for reference, hook in zip(initial, contents))
        self.initial_path_hooks = initial
        self._instrumented_path_hooks = instrumented
        self._path_hooks_lease = _OwnedValue(sys.path_hooks, instrumented)
        self._path_hooks_enabled = True
        sys.path_hooks = instrumented

    def _enable_sys_path(self) -> None:
        """Install the opt-in mutation observer around current ``sys.path``."""
        instrumented = _InstrumentedSysPath(sys.path, self)
        self._instrumented_sys_path = instrumented
        self._sys_path_lease = _OwnedValue(sys.path, instrumented)
        self._sys_path_enabled = True
        sys.path = instrumented

    def _thread_id_locked(self) -> int:
        """Return this thread's monitor identity while ``_record_lock`` is held."""
        thread_id = self._local.thread_id
        if thread_id is None:
            self._thread_id += 1
            thread_id = self._thread_id
            self._local.thread_id = thread_id
        return thread_id

    def uninstall(self) -> None:
        """Restore plain import lists and unshadow all finders (idempotent)."""
        with self._lifecycle_condition:
            while self._transition_generation is not None:
                self._lifecycle_condition.wait()
            if not self._enabled:
                return
            self._importer_cache.observe("uninstall")
            self._enabled = False
            self._deep.uninstall()
            current = sys.meta_path
            meta_path_lease = self._meta_path_lease
            if meta_path_lease is not None and current is meta_path_lease.installed:
                sys.meta_path = list(current)
            self._instrumented = None
            self._meta_path_lease = None
            if self._path_hooks_enabled:
                try:
                    path_hooks_lease = self._path_hooks_lease
                    if path_hooks_lease is not None and sys.path_hooks is path_hooks_lease.installed:
                        sys.path_hooks = list(sys.path_hooks)
                except Exception as exc:
                    self._record_internal_error("uninstall_path_hooks", exc)
                self._path_hooks_enabled = False
                self._instrumented_path_hooks = None
                self._path_hooks_lease = None
            if self._sys_path_enabled:
                try:
                    sys_path_lease = self._sys_path_lease
                    if sys_path_lease is not None and sys.path is sys_path_lease.installed:
                        sys.path = list(sys.path)
                except Exception as exc:
                    self._record_internal_error("uninstall_sys_path", exc)
                self._sys_path_enabled = False
                self._instrumented_sys_path = None
                self._sys_path_lease = None
            self._finder_attribution.uninstall(self)
            with self._record_lock:
                self._observed_path_hooks.clear()
                self._importer_cache.uninstall()
        if self._report_at_exit:
            self._report_at_exit = False
            atexit.unregister(self._atexit_callback)

    @classmethod
    def _restore_owned_attribute(cls, patch: _OwnedAttribute) -> None:
        """Restore an attribute only while the installed shadow is still owned."""
        cls._restore_attribute(patch.target, patch.name, patch.previous, patch.installed)

    @staticmethod
    def _restore_attribute(obj: object, name: str, previous: object, installed: object) -> None:
        """Restore an instance-dict shadow without invoking foreign setters.

        Idempotent: ``uninstall()`` and a racing instrumentation attempt may both
        call this for the same object, in either order, converging on the same
        end state. Uses raw ``__dict__`` access so cleanup removes the exact
        shadow even if custom attribute dispatch is hostile or has changed since
        installation, and tolerates the entry already being gone (or the object
        having no usable ``__dict__``, as with in-progress claims).

        Args:
            obj: The shadowed (or claimed) object.
            name: The instance-dict key to reset.
            previous: The prior value, or ``_MISSING`` if the key was absent.
            installed: The exact shadow this monitor installed.
        """
        try:
            instance_dict = object.__getattribute__(obj, "__dict__")
            if type(instance_dict) is not dict:
                return
            if instance_dict.get(name, _MISSING) is not installed:
                return
            if previous is _MISSING:
                instance_dict.pop(name, None)
            else:
                instance_dict[name] = previous
        except Exception:
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
        self._on_import_audit(args)

    @_guard_monitor_callback("audit_hook")
    def _on_import_audit(self, args: tuple[object, ...]) -> None:
        """Record one import boundary and recover replaced import lists."""
        fullname = _audit_resolution_name(args)
        if fullname is None:
            self._importer_cache.check_fingerprint()
        else:
            current_meta_path = sys.meta_path
            meta_path_id = id(current_meta_path)
            meta_path_type_names = tuple(type_name(finder) for finder in list(current_meta_path))
            path_hooks_id = id(sys.path_hooks) if self._path_hooks_enabled else None
            cache_fingerprint = self._importer_cache.audit_fingerprint()
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
        # Installation publishes several independently toggleable hooks.
        # Recovery must not wait on or reinterpret that intentional
        # intermediate state as a third-party reassignment.
        if self._transition_generation is not None:
            return
        self._recover_reassignments(args)

    def _record_import_audit_start(
        self,
        fullname: str,
        meta_path_id: int,
        meta_path_type_names: tuple[str, ...],
        path_hooks_id: int | None,
        cache_fingerprint: tuple[int, int] | None,
    ) -> None:
        """Append one plain-data audit record and update the cheap importer-cache fingerprint."""
        thread_name = threading.current_thread().name
        importer_cache_id = None if cache_fingerprint is None else cache_fingerprint[0]
        importer_cache_size = None if cache_fingerprint is None else cache_fingerprint[1]
        with self._record_lock:
            self._importer_cache.note_fingerprint_locked(cache_fingerprint)
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
        if self._deep.import_outcomes_enabled:
            self._local.pending_import_attempt = (attempt_id, fullname)

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
        with self._reinstall_lock:
            if not self._enabled or self._transition_generation is not None:
                return
            meta_data = self._recover_meta_path()
            path_hooks_data = self._recover_path_hooks()
            sys_path_data = self._recover_sys_path()
        if meta_data is None and path_hooks_data is None and sys_path_data is None:
            return
        fullname = args[0] if args and isinstance(args[0], str) else "<unknown>"
        stack = _capture_stack(sys._getframe())
        thread_name = threading.current_thread().name
        self._record_reassignments(fullname, stack, thread_name, meta_data, path_hooks_data, sys_path_data)

    def _recover_meta_path(self) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        current = sys.meta_path
        expected = self._instrumented
        if current is expected or expected is None:
            return None
        old_contents = tuple(type_name(finder) for finder in expected)
        new_contents = tuple(type_name(finder) for finder in list(current))
        replacement = _InstrumentedMetaPath(current, self)
        for position, finder in enumerate(list(replacement)):
            self._finder_attribution.observe_contract(finder, position, "reassignment", None, self)
            self._finder_attribution.instrument(finder, self)
        self._instrumented = replacement
        self._meta_path_lease = _OwnedValue(current, replacement)
        sys.meta_path = replacement
        return old_contents, new_contents

    def _recover_path_hooks(self) -> tuple[tuple[ObjectRef, ...], tuple[ObjectRef, ...]] | None:
        current = sys.path_hooks
        expected = self._instrumented_path_hooks
        if not self._path_hooks_enabled or current is expected or expected is None:
            return None
        old_hooks = tuple(expected)
        new_hooks = tuple(current)
        old_contents = tuple(ObjectRef.of(self._original_path_hook(hook)) for hook in old_hooks)
        new_contents = tuple(ObjectRef.of(self._original_path_hook(hook)) for hook in new_hooks)
        replacement = _InstrumentedPathHooks(new_hooks, self)
        self._instrumented_path_hooks = replacement
        self._path_hooks_lease = _OwnedValue(current, replacement)
        sys.path_hooks = replacement
        with self._record_lock:
            self._observed_path_hooks.update((id(hook), hook) for hook in (*old_hooks, *new_hooks))
        return old_contents, new_contents

    def _recover_sys_path(self) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
        current = sys.path
        expected = self._instrumented_sys_path
        if not self._sys_path_enabled or current is expected or expected is None:
            return None
        old_contents = tuple(_path_item_name(item) for item in expected)
        new_contents = tuple(_path_item_name(item) for item in list(current))
        replacement = _InstrumentedSysPath(current, self)
        self._instrumented_sys_path = replacement
        self._sys_path_lease = _OwnedValue(current, replacement)
        sys.path = replacement
        return old_contents, new_contents

    def _record_reassignments(
        self,
        fullname: str,
        stack: traceback.StackSummary,
        thread_name: str,
        meta_data: tuple[tuple[str, ...], tuple[str, ...]] | None,
        path_hooks_data: tuple[tuple[ObjectRef, ...], tuple[ObjectRef, ...]] | None,
        sys_path_data: tuple[tuple[str, ...], tuple[str, ...]] | None,
    ) -> None:
        """Append only plain reassignment evidence after releasing the reinstall lock."""
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

    @_guard_monitor_callback("meta_path_mutation")
    def _on_meta_path_mutation(
        self,
        mutated: "_InstrumentedMetaPath",
        op: "MutationOp",
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
            self._finder_attribution.observe_contract(
                finder,
                positions.get(id(finder), -1),
                "mutation",
                mutation_seq,
                self,
            )
            self._finder_attribution.instrument(finder, self)

    @_guard_monitor_callback("path_hooks_mutation")
    def _on_path_hooks_mutation(
        self,
        mutated: "_InstrumentedPathHooks",
        op: "MutationOp",
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Record a ``sys.path_hooks`` mutation without invoking any hook."""
        if not self._enabled or not self._path_hooks_enabled or mutated is not self._instrumented_path_hooks:
            return
        added_refs = tuple(ObjectRef.of(self._original_path_hook(hook)) for hook in added)
        removed_refs = tuple(ObjectRef.of(self._original_path_hook(hook)) for hook in removed)
        if self._deep.path_hooks_enabled:
            for added_hook in added:
                wrapper = self._deep.wrap_added_path_hook(added_hook)
                if wrapper is added_hook:
                    continue
                for index, current in enumerate(mutated):
                    if current is added_hook:
                        list.__setitem__(mutated, index, wrapper)
        contents_after = tuple(ObjectRef.of(self._original_path_hook(hook)) for hook in list(mutated))
        observed_hooks = (*added, *removed, *mutated)
        thread_name = threading.current_thread().name
        stack = _capture_stack(frame)
        with self._record_lock:
            # Uninstall may have completed while this callback was reducing
            # plain data. Do not retain hooks or append a late record.
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
        self._importer_cache.observe("after_path_hooks_mutation")

    @_guard_monitor_callback("importer_cache_before_path_hooks_mutation")
    def _on_path_hooks_before_mutation(self, mutated: "_InstrumentedPathHooks") -> None:
        """Observe the importer cache immediately before a live path-hook list operation."""
        if not self._enabled or not self._path_hooks_enabled or mutated is not self._instrumented_path_hooks:
            return
        self._importer_cache.observe("before_path_hooks_mutation")

    @_guard_monitor_callback("sys_path_mutation")
    def _on_sys_path_mutation(
        self,
        mutated: "_InstrumentedSysPath",
        op: "MutationOp",
        added: tuple[object, ...],
        removed: tuple[object, ...],
        frame: "FrameType",
    ) -> None:
        """Record one opt-in ``sys.path`` list mutation using plain values."""
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


# One Monitor per process: the instrumentation targets process-global state
# (sys.meta_path, the audit hook), so multiple instances would fight each other.
_monitor_singleton: Monitor | None = None
_singleton_lock = threading.Lock()
# Context-managed regions are independently composable even though they share
# the process-global monitor. Lifecycle calls remain outside this condition
# because finder cleanup and instrumentation can execute foreign code.
_monitoring_region_condition = threading.Condition()
_monitoring_region_count = 0
_monitoring_regions_own_installation = False
_monitoring_region_transition = False
_PREEXISTING_MONITOR_WARNING = (
    "monitoring() entered while monitoring was already installed; "
    "the pre-existing installation will remain active after the region exits"
)


def _release_monitoring_region(monitor: Monitor | None) -> None:
    """Release one reserved region and clean up its shared installation if last."""
    global _monitoring_region_count, _monitoring_regions_own_installation, _monitoring_region_transition
    with _monitoring_region_condition:
        _monitoring_region_count -= 1
        should_uninstall = _monitoring_region_count == 0 and _monitoring_regions_own_installation
        if should_uninstall:
            _monitoring_region_transition = True
        elif _monitoring_region_count == 0:
            _monitoring_regions_own_installation = False
    if should_uninstall:
        try:
            if monitor is not None:
                monitor.uninstall()
        finally:
            with _monitoring_region_condition:
                _monitoring_regions_own_installation = False
                _monitoring_region_transition = False
                _monitoring_region_condition.notify_all()


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
    deep_import_calls: bool | None = None,
    speculative_replay: bool | None = None,
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
        deep_import_calls: Wrap ``builtins.__import__`` to observe every import
            statement, including ``sys.modules`` cache hits.
        speculative_replay: At report time, replay a displaced importer-cache
            finder against a module that later failed on its path. Independent
            of ``deep``; consults ``METAPATHOLOGY_SPECULATIVE_REPLAY``.
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
        deep_import_calls=deep_import_calls,
        speculative_replay=speculative_replay,
    )
    return monitor


@contextmanager
def monitoring(
    *,
    monitor_path_hooks: bool | None = None,
    monitor_importer_cache: bool | None = None,
    monitor_sys_path: bool | None = None,
    deep: bool | None = None,
    deep_path_hooks: bool | None = None,
    deep_path_entry_finders: bool | None = None,
    deep_loaders: bool | None = None,
    deep_import_outcomes: bool | None = None,
    deep_import_calls: bool | None = None,
    speculative_replay: bool | None = None,
) -> "Iterator[Monitor]":
    """Observe imports only while the context-managed region is active.

    Nested and overlapping regions share the process-wide monitor. The last
    region uninstalls it only when the first region began from an inactive
    monitor. Entering the outermost region around a manually installed monitor
    emits a warning and leaves that installation active afterward. Mechanisms
    enabled by nested regions remain enabled until the shared installation ends.

    Args:
        monitor_path_hooks: Observe ``sys.path_hooks`` mutations and direct
            reassignment.
        monitor_importer_cache: Passively observe
            ``sys.path_importer_cache``.
        monitor_sys_path: Opt in to reversible ``sys.path`` observation.
        deep: Enable every deep mechanism not explicitly configured.
        deep_path_hooks: Replace path hooks with call-capturing delegates.
        deep_path_entry_finders: Shadow path-entry finder calls as they appear.
        deep_loaders: Shadow modern loader lifecycle methods.
        deep_import_outcomes: Observe exact CPython import invocation outcomes.
        deep_import_calls: Wrap ``builtins.__import__`` to observe every import
            statement, including ``sys.modules`` cache hits.
        speculative_replay: At report time, replay a displaced importer-cache
            finder against a module that later failed on its path.

    Yields:
        The process-wide monitor collecting events for the region.
    """
    global _monitoring_region_count, _monitoring_regions_own_installation, _monitoring_region_transition
    began_inactive = False
    warn_preexisting = False
    with _monitoring_region_condition:
        while _monitoring_region_transition:
            _monitoring_region_condition.wait()
        first_region = _monitoring_region_count == 0
        if first_region:
            _monitoring_region_transition = True
            current = get_monitor()
            began_inactive = current is None or not current.enabled
            warn_preexisting = not began_inactive
        else:
            # Reserve this region before applying its options so an earlier
            # region cannot uninstall the shared monitor underneath install().
            _monitoring_region_count += 1
    try:
        if warn_preexisting:
            warnings.warn(_PREEXISTING_MONITOR_WARNING, RuntimeWarning, stacklevel=3)
        monitor = install(
            report_at_exit=False,
            monitor_path_hooks=monitor_path_hooks,
            monitor_importer_cache=monitor_importer_cache,
            monitor_sys_path=monitor_sys_path,
            deep=deep,
            deep_path_hooks=deep_path_hooks,
            deep_path_entry_finders=deep_path_entry_finders,
            deep_loaders=deep_loaders,
            deep_import_outcomes=deep_import_outcomes,
            deep_import_calls=deep_import_calls,
            speculative_replay=speculative_replay,
        )
    except BaseException:
        if first_region:
            if began_inactive:
                partially_installed = get_monitor()
                if partially_installed is not None:
                    with suppress(Exception):
                        partially_installed.uninstall()
            with _monitoring_region_condition:
                _monitoring_region_transition = False
                _monitoring_region_condition.notify_all()
        else:
            # An earlier region may have exited while this reserved slot was
            # applying its options, making this the final release.
            _release_monitoring_region(get_monitor())
        raise
    if first_region:
        with _monitoring_region_condition:
            _monitoring_regions_own_installation = began_inactive
            _monitoring_region_count = 1
            _monitoring_region_transition = False
            _monitoring_region_condition.notify_all()
    try:
        yield monitor
    finally:
        _release_monitoring_region(monitor)


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
