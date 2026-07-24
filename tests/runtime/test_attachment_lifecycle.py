"""Fresh-process lifecycle for the staged target attachment agent."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from support.process import DEFAULT_SUBPROCESS_TIMEOUT

from metapathology._attachment import _start_script, _stop_script
from metapathology._attachment_process import identity_json, process_identity
from metapathology._attachment_protocol import (
    PROTOCOL_VERSION,
    _write_atomic_bytes,
    read_status,
    reserve_session,
    stage_package,
    write_manifest,
)


@pytest.mark.skipif(sys.version_info < (3, 14), reason="remote attachment requires CPython 3.14+")
def test_staged_agent_reports_future_import_and_restores_import_state(tmp_path: Path) -> None:
    root = tmp_path / "attachment"
    root.mkdir()
    package = stage_package(root)
    archive_path = package.path.as_posix()
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    restored = tmp_path / "restored"
    target_script = tmp_path / "target.py"
    target_script.write_text(
        """
import os
import pathlib
import sys
import time

root, ready, stop, restored = map(pathlib.Path, sys.argv[1:])
session = root / f"pid-{os.getpid()}"
ready_temporary = ready.with_name(f".{ready.name}.{os.getpid()}.tmp")
ready_temporary.write_text(str(os.getpid()), encoding="ascii")
ready_temporary.replace(ready)
start_script = session / "start.py"
while not start_script.exists():
    time.sleep(0.01)
exec(compile(start_script.read_bytes(), str(start_script), "exec"))
sys.path.insert(0, str(root.parent))
import attachment_probe
while not stop.exists():
    time.sleep(0.01)
stop_script = session / "stop.py"
exec(compile(stop_script.read_bytes(), str(stop_script), "exec"))
restored.write_text(
    str(type(sys.meta_path) is list and type(sys.path_hooks) is list and type(sys.path) is list),
    encoding="ascii",
)
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "attachment_probe.py").write_text("VALUE = 42\n", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-I", str(target_script), str(root), str(ready), str(stop), str(restored)],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_path(ready)
        target_pid = int(ready.read_text(encoding="ascii"))
        identity = process_identity(target_pid)
        paths = reserve_session(root, target_pid)
        manifest: dict[str, object] = {
            "protocol_version": PROTOCOL_VERSION,
            "session_id": "lifecycle-session",
            "token": "a" * 64,
            "pid": target_pid,
            "process_identity": identity_json(identity),
            "package_archive": archive_path,
            "package_fingerprint": package.fingerprint,
            "status_path": str(paths.status),
            "text_artifact": str(paths.text_artifact),
            "json_artifact": str(paths.json_artifact),
            "formats": ["text", "json"],
            "text_color": "never",
            "outputs": [],
            "capture": {
                "import_audit": True,
                "meta_path": True,
                "finder_attribution": True,
                "path_hooks": True,
                "importer_cache": True,
                "sys_path": True,
                "detailed": {
                    "path_hooks": False,
                    "path_entry_finders": False,
                    "loaders": False,
                    "import_results": False,
                    "import_calls": False,
                },
            },
            "analysis": {
                "standard_path_check": True,
                "displaced_finder_check": False,
            },
            "unsafe_explore_import_branches": False,
        }
        write_manifest(paths.manifest, manifest)
        _write_atomic_bytes(paths.start_script, _start_script(manifest, paths).encode())
        _write_atomic_bytes(paths.stop_script, _stop_script(paths).encode())
        _wait_for_state(paths.status, "active")
        stop.touch()
        stdout, stderr = process.communicate(timeout=DEFAULT_SUBPROCESS_TIMEOUT)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=DEFAULT_SUBPROCESS_TIMEOUT)

    assert process.returncode == 0, f"stdout:\n{stdout}\nstderr:\n{stderr}"
    assert restored.read_text(encoding="ascii") == "True"
    status = read_status(paths.status)
    assert status is not None
    assert status["state"] == "complete"
    raw_json = paths.json_artifact.read_text(encoding="utf-8")
    document = json.loads(raw_json)
    assert document["capture"]["remote_attachment"] == {
        "installed_at": document["capture"]["remote_attachment"]["installed_at"],
        "observation_boundary": "future_import_activity_after_attachment_only",
        "session_id": "lifecycle-session",
        "transport": "pep_768_remote_exec",
    }
    assert document["program_outcome"] is None
    assert "attachment_probe" in document["capture"]["modules_since_install"]
    text = paths.text_artifact.read_text(encoding="utf-8")
    assert "remote attachment session: lifecycle-session" in text
    assert "remote observation boundary: future import activity after attachment only" in text
    assert archive_path not in raw_json
    assert archive_path not in text


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + DEFAULT_SUBPROCESS_TIMEOUT
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {path}")
        time.sleep(0.01)


def _wait_for_state(path: Path, expected: str) -> None:
    deadline = time.monotonic() + DEFAULT_SUBPROCESS_TIMEOUT
    while True:
        status = read_status(path)
        if status is not None:
            state = status["state"]
            if state == expected:
                return
            if state == "failed":
                raise AssertionError(f"target attachment failed: {status}")
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for attachment state {expected!r}")
        time.sleep(0.01)
