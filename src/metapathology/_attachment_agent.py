"""Target-side agent executed through CPython's remote debugging facility."""

import atexit
import hashlib
import os
import sys
import time
from contextlib import suppress
from pathlib import Path
from types import ModuleType

from metapathology import _report
from metapathology._attachment_process import ProcessIdentity, current_process_identity, parse_identity, same_process
from metapathology._attachment_protocol import PROTOCOL_VERSION, read_manifest, write_status
from metapathology._config import AnalysisConfig, CaptureConfig, DetailedCaptureConfig
from metapathology._records import type_name
from metapathology._runtime import _capture_report, _resolve_report_analysis, get_monitor, install, uninstall

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal


_REMOTE_PROTOCOL_VERSION = PROTOCOL_VERSION
_REMOTE_PACKAGE_FINGERPRINT: str | None = None
_REMOTE_SESSION_ID: str | None = None
_REMOTE_SESSION_TOKEN: str | None = None
_REMOTE_MANIFEST: Path | None = None
_REMOTE_STATUS: Path | None = None
_REMOTE_TEXT_ARTIFACT: Path | None = None
_REMOTE_JSON_ARTIFACT: Path | None = None
_REMOTE_FORMATS: tuple[str, ...] = ()
_REMOTE_TEXT_COLOR: "Literal['always', 'never']" = "never"
_REMOTE_ACTIVE = False
_REMOTE_RECOVERY_REGISTERED = False


def start(manifest_path: str) -> None:
    """Install one remotely owned monitor and acknowledge its observation boundary."""
    global _REMOTE_ACTIVE, _REMOTE_RECOVERY_REGISTERED

    manifest = read_manifest(Path(manifest_path))
    status_path = _manifest_path(manifest, "status_path")
    identity = current_process_identity()
    expected_identity = parse_identity(dict.get(manifest, "process_identity"))
    if not same_process(identity, expected_identity):
        write_status(
            status_path, "failed", process_identity=identity, errors=(("start.identity", "ProcessIdentityMismatch"),)
        )
        return
    session_id = _manifest_string(manifest, "session_id", maximum=64)
    token = _manifest_string(manifest, "token", exact=64)
    fingerprint = _manifest_string(manifest, "package_fingerprint", exact=64)
    archive = _manifest_path(manifest, "package_archive")
    if _hash_file(archive) != fingerprint:
        write_status(
            status_path, "failed", process_identity=identity, errors=(("start.package", "FingerprintMismatch"),)
        )
        return
    if not _package_is_owned(archive, fingerprint):
        write_status(status_path, "failed", process_identity=identity, errors=(("start.package", "ExistingPackage"),))
        return
    if _REMOTE_ACTIVE:
        write_status(status_path, "failed", process_identity=identity, errors=(("start.session", "ActiveSession"),))
        return
    monitor = get_monitor()
    if monitor is not None and monitor.enabled:
        write_status(status_path, "failed", process_identity=identity, errors=(("start.monitor", "ActiveMonitor"),))
        return

    formats = _manifest_formats(manifest)
    _configure_session(manifest, manifest_path, status_path, formats, fingerprint, session_id, token)
    atexit.register(_recover_at_exit)
    _REMOTE_RECOVERY_REGISTERED = True
    try:
        capture, analysis, unsafe = _monitor_configuration(manifest)
        monitor = install(
            report_at_exit=False,
            capture=capture,
            analysis=analysis,
            unsafe_explore_import_branches=unsafe,
        )
        installed_at = _utc_now()
        monitor._set_remote_attachment(session_id, installed_at, os.fspath(archive))
        _REMOTE_ACTIVE = True
        write_status(status_path, "active", process_identity=identity, installed_at=installed_at)
    except BaseException as exc:
        _REMOTE_ACTIVE = False
        if _REMOTE_RECOVERY_REGISTERED:
            atexit.unregister(_recover_at_exit)
            _REMOTE_RECOVERY_REGISTERED = False
        with suppress(BaseException):
            uninstall()
        write_status(
            status_path,
            "failed",
            process_identity=identity,
            errors=(("start.install", type_name(exc)),),
        )


