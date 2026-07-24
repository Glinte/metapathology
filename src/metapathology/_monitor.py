"""Core monitor: audit hook, instrumented ``sys.meta_path`` list, finder shadowing.

Hot-path rules (see CLAUDE.md): everything needed here is imported at module
scope; recording code takes ``_record_lock`` only around plain-data appends;
foreign objects are reduced to type names and ids at capture time.
"""

import functools
import sys
import threading
import traceback
import warnings
from contextlib import contextmanager

from metapathology._branch_exploration import ImportBranchExplorer
from metapathology._detailed_capture import _DetailedCapture
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
    _ProgramOutcomeState,
    _RemoteAttachmentState,
)
from metapathology._records import (
    ImporterCacheEntry,
    ImportSearchStarted,
    MetaPathChange,
    MetaPathReplacement,
    MonitorEvent,
    MonitoringError,
    ObjectIdentity,
    PathHooksChange,
    PathHooksReplacement,
    SysPathChange,
    SysPathReplacement,
    type_name,
)

# Supported type checkers treat this conventional name as true without making
# the runtime import typing solely for annotations and casts.
TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from importlib.machinery import ModuleSpec
    from types import FrameType, ModuleType
    from typing import Concatenate, ParamSpec, Protocol
    from typing import cast as _cast

    from _typeshed.importlib import PathEntryFinderProtocol

    from metapathology._config import MonitoringRequest
    from metapathology._monitor_model import (
        ImportCallsCaptureStatus,
        ImportResultsCaptureStatus,
        PathFinderCaptureStatus,
    )
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

    observation_suspended = False
    thread_id: "int | None" = None
    pending_import_search: "tuple[int, str] | None" = None


def _capture_stack(frame: "FrameType") -> traceback.StackSummary:
    """Capture a stack summary from ``frame`` outward without reading source lines.

    ``lookup_lines=False`` keeps source reading (and any ``__loader__.get_source``
    side effects) out of the import hot path; lines resolve at format time.
    Implementation frames are omitted here so remotely staged archive paths
    never enter immutable evidence or structured reports.

    Args:
        frame: Innermost frame to start walking from.
    """
    frames = (
        (candidate, lineno) for candidate, lineno in traceback.walk_stack(frame) if not _metapathology_frame(candidate)
    )
    return traceback.StackSummary.extract(frames, limit=_STACK_CAPTURE_LIMIT, lookup_lines=False)


def _metapathology_frame(frame: "FrameType") -> bool:
    """Identify an implementation frame without relying on its staged filename."""
    globals_ = frame.f_globals
    package = dict.get(globals_, "__package__") if type(globals_) is dict else None
    return package == "metapathology" or (type(package) is str and package.startswith("metapathology."))


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
                self._record_monitoring_error(error_where, exc)
            finally:
                self._local.active = False

        return guarded

    return decorate


