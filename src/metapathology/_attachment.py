"""Controller for remotely owned metapathology capture sessions."""

import hashlib
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

from metapathology._attachment_process import (
    ProcessIdentity,
    current_process_identity,
    parse_identity,
    process_identity,
    same_process,
)
from metapathology._attachment_protocol import (
    PROTOCOL_VERSION,
    SessionPaths,
    _write_atomic_bytes,
    ensure_root,
    read_controller_status,
    read_manifest,
    read_status,
    reserve_session,
    session_paths,
    stage_package,
    write_controller_status,
    write_manifest,
    write_process_lease,
)
from metapathology._config import (
    AnalysisConfig,
    CaptureConfig,
    ReportTarget,
    _UnresolvedReportingOptions,
    resolve_install_request,
)
from metapathology._record import _Record
from metapathology._runtime import _pid_destination

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Literal

    from metapathology._config import ResolvedCaptureConfig


class AttachmentOptions(_Record):
    """Resolved CLI inputs needed to create one remote session."""

    report_destination: list[str]
    report_text: list[str]
    report_json: list[str]
    report_color: "Literal['auto', 'always', 'never'] | None"
    capture: CaptureConfig
    analysis: AnalysisConfig
    unsafe_explore_import_branches: bool | None
    wait_timeout: float | None


def attach(pid: int, options: AttachmentOptions) -> int:
    """Schedule monitoring in ``pid`` and wait for target acknowledgement."""
    _require_remote_exec()
    controller = current_process_identity()
    target = process_identity(pid)
    _require_same_owner(controller, target)
    root = ensure_root(controller.owner)
    _prune_package_cache(root)
    paths = reserve_session(root, pid)
    try:
        package = stage_package(root)
        request = resolve_install_request(
            reporting=_UnresolvedReportingOptions(
                False,
                options.report_destination,
                options.report_text,
                options.report_json,
                options.report_color,
            ),
            capture=options.capture,
            analysis=options.analysis,
            use_environment=True,
            configure_report=True,
            current_report_targets=(),
            unsafe_explore_import_branches=options.unsafe_explore_import_branches,
        )
        if request.unsafe_explore_import_branches:
            sys.stderr.write(
                "UNSAFE: attached capture will invoke skipped foreign finders and hooks; "
                "their side effects can change the target process\n"
            )
        targets = request.report_targets or (ReportTarget(None, "text", options.report_color or "auto"),)
        outputs = _output_manifest(targets, pid)
        formats = list(dict.fromkeys(output["format"] for output in outputs))
        token = secrets.token_hex(32)
        session_id = hashlib.sha256(token.encode("ascii")).hexdigest()[:16]
        manifest: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": session_id,
            "token": token,
            "pid": pid,
            "process_identity": _identity_document(target),
            "package_archive": os.fspath(package.path),
            "package_fingerprint": package.fingerprint,
            "status_path": os.fspath(paths.status),
            "text_artifact": os.fspath(paths.text_artifact),
            "json_artifact": os.fspath(paths.json_artifact),
            "formats": formats,
            "text_color": "always" if any(target.color == "always" for target in targets) else "never",
            "outputs": outputs,
            "capture": _capture_document(request.capture),
            "analysis": {
                "standard_path_check": request.analysis.standard_path_check,
                "displaced_finder_check": request.analysis.displaced_finder_check,
            },
            "unsafe_explore_import_branches": request.unsafe_explore_import_branches,
        }
        write_manifest(paths.manifest, manifest)
        _write_atomic_bytes(paths.start_script, _start_script(manifest, paths).encode("utf-8"))
        _write_atomic_bytes(paths.stop_script, _stop_script(paths).encode("utf-8"))
        write_controller_status(paths.controller_status, "start_pending")
        try:
            _remote_exec(pid, paths.start_script)
        except BaseException:
            write_controller_status(paths.controller_status, "terminal")
            _remove_session(paths)
            _prune_package_cache(root)
            raise
        _verify_birth(target)
        return _wait_for_start(paths, target, options.wait_timeout)
    except BaseException:
        if paths.session.exists() and not paths.manifest.exists():
            _remove_session(paths)
        raise


