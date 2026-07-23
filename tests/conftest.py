from pathlib import Path

import pytest
from support import PythonRunner, TempProject

# The monitor's behavior (audit hooks are per-process and irremovable, import
# state is global) can only be observed cleanly in a fresh interpreter, so
# tests run scripts via subprocess instead of touching install() in-process.


@pytest.fixture
def python_runner(tmp_path: Path) -> PythonRunner:
    """Provide a bounded fresh-interpreter runner rooted at ``tmp_path``."""
    return PythonRunner(tmp_path)


@pytest.fixture
def temp_project(tmp_path: Path) -> TempProject:
    """Provide a safe UTF-8 source-tree builder rooted at ``tmp_path``."""
    return TempProject(tmp_path)