def _stop(manifest_path: str) -> None:
    """Finalize the matching active session; mismatched requests are inert."""
    manifest = read_manifest(Path(manifest_path))
    if not _matches_active_session(manifest):
        return
    _finalize(uninstall_monitor=True)


def _recover_at_exit() -> None:
    """Best-effort report preservation during orderly interpreter shutdown."""
    if not _REMOTE_ACTIVE:
        return
    _finalize(uninstall_monitor=False)


def _finalize(*, uninstall_monitor: bool) -> None:
    global _REMOTE_ACTIVE, _REMOTE_RECOVERY_REGISTERED
    status = _REMOTE_STATUS
    if status is None:
        return
    errors: list[tuple[str, str]] = []
    identity = _safe_current_identity(errors)
    _render_artifacts(errors)
    if uninstall_monitor:
        _stop_monitor(errors)
        _REMOTE_RECOVERY_REGISTERED = False
    _REMOTE_ACTIVE = False
    state = "failed" if errors else "complete"
    with suppress(BaseException):
        write_status(status, state, process_identity=identity, errors=tuple(errors))


def _configure_session(
    manifest: dict[str, object],
    manifest_path: str,
    status_path: Path,
    formats: tuple[str, ...],
    fingerprint: str,
    session_id: str,
    token: str,
) -> None:
    global _REMOTE_FORMATS, _REMOTE_JSON_ARTIFACT, _REMOTE_MANIFEST, _REMOTE_PACKAGE_FINGERPRINT
    global _REMOTE_SESSION_ID, _REMOTE_SESSION_TOKEN, _REMOTE_STATUS, _REMOTE_TEXT_ARTIFACT, _REMOTE_TEXT_COLOR
    _REMOTE_MANIFEST = Path(manifest_path)
    _REMOTE_STATUS = status_path
    _REMOTE_TEXT_ARTIFACT = _manifest_path(manifest, "text_artifact")
    _REMOTE_JSON_ARTIFACT = _manifest_path(manifest, "json_artifact")
    _REMOTE_FORMATS = formats
    _REMOTE_TEXT_COLOR = "always" if dict.get(manifest, "text_color") == "always" else "never"
    _REMOTE_PACKAGE_FINGERPRINT = fingerprint
    _REMOTE_SESSION_ID = session_id
    _REMOTE_SESSION_TOKEN = token


def _render_artifacts(errors: list[tuple[str, str]]) -> None:
    try:
        monitor = get_monitor()
        if monitor is None or not monitor.enabled:
            errors.append(("stop.monitor", "InactiveMonitor"))
            return
        artifact = _capture_report(monitor, _resolve_report_analysis(None))
    except BaseException as exc:
        errors.append(("report.capture", type_name(exc)))
        return
    if "text" in _REMOTE_FORMATS and _REMOTE_TEXT_ARTIFACT is not None:
        try:
            _report.write_report(
                artifact,
                _REMOTE_TEXT_ARTIFACT,
                format="text",
                color=_REMOTE_TEXT_COLOR,
            )
        except BaseException as exc:
            errors.append(("report.text", type_name(exc)))
    if "json" in _REMOTE_FORMATS and _REMOTE_JSON_ARTIFACT is not None:
        try:
            _report.write_report(artifact, _REMOTE_JSON_ARTIFACT, format="json", color="never")
        except BaseException as exc:
            errors.append(("report.json", type_name(exc)))


def _stop_monitor(errors: list[tuple[str, str]]) -> None:
    if _REMOTE_RECOVERY_REGISTERED:
        try:
            atexit.unregister(_recover_at_exit)
        except BaseException as exc:
            errors.append(("stop.atexit", type_name(exc)))
    try:
        uninstall()
    except BaseException as exc:
        errors.append(("stop.uninstall", type_name(exc)))


