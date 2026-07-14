import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# The monitor's behavior (audit hooks are per-process and irremovable, import
# state is global) can only be observed cleanly in a fresh interpreter, so
# tests run scripts via subprocess instead of touching install() in-process.

# Below pytest-timeout's 30s so a hung subprocess fails with a readable
# TimeoutExpired instead of the whole test being killed.
SUBPROCESS_TIMEOUT = 25

# Signature of the run_python fixture's callable: (code, *argv) -> completed process.
RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


@pytest.fixture
def run_python(tmp_path: Path) -> RunPython:
    def run(code: str, *argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", code, *argv],
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=False,
            timeout=SUBPROCESS_TIMEOUT,
        )

    return run