def stop(pid: int, *, wait_timeout: float | None, discard: bool = False) -> int:
    """Stop or reconcile one PID-scoped remote session."""
    _require_remote_exec()
    controller = current_process_identity()
    root = ensure_root(controller.owner)
    paths = session_paths(root, pid)
    manifest = read_manifest(paths.manifest)
    expected = parse_identity(dict.get(manifest, "process_identity"))
    _require_same_owner(controller, expected)
    target_status = read_status(paths.status)
    controller_status = read_controller_status(paths.controller_status)
    current = _current_identity_or_none(pid)
    alive = current is not None and same_process(current, expected)
    if discard:
        if alive and dict.get(controller_status, "state") in ("start_pending", "active", "stop_pending"):
            raise RuntimeError("cannot discard a session that may still execute in the target")
        _remove_session(paths)
        _prune_package_cache(root)
        return 0
    if _terminal(target_status):
        write_controller_status(
            paths.controller_status,
            "terminal",
            delivered=_delivered_indexes(controller_status),
        )
        return _deliver(paths, manifest)
    state = dict.get(controller_status, "state")
    if state == "start_pending":
        result = _wait_for_start(paths, expected, wait_timeout)
        if result != 0:
            return result
        controller_status = read_controller_status(paths.controller_status)
        state = dict.get(controller_status, "state")
        current = _current_identity_or_none(pid)
        alive = current is not None and same_process(current, expected)
    if not alive:
        raise RuntimeError("the original target process is no longer available; use stop --discard to clean up")
    if state == "active":
        write_controller_status(
            paths.controller_status,
            "stop_pending",
            delivered=_delivered_indexes(controller_status),
        )
        try:
            _remote_exec(pid, paths.stop_script)
        except BaseException:
            write_controller_status(
                paths.controller_status,
                "active",
                delivered=_delivered_indexes(controller_status),
            )
            raise
        _verify_birth(expected)
    return _wait_for_stop(paths, expected, wait_timeout)


def _wait_for_start(paths: SessionPaths, expected: ProcessIdentity, timeout: float | None) -> int:
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            status = read_status(paths.status)
            if status is not None:
                state = dict.get(status, "state")
                if state == "active":
                    acknowledged = parse_identity(dict.get(status, "process_identity"))
                    if not same_process(acknowledged, expected):
                        raise RuntimeError("target acknowledgement came from a different process birth")
                    manifest = read_manifest(paths.manifest)
                    write_process_lease(
                        paths.root,
                        expected,
                        _required_string(manifest, "package_fingerprint", exact=64),
                    )
                    write_controller_status(paths.controller_status, "active")
                    sys.stderr.write(
                        f"metapathology attachment {dict.get(manifest, 'session_id')} is active in PID {expected.pid}\n"
                    )
                    return 0
                if state in ("complete", "failed"):
                    write_controller_status(paths.controller_status, "terminal")
                    if state == "complete":
                        return _deliver(paths, read_manifest(paths.manifest))
                    _write_target_errors(status)
                    return 1
            if deadline is not None and time.monotonic() >= deadline:
                _print_pending(paths, "start")
                return 3
            time.sleep(0.05)
    except KeyboardInterrupt:
        _print_pending(paths, "start")
        return 3


def _wait_for_stop(paths: SessionPaths, expected: ProcessIdentity, timeout: float | None) -> int:
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            status = read_status(paths.status)
            if _terminal(status):
                controller = read_controller_status(paths.controller_status)
                write_controller_status(
                    paths.controller_status,
                    "terminal",
                    delivered=_delivered_indexes(controller),
                )
                return _deliver(paths, read_manifest(paths.manifest))
            current = _current_identity_or_none(expected.pid)
            if current is None or not same_process(current, expected):
                status = read_status(paths.status)
                if _terminal(status):
                    continue
                raise RuntimeError("the target exited without producing an orderly-shutdown report")
            if deadline is not None and time.monotonic() >= deadline:
                _print_pending(paths, "stop")
                return 3
            time.sleep(0.05)
    except KeyboardInterrupt:
        _print_pending(paths, "stop")
        return 3