class Monitor:
    """Captured import-machinery evidence for the current process.

    Obtain this object from ``install()`` or ``monitoring()``; constructing it
    directly does not start monitoring. Event accessors return snapshots, so
    callers cannot mutate the monitor's internal evidence.
    """

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
        self._detailed = _DetailedCapture(self)
        self._branch_exploration = ImportBranchExplorer(self)
        self._importer_cache = _ImporterCacheObserver(self)
        self._import_audit_enabled = False
        self._meta_path_enabled = False
        self._finder_attribution_enabled = False
        # One counter for all record types, so the report can interleave the
        # different event kinds chronologically.
        self._seq = 0
        self._search_id = 0
        self._thread_id = 0
        self._events: list[MonitorEvent] = []
        # Master switch: hooks stay registered after uninstall() (audit hooks
        # are irremovable) but become no-ops when this is False.
        self._enabled = False
        # sys.addaudithook is one-way; remember so a reinstall never stacks a second hook.
        self._audit_installed = False
        # Set by the generated .pth activation path. The first activation wins
        # because a later site directory cannot move the evidence cutoff earlier.
        self._early_site_bootstrap: _EarlySiteBootstrapState | None = None
        self._frozen_bootstrap: _FrozenBootstrapState | None = None
        self._remote_attachment: _RemoteAttachmentState | None = None
        # The exact list object we last put into sys.meta_path. The audit hook
        # compares by identity: a mismatch means someone reassigned sys.meta_path.
        self._instrumented: _InstrumentedMetaPath | None = None
        self._meta_path_lease: _OwnedValue | None = None
        # Path-hooks monitoring is independently enableable. Observed hooks remain strongly
        # referenced while active so recorded ids cannot be reused mid-capture.
        self._path_hooks_enabled = False
        self._instrumented_path_hooks: _InstrumentedPathHooks | None = None
        self._path_hooks_lease: _OwnedValue | None = None
        self.initial_path_hooks: tuple[ObjectIdentity, ...] = ()
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
        self._program_outcome: _ProgramOutcomeState | None = None

    @property
    def enabled(self) -> bool:
        """Whether the monitor is currently observing."""
        return self._enabled

    @property
    def import_audit_enabled(self) -> bool:
        """Whether import audit starts and reassignment recovery are active."""
        return self._enabled and self._import_audit_enabled

    @property
    def meta_path_enabled(self) -> bool:
        """Whether reversible ``sys.meta_path`` list observation is active."""
        return self._enabled and self._meta_path_enabled

    @property
    def finder_attribution_enabled(self) -> bool:
        """Whether writable finder instances are shadowed for attribution."""
        return self._enabled and self._finder_attribution_enabled

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
    def detailed_capture(self) -> tuple[str, ...]:
        """Names of active detailed capture mechanisms."""
        return self._detailed.enabled_names(self._enabled)

    @property
    def import_results_capture_status(self) -> "ImportResultsCaptureStatus":
        """Activation state and thread scope of exact import outcomes."""
        return self._detailed.import_results_capture_status

    @property
    def import_calls_capture_status(self) -> "ImportCallsCaptureStatus":
        """Activation state of ``builtins.__import__`` call observation."""
        return self._detailed.import_calls_capture_status

    @property
    def path_finder_capture_status(self) -> "PathFinderCaptureStatus":
        """Availability of exact aggregate standard-finder evidence."""
        return self._detailed.path_finder_capture_status

    @property
    def unsafe_import_branch_exploration_status(self) -> str:
        """Coverage of unsafe branch exploration.

        The value is ``"complete"``, ``"partial"``, ``"disabled"``, or
        ``"uninstalled"``. Partial coverage means prerequisite capture was
        disabled or an existing profiler prevented some calls from being
        observed.
        """
        return self._branch_exploration.status

    def _original_path_hook(self, hook: object) -> object:
        """Normalize one detailed wrapper to the foreign hook it delegates to."""
        return self._detailed.original_path_hook(hook)

    def _unsafe_exploration_active(self) -> bool:
        """Return whether this thread is executing a skipped foreign candidate."""
        return self._branch_exploration.active

    def _explore_after_meta_path_exception(
        self,
        finder: object,
        fullname: str,
        path: object,
        target: object,
        trigger_event_seq: int | None,
    ) -> None:
        """Delegate one terminal finder exception to the unsafe explorer."""
        self._branch_exploration.after_meta_path_exception(
            finder,
            fullname,
            path,
            target,
            trigger_event_seq,
        )

    @property
    def program_outcome(self) -> "_ProgramOutcomeState | None":
        """Plain reduction of the target's completion, if one was recorded."""
        return self._program_outcome

    def record_program_outcome(self, exception: BaseException | None = None, exit_code: int | None = None) -> None:
        """Record how the monitored target finished, reduced to plain data.

        Only the exception's type name and, for ``ImportError`` subclasses,
        its ``name`` attribute are read; the message is never stringified.

        Args:
            exception: The exception that ended the target, or None.
            exit_code: The process exit status the target run produces.
        """
        if exception is None:
            self._program_outcome = _ProgramOutcomeState("completed", None, None, exit_code)
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
        self._program_outcome = _ProgramOutcomeState("raised", type_name(exception), missing, exit_code)

    def events(self) -> list[MonitorEvent]:
        """Return a snapshot of all recorded events in capture order.

        The list is independent of the monitor. Its event records are
        immutable.
        """
        with self._record_lock:
            return list(self._events)

    def skipped_finders(self) -> list[tuple[str, str]]:
        """Return ``(finder name, reason)`` for meta-path entries that could not be instrumented."""
        return self._finder_attribution.skipped_names(self)

    def _snapshot(self) -> MonitorSnapshot:
        """Copy chronological evidence and skipped finders at one sequence cutoff."""
        self._importer_cache.observe("report")
        with self._record_lock:
            cache_state = self._importer_cache.report_state(self._enabled)
            skipped_finders, finder_apis = self._finder_attribution.snapshot_locked()
            return MonitorSnapshot(
                cutoff_seq=self._seq,
                events=tuple(self._events),
                skipped_finders=skipped_finders,
                finder_apis=finder_apis,
                importer_cache=cache_state,
                retained_cache_finders=self._importer_cache.retained_finders_locked(),
                early_site_bootstrap=self._early_site_bootstrap,
                frozen_bootstrap=self._frozen_bootstrap,
                remote_attachment=self._remote_attachment,
                enabled=self._enabled,
                baseline_modules=self.baseline_modules,
                initial_meta_path=self.initial_meta_path,
                import_audit_enabled=self._enabled and self._import_audit_enabled,
                meta_path_enabled=self._enabled and self._meta_path_enabled,
                finder_attribution_enabled=self._enabled and self._finder_attribution_enabled,
                path_hooks_enabled=self._enabled and self._path_hooks_enabled,
                initial_path_hooks=self.initial_path_hooks,
                sys_path_enabled=self._enabled and self._sys_path_enabled,
                detailed_capture=self.detailed_capture,
                import_results_capture_status=self._detailed.import_results_capture_status,
                import_calls_capture_status=self._detailed.import_calls_capture_status,
                path_finder_capture_status=self._detailed.path_finder_capture_status,
                unsafe_import_branch_exploration_status=self._branch_exploration.status,
                program_outcome=self._program_outcome,
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

    def _set_remote_attachment(self, session_id: str, installed_at: str) -> None:
        """Attach late remote-install provenance to this capture."""
        state = _RemoteAttachmentState(
            session_id,
            installed_at,
            "pep_768_remote_exec",
            "future_import_activity_after_attachment_only",
        )
        with self._record_lock:
            if self._remote_attachment is None:
                self._remote_attachment = state

    @contextmanager
    def _suspend_observation(self) -> "Iterator[None]":
        """Make this thread's monitor producers inert around diagnostic work."""
        previously_active = self._local.active
        previously_suspended = self._local.observation_suspended
        self._local.active = True
        self._local.observation_suspended = True
        try:
            yield
        finally:
            self._local.active = previously_active
            self._local.observation_suspended = previously_suspended

    def _install(self, request: "MonitoringRequest") -> None:
        """Activate the monitoring mechanisms from one resolved request."""
        with self._lifecycle_condition:
            while self._transition_generation is not None:
                self._lifecycle_condition.wait()
            self._lifecycle_generation += 1
            generation = self._lifecycle_generation
            self._transition_generation = generation
            activate_monitor = not self._enabled
            if activate_monitor:
                self._enabled = True
        try:
            # Explicit errors were raised by the resolver before this point.
            # Invalid environment values are retained as plain diagnostics.
            for issue in request.issues:
                self._record_monitoring_error(issue, ValueError())
            if activate_monitor:
                self._activate_core_monitor(generation, request)
            self._activate_requested_mechanisms(request)
        finally:
            with self._lifecycle_condition:
                if self._transition_generation == generation:
                    self._transition_generation = None
                    self._lifecycle_condition.notify_all()

    def _activate_core_monitor(self, generation: int, request: "MonitoringRequest") -> None:
        """Prepare and commit only the independently requested core mechanisms."""
        if _IMPLEMENTATION_NAME != "cpython":
            warnings.warn(_UNSUPPORTED_IMPLEMENTATION_WARNING, RuntimeWarning, stacklevel=3)
        self.baseline_modules = frozenset(sys.modules)
        current = sys.meta_path
        self.initial_meta_path = tuple(type_name(finder) for finder in current)
        instrumented = _InstrumentedMetaPath(current, self) if request.meta_path else None
        # Finder attribute access is foreign code and can import. Preparation
        # therefore runs outside the lifecycle lock.
        self._local.active = True
        try:
            if request.finder_attribution:
                for position, finder in enumerate(list(current)):
                    self._finder_attribution.observe_api(finder, position, "install", None, self)
                    self._finder_attribution.instrument(finder, self)
        finally:
            self._local.active = False
        if request.import_audit and not self._audit_installed:
            sys.addaudithook(self._audit)
            self._audit_installed = True
        with self._lifecycle_condition:
            if self._transition_generation != generation:
                raise RuntimeError("install transition superseded before commit")
            self._import_audit_enabled = request.import_audit
            self._meta_path_enabled = request.meta_path
            self._finder_attribution_enabled = request.finder_attribution
            if instrumented is not None:
                self._instrumented = instrumented
                self._meta_path_lease = _OwnedValue(current, instrumented)
                sys.meta_path = instrumented

    def _activate_requested_mechanisms(self, request: "MonitoringRequest") -> None:
        """Enable requested independent mechanisms without disabling active ones."""
        if request.unsafe_explore_import_branches and not self._branch_exploration.enabled:
            self._branch_exploration.enable()
        if request.path_hooks and not self._path_hooks_enabled:
            self._enable_path_hooks()
        if request.importer_cache and not self._importer_cache.enabled:
            self._importer_cache.enable()
        if request.sys_path and not self._sys_path_enabled:
            self._enable_sys_path()
        if request.capture_path_entry_finder_calls:
            self._detailed.enable_path_entry_finders()
        if request.capture_loader_calls:
            self._detailed.enable_loaders()
        if request.capture_path_hook_calls and not self._detailed.path_hooks_enabled:
            self._detailed.enable_path_hooks()
        if request.capture_import_results and not self._detailed.import_results_enabled:
            self._detailed.enable_import_results()
        if request.unsafe_explore_import_branches:
            self._branch_exploration.set_capture_coverage(
                profiler=self._detailed.import_results_enabled,
                prerequisites=(
                    request.finder_attribution
                    and request.path_hooks
                    and request.importer_cache
                    and request.capture_path_hook_calls
                    and request.capture_path_entry_finder_calls
                ),
            )
        if request.capture_import_calls and not self._detailed.import_calls_enabled:
            self._detailed.enable_import_calls()

    def _enable_path_hooks(self) -> None:
        """Install the instrumented list around the current ``sys.path_hooks`` contents."""
        contents = list(sys.path_hooks)
        initial = tuple(ObjectIdentity.of(hook) for hook in contents)
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

    def _uninstall(self) -> None:
        """Restore plain import lists and unshadow all finders (idempotent)."""
        with self._lifecycle_condition:
            while self._transition_generation is not None:
                self._lifecycle_condition.wait()
            if not self._enabled:
                return
            self._importer_cache.observe("uninstall")
            self._enabled = False
            self._branch_exploration.uninstall()
            self._detailed.uninstall()
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
                    self._record_monitoring_error("uninstall_path_hooks", exc)
                self._path_hooks_enabled = False
                self._instrumented_path_hooks = None
                self._path_hooks_lease = None
            if self._sys_path_enabled:
                try:
                    sys_path_lease = self._sys_path_lease
                    if sys_path_lease is not None and sys.path is sys_path_lease.installed:
                        sys.path = list(sys.path)
                except Exception as exc:
                    self._record_monitoring_error("uninstall_sys_path", exc)
                self._sys_path_enabled = False
                self._instrumented_sys_path = None
                self._sys_path_lease = None
            self._finder_attribution.uninstall(self)
            self._import_audit_enabled = False
            self._meta_path_enabled = False
            self._finder_attribution_enabled = False
            with self._record_lock:
                self._observed_path_hooks.clear()
                self._importer_cache.uninstall()

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
        if event != "import" or not self._enabled or not self._import_audit_enabled:
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
            self._record_import_search_started(
                fullname,
                meta_path_id,
                meta_path_type_names,
                path_hooks_id,
                cache_fingerprint,
            )
        meta_path_current = not self._meta_path_enabled or sys.meta_path is self._instrumented
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

    def _record_import_search_started(
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
        # On CPython 3.15+, the profiler can observe _find_and_load before this
        # audit event. Reuse that search instead of recording one import twice.
        profiler_search_id = self._detailed.active_import_search_id(fullname)
        with self._record_lock:
            self._importer_cache.note_fingerprint_locked(cache_fingerprint)
            thread_id = self._thread_id_locked()
            if profiler_search_id is None:
                self._search_id += 1
                search_id = self._search_id
            else:
                search_id = profiler_search_id
            self._seq += 1
            self._events.append(
                ImportSearchStarted(
                    sequence=self._seq,
                    search_id=search_id,
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
        if self._detailed.import_results_enabled and profiler_search_id is None:
            self._local.pending_import_search = (search_id, fullname)

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
        if not self._meta_path_enabled or current is expected or expected is None:
            return None
        old_contents = tuple(type_name(finder) for finder in expected)
        new_contents = tuple(type_name(finder) for finder in list(current))
        replacement = _InstrumentedMetaPath(current, self)
        if self._finder_attribution_enabled:
            for position, finder in enumerate(list(replacement)):
                self._finder_attribution.observe_api(finder, position, "replacement", None, self)
                self._finder_attribution.instrument(finder, self)
        self._instrumented = replacement
        self._meta_path_lease = _OwnedValue(current, replacement)
        sys.meta_path = replacement
        return old_contents, new_contents

    def _recover_path_hooks(self) -> tuple[tuple[ObjectIdentity, ...], tuple[ObjectIdentity, ...]] | None:
        current = sys.path_hooks
        expected = self._instrumented_path_hooks
        if not self._path_hooks_enabled or current is expected or expected is None:
            return None
        old_hooks = tuple(expected)
        new_hooks = tuple(current)
        old_contents = tuple(ObjectIdentity.of(self._original_path_hook(hook)) for hook in old_hooks)
        new_contents = tuple(ObjectIdentity.of(self._original_path_hook(hook)) for hook in new_hooks)
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
        path_hooks_data: tuple[tuple[ObjectIdentity, ...], tuple[ObjectIdentity, ...]] | None,
        sys_path_data: tuple[tuple[str, ...], tuple[str, ...]] | None,
    ) -> None:
        """Append only plain reassignment evidence after releasing the reinstall lock."""
        with self._record_lock:
            if meta_data is not None:
                self._seq += 1
                self._events.append(
                    MetaPathReplacement(
                        sequence=self._seq,
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
                    PathHooksReplacement(
                        sequence=self._seq,
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
                    SysPathReplacement(
                        sequence=self._seq,
                        during_import=fullname,
                        old_contents=sys_path_data[0],
                        new_contents=sys_path_data[1],
                        thread_name=thread_name,
                        stack=stack,
                    )
                )

    @_guard_monitor_callback("meta_path_change")
    def _on_meta_path_change(
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
        if not self._enabled or not self._meta_path_enabled:
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
                MetaPathChange(
                    sequence=self._seq,
                    op=op,
                    added=added_names,
                    removed=removed_names,
                    contents_after=contents_after,
                    thread_name=thread_name,
                    stack=stack,
                )
            )
        if self._finder_attribution_enabled:
            positions = {id(finder): position for position, finder in enumerate(list(mutated))}
            for finder in added:
                self._finder_attribution.observe_api(
                    finder,
                    positions.get(id(finder), -1),
                    "change",
                    mutation_seq,
                    self,
                )
                self._finder_attribution.instrument(finder, self)

    @_guard_monitor_callback("path_hooks_change")
    def _on_path_hooks_change(
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
        added_refs = tuple(ObjectIdentity.of(self._original_path_hook(hook)) for hook in added)
        removed_refs = tuple(ObjectIdentity.of(self._original_path_hook(hook)) for hook in removed)
        if self._detailed.path_hooks_enabled:
            for added_hook in added:
                wrapper = self._detailed.wrap_added_path_hook(added_hook)
                if wrapper is added_hook:
                    continue
                for index, current in enumerate(mutated):
                    if current is added_hook:
                        list.__setitem__(mutated, index, wrapper)
        contents_after = tuple(ObjectIdentity.of(self._original_path_hook(hook)) for hook in list(mutated))
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
                PathHooksChange(
                    sequence=self._seq,
                    op=op,
                    added=added_refs,
                    removed=removed_refs,
                    contents_after=contents_after,
                    thread_name=thread_name,
                    stack=stack,
                )
            )
        self._importer_cache.observe("after_path_hooks_change")

    @_guard_monitor_callback("importer_cache_before_path_hooks_change")
    def _on_path_hooks_before_mutation(self, mutated: "_InstrumentedPathHooks") -> None:
        """Observe the importer cache immediately before a live path-hook list operation."""
        if not self._enabled or not self._path_hooks_enabled or mutated is not self._instrumented_path_hooks:
            return
        self._importer_cache.observe("before_path_hooks_change")

    @_guard_monitor_callback("sys_path_change")
    def _on_sys_path_change(
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
                SysPathChange(
                    self._seq,
                    op,
                    added_paths,
                    removed_paths,
                    contents_after,
                    thread_name,
                    stack,
                )
            )

    def _record_monitoring_error(self, where: str, exc: BaseException) -> None:
        """Record an exception raised by our own instrumentation instead of letting it escape.

        Args:
            where: Short label of the code path that failed.
            exc: The caught exception. Only its type name is captured; calling
                ``str(exc)`` here could execute foreign code during an import.
        """
        exception_type_name = type(exc).__name__
        with self._record_lock:
            self._seq += 1
            self._events.append(
                MonitoringError(sequence=self._seq, where=where, exception_type_name=exception_type_name)
            )
