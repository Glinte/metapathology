"""Process identity and bounded filesystem attachment protocol."""

import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from metapathology._attachment_process import (
    current_process_identity,
    identity_json,
    parse_identity,
    process_identity,
    same_process,
)
from metapathology._attachment_protocol import (
    MAX_STATUS_ERRORS,
    ensure_root,
    read_status,
    reserve_session,
    stage_package,
    write_status,
)


def test_current_process_identity_is_stable_and_round_trips() -> None:
    first = current_process_identity()
    second = process_identity(os.getpid())

    assert same_process(first, second)
    assert same_process(parse_identity(identity_json(first)), first)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"platform": "linux", "pid": True, "owner": "1", "birth": "2"},
        {"platform": "other", "pid": 1, "owner": "1", "birth": "2"},
        {"platform": "linux", "pid": 1, "owner": "", "birth": "2"},
    ],
)
def test_process_identity_rejects_malformed_documents(value: object) -> None:
    with pytest.raises(ValueError, match="process identity"):
        parse_identity(value)


def test_private_root_and_pid_session_are_reserved_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("metapathology._attachment_protocol.tempfile.gettempdir", lambda: os.fspath(tmp_path))
    identity = current_process_identity()

    root = ensure_root(identity.owner)
    paths = reserve_session(root, 123)

    assert paths.session.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
    with pytest.raises(FileExistsError):
        reserve_session(root, 123)


def test_staged_package_is_reproducible_and_contains_only_runtime_sources(tmp_path: Path) -> None:
    root = tmp_path / "attachment"
    (root / "packages").mkdir(parents=True)

    first = stage_package(root)
    second = stage_package(root)

    assert first.path == second.path
    assert first.fingerprint == second.fingerprint
    assert hashlib.sha256(first.path.read_bytes()).hexdigest() == first.fingerprint
    with zipfile.ZipFile(first.path) as archive:
        names = archive.namelist()
    assert names == sorted(names)
    assert "metapathology/__init__.py" in names
    assert all(name.startswith("metapathology/") for name in names)
    assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names)


def test_target_status_has_fixed_error_capacity(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    errors = tuple((f"location-{index}", f"Error{index}") for index in range(MAX_STATUS_ERRORS + 3))

    write_status(path, "failed", errors=errors)
    status = read_status(path)

    assert status is not None
    projected = status["errors"]
    assert isinstance(projected, list)
    assert len(projected) == MAX_STATUS_ERRORS
    assert status["error_overflow"] == 3
    assert "location-16" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "replacement",
    [
        {"protocol_version": 2},
        {"errors": [{"where": "location", "exception_type_name": "x" * 257}]},
        {"errors": [{"where": "location", "exception_type_name": "Error"}] * (MAX_STATUS_ERRORS + 1)},
        {"error_overflow": -1},
    ],
)
def test_target_status_rejects_unbounded_or_mismatched_fields(
    replacement: dict[str, object],
    tmp_path: Path,
) -> None:
    path = tmp_path / "status.json"
    document: dict[str, object] = {
        "protocol_version": 1,
        "state": "failed",
        "installed_at": None,
        "errors": [],
        "error_overflow": 0,
    }
    document.update(replacement)
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="attachment status"):
        read_status(path)
