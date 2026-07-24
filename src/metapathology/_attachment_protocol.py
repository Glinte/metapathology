"""Versioned filesystem protocol shared by attachment controller and target."""

import hashlib
import io
import json
import os
import stat
import tempfile
import zipfile
from contextlib import suppress
from pathlib import Path

import metapathology
from metapathology._attachment_process import ProcessIdentity, identity_json, identity_key, parse_identity
from metapathology._record import _Record

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Literal

    AttachmentState = Literal["start_pending", "active", "stop_pending", "complete", "failed"]


PROTOCOL_VERSION = 1
MAX_STATUS_BYTES = 65_536
MAX_STATUS_ERRORS = 16
MAX_STATUS_VALUE = 256
_PACKAGE_SUFFIXES = frozenset((".py", ".json"))


class SessionPaths(_Record):
    """Owned paths for one PID-scoped attachment session."""

    root: Path
    session: Path
    manifest: Path
    status: Path
    controller_status: Path
    start_script: Path
    stop_script: Path
    text_artifact: Path
    json_artifact: Path


class PackageArchive(_Record):
    """Content-addressed package source retained for a live target."""

    path: Path
    fingerprint: str


def attachment_root(owner: str) -> Path:
    """Return the private root for one operating-system identity."""
    digest = hashlib.sha256(owner.encode("utf-8", "surrogatepass")).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / f"metapathology-attach-{digest}"


def ensure_root(owner: str) -> Path:
    """Create and validate the private per-user attachment root."""
    root = attachment_root(owner)
    with suppress(FileExistsError):
        root.mkdir(mode=0o700)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or root.is_symlink():
        raise OSError(f"attachment root is not a private directory: {root}")
    if os.name != "nt":
        if info.st_uid != os.geteuid():
            raise PermissionError(f"attachment root is owned by a different user: {root}")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError(f"attachment root permissions are too broad: {root}")
    (root / "packages").mkdir(mode=0o700, exist_ok=True)
    (root / "leases").mkdir(mode=0o700, exist_ok=True)
    return root


def session_paths(root: Path, pid: int) -> SessionPaths:
    """Return every path owned by a PID session."""
    session = root / f"pid-{pid}"
    return SessionPaths(
        root,
        session,
        session / "manifest.json",
        session / "target-status.json",
        session / "controller-status.json",
        session / "start.py",
        session / "stop.py",
        session / "report.txt",
        session / "report.json",
    )


def reserve_session(root: Path, pid: int) -> SessionPaths:
    """Atomically reserve the one allowed unfinished session for ``pid``."""
    paths = session_paths(root, pid)
    paths.session.mkdir(mode=0o700)
    return paths


def stage_package(root: Path) -> PackageArchive:
    """Build or reuse the content-addressed pure-Python package ZIP."""
    source = Path(metapathology.__file__).resolve().parent
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.name != "py.typed" and path.suffix not in _PACKAGE_SUFFIXES:
                continue
            relative = path.relative_to(source.parent).as_posix()
            info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    contents = buffer.getvalue()
    fingerprint = hashlib.sha256(contents).hexdigest()
    destination = root / "packages" / f"{fingerprint}.zip"
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != fingerprint:
            raise OSError(f"staged package fingerprint mismatch: {destination}")
        return PackageArchive(destination, fingerprint)
    _write_atomic_bytes(destination, contents)
    return PackageArchive(destination, fingerprint)


def write_manifest(path: Path, manifest: "Mapping[str, object]") -> None:
    """Write the immutable session manifest."""
    if path.exists():
        raise FileExistsError(path)
    encoded = _encode_json(manifest)
    _write_atomic_bytes(path, encoded)


def read_manifest(path: Path) -> dict[str, object]:
    """Read and minimally validate one immutable session manifest."""
    value = _read_json(path)
    if type(value) is not dict:
        raise ValueError("attachment manifest must be an object")
    if dict.get(value, "protocol_version") != PROTOCOL_VERSION:
        raise ValueError("attachment protocol version does not match")
    token = dict.get(value, "token")
    session_id = dict.get(value, "session_id")
    fingerprint = dict.get(value, "package_fingerprint")
    if type(token) is not str or len(token) != 64:
        raise ValueError("attachment token is invalid")
    if type(session_id) is not str or not 8 <= len(session_id) <= 64:
        raise ValueError("attachment session ID is invalid")
    if type(fingerprint) is not str or len(fingerprint) != 64:
        raise ValueError("attachment package fingerprint is invalid")
    parse_identity(dict.get(value, "process_identity"))
    return value


