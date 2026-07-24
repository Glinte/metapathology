"""Controller state transitions that do not require a live debugger transport."""

import os
from pathlib import Path

import pytest

from metapathology import _attachment
from metapathology._attachment_process import ProcessIdentity, current_process_identity, identity_json
from metapathology._attachment_protocol import (
    PROTOCOL_VERSION,
    SessionPaths,
    read_controller_status,
    reserve_session,
    write_controller_status,
    write_manifest,
    write_status,
)


def _manifest(
    identity: ProcessIdentity,
    session_paths: SessionPaths,
    *,
    outputs: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "session_id": "session1",
        "token": "a" * 64,
        "pid": identity.pid,
        "process_identity": identity_json(identity),
        "package_archive": os.fspath(session_paths.root / "packages" / f"{'b' * 64}.zip"),
        "package_fingerprint": "b" * 64,
        "status_path": os.fspath(session_paths.status),
        "text_artifact": os.fspath(session_paths.text_artifact),
        "json_artifact": os.fspath(session_paths.json_artifact),
        "formats": ["text"],
        "text_color": "never",
        "outputs": outputs,
        "capture": {},
        "analysis": {},
        "unsafe_explore_import_branches": False,
    }


def test_repeated_stop_waits_for_existing_pending_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    identity = current_process_identity()
    root = tmp_path / "root"
    root.mkdir()
    paths = reserve_session(root, identity.pid)
    write_manifest(paths.manifest, _manifest(identity, paths, outputs=[]))
    write_controller_status(paths.controller_status, "active")
    write_status(paths.status, "active", process_identity=identity, installed_at="2026-01-01T00:00:00Z")
    scheduled: list[tuple[int, Path]] = []
    monkeypatch.setattr(_attachment, "ensure_root", lambda _owner: root)
    monkeypatch.setattr(_attachment, "_remote_exec", lambda pid, script: scheduled.append((pid, script)))
    monkeypatch.setattr(_attachment, "_current_identity_or_none", lambda _pid: identity)

    assert _attachment.stop(identity.pid, wait_timeout=0) == 3
    assert _attachment.stop(identity.pid, wait_timeout=0) == 3

    assert scheduled == [(identity.pid, paths.stop_script)]
    assert read_controller_status(paths.controller_status)["state"] == "stop_pending"


def test_partial_delivery_retries_only_missing_destinations(tmp_path: Path) -> None:
    identity = current_process_identity()
    root = tmp_path / "root"
    root.mkdir()
    paths = reserve_session(root, identity.pid)
    blocked = tmp_path / "blocked.txt"
    blocked.mkdir()
    delivered = tmp_path / "delivered.txt"
    outputs: list[dict[str, object]] = [
        {"format": "text", "destination": os.fspath(blocked)},
        {"format": "text", "destination": os.fspath(delivered)},
    ]
    manifest = _manifest(identity, paths, outputs=outputs)
    write_manifest(paths.manifest, manifest)
    write_controller_status(paths.controller_status, "terminal")
    paths.text_artifact.write_text("one artifact\n", encoding="utf-8")
    write_status(paths.status, "complete", process_identity=identity)

    assert _attachment._deliver(paths, manifest) == 1
    first_mtime = delivered.stat().st_mtime_ns
    assert read_controller_status(paths.controller_status)["delivered"] == [1]

    blocked.rmdir()
    assert _attachment._deliver(paths, manifest) == 0
    assert blocked.read_text(encoding="utf-8") == "one artifact\n"
    assert delivered.stat().st_mtime_ns == first_mtime
    assert not paths.session.exists()
