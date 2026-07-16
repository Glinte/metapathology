"""Generated frozen bootstrap behavior and lifecycle."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from metapathology.frozen_bootstrap import generate, render

_SUBPROCESS_TIMEOUT = 30


@pytest.mark.parametrize("integration", ["cx-freeze", "embedded", "pyinstaller"])
def test_generated_activation_is_deterministic_and_valid(integration: str, tmp_path: Path) -> None:
    first = render(integration)  # type: ignore[arg-type]
    second = render(integration)  # type: ignore[arg-type]
    assert first == second
    compile(first, f"<{integration}>", "exec")

    output = tmp_path / f"{integration}.py"
    generated = generate(integration, output)  # type: ignore[arg-type]
    assert generated == str(output)
    assert output.read_text(encoding="utf-8") == first
    with pytest.raises(FileExistsError):
        generate(integration, output)  # type: ignore[arg-type]
    generate(integration, output, force=True)  # type: ignore[arg-type]
    assert output.read_text(encoding="utf-8") == first


def test_nuitka_launcher_requires_and_dispatches_a_module(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires an application module"):
        render("nuitka")
    source = render("nuitka", module="fixture_app")
    assert source.index("activate_frozen") < source.index("from fixture_app import main")
    compile(source, "<nuitka>", "exec")

    (tmp_path / "fixture_app.py").write_text("def main():\n    print('fixture-ran')\n", encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(source, encoding="utf-8")
    process = subprocess.run(
        [sys.executable, str(launcher)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "fixture-ran"


def test_nuitka_launcher_rejects_source_injection() -> None:
    with pytest.raises(ValueError, match="valid dotted Python identifiers"):
        render("nuitka", module="fixture_app; import bad")


def test_cx_freeze_adapter_exposes_the_init_script_protocol() -> None:
    source = render("cx-freeze")
    compile(source, "<cx-freeze>", "exec")
    assert "def run(name):" in source
    assert source.index("activate_frozen") < source.index("def run(name):")


def test_generated_activation_records_provenance_and_environment_configuration(tmp_path: Path) -> None:
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text(render("embedded"), encoding="utf-8")
    report = tmp_path / "frozen.json"
    environment = dict(os.environ)
    environment["METAPATHOLOGY_REPORT"] = str(report)
    environment["METAPATHOLOGY_REPORT_FORMAT"] = "json"
    process = subprocess.run(
        [sys.executable, str(bootstrap)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert process.returncode == 0, process.stderr
    reports = list(tmp_path.glob("frozen.*.json"))
    assert len(reports) == 1
    document = json.loads(reports[0].read_text(encoding="utf-8"))
    assert document["capture"]["frozen_bootstrap"] == {
        "boundary": "after freezer initialization",
        "integration": "embedded",
        "path": str(bootstrap),
    }


def test_generated_activation_failure_does_not_stop_application(tmp_path: Path) -> None:
    source = render("nuitka", module="fixture_app")
    (tmp_path / "fixture_app.py").write_text("def main():\n    print('continued')\n", encoding="utf-8")
    launcher = tmp_path / "launcher.py"
    launcher.write_text(source, encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = ""
    process = subprocess.run(
        [sys.executable, "-S", str(launcher)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert process.returncode == 0
    assert process.stdout.strip() == "continued"
    assert "activation failed: ModuleNotFoundError:" in process.stderr
