"""Process-wide monitoring lifecycle and report-output policy."""

import atexit
import os
import threading
import warnings
from contextlib import contextmanager, suppress

from metapathology import _report
from metapathology._config import (
    AnalysisConfig,
    CaptureConfig,
    InstallRequest,
    ResolvedAnalysisConfig,
    ResolvedCaptureConfig,
    _UnresolvedReportingOptions,
    monitoring_request,
    normalize_report_destination,
    resolve_install_request,
)
from metapathology._config import (
    ReportTarget as _ReportTarget,
)
from metapathology._monitor import Monitor

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from os import PathLike
    from typing import Literal, TextIO

    from metapathology._report import ReportFailure
    from metapathology._report_model import ReportDocument

    _ColorMode = Literal["auto", "always", "never"]
    _ReportFormat = Literal["text", "json"]


class _AutomaticReporting:
    """Own automatic output targets and their atexit callback."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._targets: tuple[_ReportTarget, ...] = (_ReportTarget(None, "text", "auto"),)
        self._registered = False
        self._analysis = ResolvedAnalysisConfig(True, False)
        self._callback = self._write_at_exit

    def targets(self) -> tuple[_ReportTarget, ...]:
        """Return one coherent copy of the configured targets."""
        with self._lock:
            return self._targets

    def configure(self, request: InstallRequest) -> None:
        """Apply resolved output policy after monitoring activation succeeds."""
        with self._lock:
            self._targets = request.report_targets or (_ReportTarget(None, "text", "auto"),)
            self._analysis = request.analysis
            register = request.report_at_exit and not self._registered
            if register:
                self._registered = True
        if register:
            atexit.register(self._callback)

    def unregister(self) -> None:
        """Disable the automatic output callback without discarding its options."""
        with self._lock:
            registered = self._registered
            self._registered = False
        if registered:
            atexit.unregister(self._callback)

    def write_configured(self) -> None:
        """Write the configured target, adding the current PID to file paths."""
        targets = self.targets()
        monitor = _require_monitor()
        artifact = _capture_report(monitor, self._analysis)
        first_error: Exception | None = None
        for target in targets:
            destination = target.destination
            if destination is not None:
                destination = _pid_destination(destination, os.getpid())
            try:
                _report.write_report(artifact, destination, format=target.format, color=target.color)
            except Exception as exc:
                monitor._record_monitoring_error("report_write", exc)
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _write_at_exit(self) -> None:
        """Write automatically without disturbing interpreter shutdown."""
        with suppress(Exception):
            self.write_configured()


# One monitor and reporting policy per process. The instrumentation itself has
# a separate lifecycle lock because audit recovery must not wait on output I/O.
_monitor_singleton: Monitor | None = None
_singleton_lock = threading.Lock()
_runtime_condition = threading.Condition()
_runtime_transition = False
_automatic_reporting = _AutomaticReporting()
_active_capture: ResolvedCaptureConfig | None = None
_active_analysis: ResolvedAnalysisConfig | None = None
_active_unsafe_exploration: bool | None = None

_monitoring_region_condition = threading.Condition()
_monitoring_region_count = 0
_monitoring_regions_own_installation = False
_monitoring_region_transition = False
_PREEXISTING_MONITOR_WARNING = (
    "monitoring() entered while monitoring was already installed; "
    "the pre-existing installation will remain active after the region exits"
)


def install(
    *,
    report_at_exit: bool = True,
    report_destination: "str | PathLike[str] | Sequence[str | PathLike[str]] | None" = None,
    report_text: "str | PathLike[str] | Sequence[str | PathLike[str]] | None" = None,
    report_json: "str | PathLike[str] | Sequence[str | PathLike[str]] | None" = None,
    report_color: "Literal['auto', 'always', 'never'] | None" = None,
    capture: CaptureConfig | None = None,
    analysis: AnalysisConfig | None = None,
    unsafe_explore_import_branches: bool | None = None,
) -> Monitor:
    """Install the process-wide monitor and configure automatic reporting."""
    global _active_analysis, _active_capture, _active_unsafe_exploration
    normalized_destination = normalize_report_destination(report_destination)
    normalized_text = normalize_report_destination(report_text)
    normalized_json = normalize_report_destination(report_json)
    report_configuration_explicit = (
        report_destination is not None or report_text is not None or report_json is not None or report_color is not None
    )
    _begin_runtime_transition()
    try:
        monitor = _get_or_create_monitor()
        current_targets = _automatic_reporting.targets()
        active = monitor.enabled
        request = resolve_install_request(
            reporting=_UnresolvedReportingOptions(
                report_at_exit=report_at_exit,
                destinations=normalized_destination,
                text_destinations=normalized_text,
                json_destinations=normalized_json,
                color=report_color,
            ),
            capture=CaptureConfig() if capture is None else capture,
            analysis=AnalysisConfig() if analysis is None else analysis,
            use_environment=not monitor.enabled,
            configure_report=not monitor.enabled or report_configuration_explicit,
            current_report_targets=current_targets,
            unsafe_explore_import_branches=unsafe_explore_import_branches,
        )
        if active:
            requested_capture = _active_capture if capture is None else request.capture
            requested_analysis = _active_analysis if analysis is None else request.analysis
            requested_unsafe_exploration = (
                _active_unsafe_exploration
                if unsafe_explore_import_branches is None
                else request.unsafe_explore_import_branches
            )
            if (
                requested_capture != _active_capture
                or requested_analysis != _active_analysis
                or requested_unsafe_exploration != _active_unsafe_exploration
            ):
                raise RuntimeError("capture and analysis configuration cannot change during an active installation")
            assert requested_capture is not None
            assert requested_analysis is not None
            assert requested_unsafe_exploration is not None
            request = InstallRequest(
                report_at_exit=request.report_at_exit,
                report_targets=request.report_targets,
                capture=requested_capture,
                analysis=requested_analysis,
                unsafe_explore_import_branches=requested_unsafe_exploration,
                issues=request.issues,
            )
        monitor._install(monitoring_request(request))
        _automatic_reporting.configure(request)
        _active_capture = request.capture
        _active_analysis = request.analysis
        _active_unsafe_exploration = request.unsafe_explore_import_branches
        return monitor
    finally:
        _end_runtime_transition()


def uninstall() -> None:
    """Restore import state and unregister automatic reporting."""
    _begin_runtime_transition()
    try:
        monitor = get_monitor()
        if monitor is not None:
            monitor._uninstall()
        _automatic_reporting.unregister()
    finally:
        _end_runtime_transition()


def get_monitor() -> Monitor | None:
    """Return the process-wide monitor, if it has ever been created."""
    with _singleton_lock:
        return _monitor_singleton


def _get_or_create_monitor() -> Monitor:
    global _monitor_singleton
    with _singleton_lock:
        if _monitor_singleton is None:
            _monitor_singleton = Monitor()
        return _monitor_singleton


def _begin_runtime_transition() -> None:
    """Reserve lifecycle ownership without holding a lock across foreign code."""
    global _runtime_transition
    with _runtime_condition:
        while _runtime_transition:
            _runtime_condition.wait()
        _runtime_transition = True


def _end_runtime_transition() -> None:
    global _runtime_transition
    with _runtime_condition:
        _runtime_transition = False
        _runtime_condition.notify_all()


def write_report(
    destination: "TextIO | str | PathLike[str] | None" = None,
    *,
    format: "Literal['text', 'json']" = "text",
    color: "Literal['auto', 'always', 'never']" = "auto",
    analysis: AnalysisConfig | None = None,
) -> None:
    """Write an explicit report to a stream or exact path."""
    monitor = _require_monitor()
    _report.validate_format(format)
    _report.validate_color(color)
    _write_report(monitor, destination, format=format, color=color, analysis=analysis)


def render_report(
    *, format: "Literal['text', 'json']" = "text", color: bool = False, analysis: AnalysisConfig | None = None
) -> str:
    """Return an explicit report as text or stable JSON."""
    _report.validate_format(format)
    artifact = _capture_report(_require_monitor(), _resolve_report_analysis(analysis))
    return _report.render_report(artifact, format=format, color=color)


def write_configured_report() -> None:
    """Write the automatic target immediately, as used by the CLI."""
    _automatic_reporting.write_configured()


def _release_monitoring_region() -> None:
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
            uninstall()
        finally:
            with _monitoring_region_condition:
                _monitoring_regions_own_installation = False
                _monitoring_region_transition = False
                _monitoring_region_condition.notify_all()


@contextmanager
def monitoring(
    *,
    capture: CaptureConfig | None = None,
    analysis: AnalysisConfig | None = None,
    unsafe_explore_import_branches: bool | None = None,
) -> "Iterator[Monitor]":
    """Observe imports only while a nested or overlapping region is active."""
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
            _monitoring_region_count += 1
    try:
        if warn_preexisting:
            warnings.warn(_PREEXISTING_MONITOR_WARNING, RuntimeWarning, stacklevel=3)
        monitor = install(
            report_at_exit=False,
            capture=capture,
            analysis=analysis,
            unsafe_explore_import_branches=unsafe_explore_import_branches,
        )
    except BaseException:
        if first_region:
            if began_inactive:
                with suppress(Exception):
                    uninstall()
            with _monitoring_region_condition:
                _monitoring_region_transition = False
                _monitoring_region_condition.notify_all()
        else:
            _release_monitoring_region()
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
        _release_monitoring_region()


def _pid_destination(path: str, pid: int) -> str:
    if "{pid}" in path:
        return path.replace("{pid}", str(pid))
    stem, suffix = os.path.splitext(path)
    return f"{stem}.{pid}{suffix}"


def _require_monitor() -> Monitor:
    monitor = get_monitor()
    if monitor is None:
        raise RuntimeError("metapathology.install() has not been called")
    return monitor


def _resolve_report_analysis(analysis: AnalysisConfig | None) -> ResolvedAnalysisConfig:
    """Resolve a one-report override against the active installed policy."""
    active = _active_analysis
    if active is None:
        raise RuntimeError("metapathology.install() has not been called")
    if analysis is None:
        return active
    values = (analysis.checks, analysis.standard_path_check, analysis.displaced_finder_check)
    if any(value is not None and type(value) is not bool for value in values):
        raise TypeError("analysis configuration fields must be bool or None")
    standard_default = active.standard_path_check if analysis.checks is None else analysis.checks
    displaced_default = active.displaced_finder_check if analysis.checks is None else analysis.checks
    return ResolvedAnalysisConfig(
        standard_path_check=standard_default if analysis.standard_path_check is None else analysis.standard_path_check,
        displaced_finder_check=displaced_default
        if analysis.displaced_finder_check is None
        else analysis.displaced_finder_check,
    )


def _capture_report(
    monitor: Monitor, analysis: ResolvedAnalysisConfig | None = None
) -> "ReportDocument | ReportFailure":
    """Capture once while suppressing only this thread's diagnostic activity."""
    try:
        snapshot = monitor._snapshot()
        with monitor._suspend_observation():
            return _report.capture_report(snapshot, _resolve_report_analysis(None) if analysis is None else analysis)
    except Exception as exc:
        return _report.ReportFailure(type(exc).__name__)


def _write_report(
    monitor: Monitor,
    destination: "TextIO | str | PathLike[str] | None",
    *,
    format: "Literal['text', 'json']",
    color: "Literal['auto', 'always', 'never']",
    analysis: AnalysisConfig | None,
) -> None:
    """Capture and write once, recording explicit output failures as evidence."""
    artifact = _capture_report(monitor, _resolve_report_analysis(analysis))
    try:
        _report.write_report(artifact, destination, format=format, color=color)
    except Exception as exc:
        monitor._record_monitoring_error("report_write", exc)
        raise