def _monitor_configuration(manifest: dict[str, object]) -> tuple[CaptureConfig, AnalysisConfig, bool]:
    capture = _manifest_mapping(manifest, "capture")
    detailed = _manifest_mapping(capture, "detailed")
    analysis = _manifest_mapping(manifest, "analysis")
    return (
        CaptureConfig(
            import_audit=_required_bool(capture, "import_audit"),
            meta_path=_required_bool(capture, "meta_path"),
            finder_attribution=_required_bool(capture, "finder_attribution"),
            path_hooks=_required_bool(capture, "path_hooks"),
            importer_cache=_required_bool(capture, "importer_cache"),
            sys_path=_required_bool(capture, "sys_path"),
            detailed=DetailedCaptureConfig(
                enabled=False,
                path_hooks=_required_bool(detailed, "path_hooks"),
                path_entry_finders=_required_bool(detailed, "path_entry_finders"),
                loaders=_required_bool(detailed, "loaders"),
                import_results=_required_bool(detailed, "import_results"),
                import_calls=_required_bool(detailed, "import_calls"),
            ),
        ),
        AnalysisConfig(
            standard_path_check=_required_bool(analysis, "standard_path_check"),
            displaced_finder_check=_required_bool(analysis, "displaced_finder_check"),
        ),
        _required_bool(manifest, "unsafe_explore_import_branches"),
    )


def _package_is_owned(archive: Path, fingerprint: str) -> bool:
    root = sys.modules if type(sys.modules) is dict else None
    package = None if root is None else dict.get(root, "metapathology")
    if type(package) is not ModuleType:
        return False
    namespace = object.__getattribute__(package, "__dict__")
    filename = dict.get(namespace, "__file__")
    expected_prefix = os.fspath(archive)
    if type(filename) is str and filename.startswith(expected_prefix):
        return True
    return fingerprint == _REMOTE_PACKAGE_FINGERPRINT and _REMOTE_PROTOCOL_VERSION == PROTOCOL_VERSION


def _matches_active_session(manifest: dict[str, object]) -> bool:
    return (
        _REMOTE_ACTIVE
        and dict.get(manifest, "protocol_version") == _REMOTE_PROTOCOL_VERSION
        and dict.get(manifest, "package_fingerprint") == _REMOTE_PACKAGE_FINGERPRINT
        and dict.get(manifest, "session_id") == _REMOTE_SESSION_ID
        and dict.get(manifest, "token") == _REMOTE_SESSION_TOKEN
    )


def _safe_current_identity(errors: list[tuple[str, str]]) -> ProcessIdentity | None:
    try:
        return current_process_identity()
    except BaseException as exc:
        errors.append(("stop.identity", type_name(exc)))
        return None


def _manifest_mapping(mapping: dict[str, object], key: str) -> dict[str, object]:
    value = dict.get(mapping, key)
    if type(value) is not dict:
        raise ValueError(f"attachment manifest {key} is invalid")
    return value


def _manifest_string(
    mapping: dict[str, object],
    key: str,
    *,
    exact: int | None = None,
    maximum: int = 32_768,
) -> str:
    value = dict.get(mapping, key)
    if type(value) is not str or not value or len(value) > maximum or (exact is not None and len(value) != exact):
        raise ValueError(f"attachment manifest {key} is invalid")
    return value


def _manifest_path(mapping: dict[str, object], key: str) -> Path:
    return Path(_manifest_string(mapping, key))


def _manifest_formats(mapping: dict[str, object]) -> tuple[str, ...]:
    value = dict.get(mapping, "formats")
    if type(value) is not list or not value or any(item not in ("text", "json") for item in value):
        raise ValueError("attachment manifest formats are invalid")
    return tuple(dict.fromkeys(value))


def _required_bool(mapping: dict[str, object], key: str) -> bool:
    value = dict.get(mapping, key)
    if type(value) is not bool:
        raise ValueError(f"attachment manifest {key} is invalid")
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(131_072):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


_REMOTE_STOP_ENTRY = _stop