def write_status(
    path: Path,
    state: "AttachmentState",
    *,
    process_identity: ProcessIdentity | None = None,
    installed_at: str | None = None,
    errors: "Sequence[tuple[str, str]]" = (),
    error_overflow: int = 0,
) -> None:
    """Atomically replace the bounded target-written status."""
    projected_errors = [
        {"where": where[:MAX_STATUS_VALUE], "exception_type_name": exception[:MAX_STATUS_VALUE]}
        for where, exception in errors[:MAX_STATUS_ERRORS]
    ]
    overflow = error_overflow + max(0, len(errors) - MAX_STATUS_ERRORS)
    document: dict[str, object] = {
        "protocol_version": PROTOCOL_VERSION,
        "state": state,
        "installed_at": installed_at,
        "errors": projected_errors,
        "error_overflow": overflow,
    }
    if process_identity is not None:
        document["process_identity"] = identity_json(process_identity)
    encoded = _encode_json(document)
    if len(encoded) > MAX_STATUS_BYTES:
        raise ValueError("attachment status exceeds its fixed capacity")
    _write_atomic_bytes(path, encoded)


def read_status(path: Path) -> dict[str, object] | None:
    """Read a target status, returning ``None`` before acknowledgement."""
    try:
        value = _read_json(path, limit=MAX_STATUS_BYTES)
    except FileNotFoundError:
        return None
    if type(value) is not dict:
        raise ValueError("attachment status must be an object")
    if dict.get(value, "protocol_version") != PROTOCOL_VERSION:
        raise ValueError("attachment status protocol version does not match")
    state = dict.get(value, "state")
    if state not in ("start_pending", "active", "stop_pending", "complete", "failed"):
        raise ValueError("attachment status state is invalid")
    installed_at = dict.get(value, "installed_at")
    if installed_at is not None and (type(installed_at) is not str or len(installed_at) > MAX_STATUS_VALUE):
        raise ValueError("attachment status installation time is invalid")
    errors = dict.get(value, "errors")
    if type(errors) is not list or len(errors) > MAX_STATUS_ERRORS:
        raise ValueError("attachment status errors are invalid")
    for error in errors:
        if type(error) is not dict:
            raise ValueError("attachment status error is invalid")
        where = dict.get(error, "where")
        exception = dict.get(error, "exception_type_name")
        if (
            type(where) is not str
            or not where
            or len(where) > MAX_STATUS_VALUE
            or type(exception) is not str
            or not exception
            or len(exception) > MAX_STATUS_VALUE
        ):
            raise ValueError("attachment status error fields are invalid")
    error_overflow = dict.get(value, "error_overflow")
    if type(error_overflow) is not int or error_overflow < 0:
        raise ValueError("attachment status error overflow is invalid")
    identity = dict.get(value, "process_identity")
    if identity is not None:
        parse_identity(identity)
    return value


def write_controller_status(
    path: Path,
    state: str,
    *,
    delivered: "Sequence[int]" = (),
) -> None:
    """Persist controller-owned scheduling and delivery progress."""
    if state not in ("start_pending", "active", "stop_pending", "terminal"):
        raise ValueError("controller attachment state is invalid")
    if any(type(index) is not int or index < 0 for index in delivered):
        raise ValueError("delivered output indexes are invalid")
    encoded = _encode_json(
        {
            "protocol_version": PROTOCOL_VERSION,
            "state": state,
            "delivered": list(dict.fromkeys(delivered)),
        }
    )
    if len(encoded) > MAX_STATUS_BYTES:
        raise ValueError("controller status exceeds its fixed capacity")
    _write_atomic_bytes(path, encoded)


def read_controller_status(path: Path) -> dict[str, object]:
    """Read and validate controller-owned session progress."""
    value = _read_json(path)
    if type(value) is not dict or dict.get(value, "protocol_version") != PROTOCOL_VERSION:
        raise ValueError("controller attachment status is invalid")
    state = dict.get(value, "state")
    delivered = dict.get(value, "delivered")
    if state not in ("start_pending", "active", "stop_pending", "terminal"):
        raise ValueError("controller attachment state is invalid")
    if type(delivered) is not list or any(type(index) is not int or index < 0 for index in delivered):
        raise ValueError("controller delivered outputs are invalid")
    return value


def process_lease_path(root: Path, identity: ProcessIdentity) -> Path:
    """Return the content-source lease held for one target birth."""
    return root / "leases" / f"{identity_key(identity)}.json"


def write_process_lease(root: Path, identity: ProcessIdentity, fingerprint: str) -> None:
    """Retain package source until the referenced process birth is gone."""
    _write_atomic_bytes(
        process_lease_path(root, identity),
        _encode_json(
            {
                "protocol_version": PROTOCOL_VERSION,
                "process_identity": identity_json(identity),
                "package_fingerprint": fingerprint,
            }
        ),
    )


def _encode_json(value: "Mapping[str, object]") -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _read_json(path: Path, *, limit: int = MAX_STATUS_BYTES) -> object:
    with path.open("rb") as stream:
        contents = stream.read(limit + 1)
    if len(contents) > limit:
        raise ValueError(f"attachment file exceeds its capacity: {path}")
    return json.loads(contents)


def _write_atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary)