def _deliver(paths: SessionPaths, manifest: dict[str, object]) -> int:
    outputs = dict.get(manifest, "outputs")
    if type(outputs) is not list:
        raise ValueError("attachment outputs are invalid")
    controller = read_controller_status(paths.controller_status)
    delivered = set(_delivered_indexes(controller))
    failed = False
    for index, output in enumerate(outputs):
        if index in delivered:
            continue
        if type(output) is not dict:
            raise ValueError("attachment output is invalid")
        format_ = dict.get(output, "format")
        destination = dict.get(output, "destination")
        artifact = paths.text_artifact if format_ == "text" else paths.json_artifact
        try:
            contents = artifact.read_bytes()
            if destination is None:
                sys.stderr.write(contents.decode("utf-8"))
            elif type(destination) is str:
                _write_atomic_bytes(Path(destination), contents)
            else:
                raise ValueError("attachment output destination is invalid")
        except BaseException as exc:
            failed = True
            sys.stderr.write(f"metapathology: failed to deliver output {index}: {type(exc).__name__}\n")
            continue
        delivered.add(index)
        write_controller_status(paths.controller_status, "terminal", delivered=sorted(delivered))
    status = read_status(paths.status)
    if status is not None and dict.get(status, "state") == "failed":
        _write_target_errors(status)
        failed = True
    if failed or len(delivered) != len(outputs):
        return 1
    root = paths.root
    _remove_session(paths)
    _prune_package_cache(root)
    return 0


def _output_manifest(targets: tuple[ReportTarget, ...], pid: int) -> list[dict[str, object]]:
    outputs: list[dict[str, object]] = []
    cwd = Path.cwd()
    for target in targets:
        destination = target.destination
        if destination is not None and destination != "-":
            destination = _pid_destination(destination, pid)
            path = Path(destination)
            destination = os.fspath(path if path.is_absolute() else cwd / path)
        elif destination == "-":
            destination = None
        outputs.append({"format": target.format, "destination": destination})
    return outputs


def _capture_document(capture: "ResolvedCaptureConfig") -> dict[str, object]:
    return {
        "import_audit": capture.import_audit,
        "meta_path": capture.meta_path,
        "finder_attribution": capture.finder_attribution,
        "path_hooks": capture.path_hooks,
        "importer_cache": capture.importer_cache,
        "sys_path": capture.sys_path,
        "detailed": {
            "path_hooks": capture.detailed.path_hooks,
            "path_entry_finders": capture.detailed.path_entry_finders,
            "loaders": capture.detailed.loaders,
            "import_results": capture.detailed.import_results,
            "import_calls": capture.detailed.import_calls,
        },
    }


def _start_script(manifest: dict[str, object], paths: SessionPaths) -> str:
    archive = _required_string(manifest, "package_archive")
    fingerprint = _required_string(manifest, "package_fingerprint", exact=64)
    return f"""# Generated by metapathology attachment protocol {PROTOCOL_VERSION}.
import hashlib as _h
import json as _j
import os as _o
import sys as _s
_archive = {archive!r}
_manifest = {os.fspath(paths.manifest)!r}
_status = {os.fspath(paths.status)!r}
def _failed(_exc):
    try:
        _doc = {{"protocol_version": {PROTOCOL_VERSION}, "state": "failed", "installed_at": None,
                "errors": [{{"where": "start.bootstrap", "exception_type_name": type(_exc).__name__[:256]}}],
                "error_overflow": 0}}
        _tmp = _status + ".bootstrap.tmp"
        with open(_tmp, "w", encoding="utf-8") as _stream:
            _j.dump(_doc, _stream, sort_keys=True)
            _stream.write("\\n")
        _o.replace(_tmp, _status)
    except BaseException:
        pass
try:
    with open(_archive, "rb") as _stream:
        if _h.file_digest(_stream, "sha256").hexdigest() != {fingerprint!r}:
            raise RuntimeError("package fingerprint mismatch")
    _modules = _s.modules
    _existing = dict.get(_modules, "metapathology") if type(_modules) is dict else object()
    _added = False
    if _existing is None:
        list.insert(_s.path, 0, _archive)
        _added = True
    try:
        from metapathology import _attachment_agent as _agent
    finally:
        if _added:
            try:
                list.remove(_s.path, _archive)
            except (ValueError, TypeError):
                pass
    _cache = _s.path_importer_cache
    if type(_cache) is dict:
        for _key in list(_cache):
            if type(_key) is str and (
                _key == _archive
                or _key.startswith(_archive + _o.sep)
                or _key.startswith(_archive + "/")
            ):
                dict.pop(_cache, _key, None)
    _agent.start(_manifest)
except BaseException as _exc:
    _failed(_exc)
"""


def _stop_script(paths: SessionPaths) -> str:
    return f"""# Generated by metapathology attachment protocol {PROTOCOL_VERSION}.
import sys as _s
try:
    _modules = _s.modules
    _agent = dict.get(_modules, "metapathology._attachment_agent") if type(_modules) is dict else None
    if type(_agent) is type(_s):
        _namespace = object.__getattribute__(_agent, "__dict__")
        _entry = dict.get(_namespace, "_REMOTE_STOP_ENTRY")
        if callable(_entry):
            _entry({os.fspath(paths.manifest)!r})
except BaseException:
    pass
"""


def _remote_exec(pid: int, script: Path) -> None:
    remote_exec = getattr(sys, "remote_exec", None)
    if remote_exec is None:
        raise RuntimeError("remote attachment requires CPython 3.14 or newer")
    remote_exec(pid, script)


def _require_remote_exec() -> None:
    if sys.implementation.name != "cpython" or sys.version_info < (3, 14) or not hasattr(sys, "remote_exec"):
        raise RuntimeError("remote attachment requires CPython 3.14 or newer")


def _require_same_owner(controller: ProcessIdentity, target: ProcessIdentity) -> None:
    if controller.platform != target.platform or controller.owner != target.owner:
        raise PermissionError("remote attachment supports only controller and target processes owned by the same user")


def _verify_birth(expected: ProcessIdentity) -> None:
    current = process_identity(expected.pid)
    if not same_process(current, expected):
        raise RuntimeError("target PID was reused during remote attachment")


def _current_identity_or_none(pid: int) -> ProcessIdentity | None:
    try:
        return process_identity(pid)
    except (OSError, ProcessLookupError):
        return None


def _identity_document(identity: ProcessIdentity) -> dict[str, object]:
    return {
        "platform": identity.platform,
        "pid": identity.pid,
        "owner": identity.owner,
        "birth": identity.birth,
    }


def _terminal(status: dict[str, object] | None) -> bool:
    return status is not None and dict.get(status, "state") in ("complete", "failed")


def _delivered_indexes(status: dict[str, object]) -> list[int]:
    value = dict.get(status, "delivered")
    if type(value) is not list or any(type(index) is not int or index < 0 for index in value):
        raise ValueError("controller delivered outputs are invalid")
    return value


def _required_string(
    mapping: dict[str, object],
    key: str,
    *,
    exact: int | None = None,
) -> str:
    value = dict.get(mapping, key)
    if type(value) is not str or not value or len(value) > 32_768 or (exact is not None and len(value) != exact):
        raise ValueError(f"attachment manifest {key} is invalid")
    return value


def _write_target_errors(status: dict[str, object]) -> None:
    errors = dict.get(status, "errors")
    if type(errors) is not list:
        return
    for error in errors:
        if type(error) is not dict:
            continue
        where = dict.get(error, "where")
        exception = dict.get(error, "exception_type_name")
        if type(where) is str and type(exception) is str:
            sys.stderr.write(f"metapathology target error at {where}: {exception}\n")


def _print_pending(paths: SessionPaths, operation: str) -> None:
    manifest = read_manifest(paths.manifest)
    sys.stderr.write(
        f"metapathology {operation} is still pending for session {dict.get(manifest, 'session_id')}; "
        f"run `metapathology stop {dict.get(manifest, 'pid')}` to reconcile it\n"
    )


def _remove_session(paths: SessionPaths) -> None:
    resolved_root = paths.root.resolve()
    resolved_session = paths.session.resolve()
    if resolved_session.parent != resolved_root or resolved_session.name != f"pid-{_session_pid(paths)}":
        raise OSError("refusing to remove an attachment path outside its owned root")
    shutil.rmtree(resolved_session)


def _session_pid(paths: SessionPaths) -> int:
    prefix, separator, value = paths.session.name.partition("-")
    if prefix != "pid" or separator != "-" or not value.isdecimal():
        raise ValueError("attachment session directory name is invalid")
    return int(value)


def _prune_package_cache(root: Path) -> None:
    referenced: set[str] = set()
    for session in root.glob("pid-*"):
        manifest_path = session / "manifest.json"
        try:
            manifest = read_manifest(manifest_path)
            referenced.add(_required_string(manifest, "package_fingerprint", exact=64))
        except (OSError, ValueError):
            continue
    for lease in (root / "leases").glob("*.json"):
        try:
            value = json.loads(lease.read_text(encoding="utf-8"))
            if type(value) is not dict:
                raise ValueError
            identity = parse_identity(dict.get(value, "process_identity"))
            fingerprint = _required_string(value, "package_fingerprint", exact=64)
            current = _current_identity_or_none(identity.pid)
            if current is None or not same_process(current, identity):
                lease.unlink()
                continue
            referenced.add(fingerprint)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    for archive in (root / "packages").glob("*.zip"):
        if archive.stem not in referenced:
            archive.unlink()
