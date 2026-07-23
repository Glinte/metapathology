"""Generated early-site bootstrap lifecycle and startup behavior."""

import json
import os
import subprocess
import sys
from pathlib import Path

_SUBPROCESS_TIMEOUT = 60
_BOOTSTRAP_NAME = "00_metapathology_early.pth"


def _run_manager(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "metapathology.site_bootstrap", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )


def test_install_status_and_remove_are_symmetric_and_idempotent(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    bootstrap = site_packages / _BOOTSTRAP_NAME

    first = _run_manager("install", "--site-packages", str(site_packages), cwd=tmp_path)
    assert first.returncode == 0, first.stderr
    contents = bootstrap.read_text(encoding="utf-8")
    assert "METAPATHOLOGY_EARLY_BOOTSTRAP" in contents
    assert "metapathology._early_bootstrap" in contents

    second = _run_manager("install", "--site-packages", str(site_packages), cwd=tmp_path)
    assert second.returncode == 0, second.stderr
    assert bootstrap.read_text(encoding="utf-8") == contents

    status = _run_manager("status", "--site-packages", str(site_packages), cwd=tmp_path)
    assert status.returncode == 0, status.stderr
    assert "installed" in status.stdout

    removed = _run_manager("remove", "--site-packages", str(site_packages), cwd=tmp_path)
    assert removed.returncode == 0, removed.stderr
    assert not bootstrap.exists()
    repeated = _run_manager("remove", "--site-packages", str(site_packages), cwd=tmp_path)
    assert repeated.returncode == 0, repeated.stderr


def test_manager_refuses_to_overwrite_or_remove_foreign_file(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    bootstrap = site_packages / _BOOTSTRAP_NAME
    bootstrap.write_text("# owned by somebody else\n", encoding="utf-8")

    installed = _run_manager("install", "--site-packages", str(site_packages), cwd=tmp_path)
    assert installed.returncode == 1
    assert "not owned by metapathology" in installed.stderr
    removed = _run_manager("remove", "--site-packages", str(site_packages), cwd=tmp_path)
    assert removed.returncode == 1
    assert bootstrap.read_text(encoding="utf-8") == "# owned by somebody else\n"


def test_manager_repairs_and_removes_owned_truncated_file(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    bootstrap = site_packages / _BOOTSTRAP_NAME

    installed = _run_manager("install", "--site-packages", str(site_packages), cwd=tmp_path)
    assert installed.returncode == 0, installed.stderr
    header = bootstrap.read_text(encoding="utf-8").splitlines()[0]
    bootstrap.write_text(f"{header}\n", encoding="utf-8")

    damaged = _run_manager("status", "--site-packages", str(site_packages), cwd=tmp_path)
    assert damaged.returncode == 1
    assert "damaged" in damaged.stdout
    repaired = _run_manager("install", "--site-packages", str(site_packages), cwd=tmp_path)
    assert repaired.returncode == 0, repaired.stderr
    assert "metapathology._early_bootstrap" in bootstrap.read_text(encoding="utf-8")

    bootstrap.write_text(f"{bootstrap.read_text(encoding='utf-8').splitlines()[0]}\n", encoding="utf-8")
    removed = _run_manager("remove", "--site-packages", str(site_packages), cwd=tmp_path)
    assert removed.returncode == 0, removed.stderr
    assert not bootstrap.exists()


def test_early_bootstrap_observes_later_pth_and_inherited_child(tmp_path: Path) -> None:
    environment = tmp_path / "target-env"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        capture_output=True,
        text=True,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert created.returncode == 0, created.stderr
    target_python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    queried = subprocess.run(
        [str(target_python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert queried.returncode == 0, queried.stderr
    site_packages = Path(queried.stdout.strip())

    source_root = Path(__file__).resolve().parents[2] / "src"
    (site_packages / "000_metapathology_source.pth").write_text(f"{source_root}\n", encoding="utf-8")
    (site_packages / "_earlier_hook.py").write_text(
        "import sys\n"
        "def earlier_hook(path):\n"
        "    raise ImportError\n"
        "sys.path_hooks.append(earlier_hook)\n"
        "sys.path_importer_cache['metapathology-before-bootstrap'] = None\n",
        encoding="utf-8",
    )
    (site_packages / "001_earlier_hook.pth").write_text("import _earlier_hook\n", encoding="utf-8")

    installed = _run_manager("install", "--site-packages", str(site_packages), cwd=tmp_path)
    assert installed.returncode == 0, installed.stderr

    (site_packages / "zz_later_hook.py").write_text(
        "import sys\n"
        "def later_hook(path):\n"
        "    raise ImportError\n"
        "sys.path_hooks.append(later_hook)\n"
        "sys.path_importer_cache['metapathology-after-bootstrap'] = None\n",
        encoding="utf-8",
    )
    (site_packages / "zz_later_hook.pth").write_text("import zz_later_hook\n", encoding="utf-8")

    report_path = tmp_path / "early-report.json"
    process_environment = dict(os.environ)
    process_environment["METAPATHOLOGY_EARLY_BOOTSTRAP"] = "1"
    process_environment["METAPATHOLOGY_REPORT"] = str(report_path)
    parent = subprocess.run(
        [
            str(target_python),
            "-c",
            "import subprocess, sys; subprocess.run([sys.executable, '-c', 'print(\"child\")'], check=True)",
        ],
        capture_output=True,
        text=True,
        env=process_environment,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert parent.returncode == 0, parent.stderr

    reports = list(tmp_path.glob("early-report.*.json"))
    assert len(reports) == 2
    for report in reports:
        document = json.loads(report.read_text(encoding="utf-8"))
        bootstrap = document["capture"]["early_site_bootstrap"]
        assert bootstrap["path"].endswith(_BOOTSTRAP_NAME)
        assert bootstrap["activation_source"] == "environment:METAPATHOLOGY_EARLY_BOOTSTRAP"
        assert bootstrap["site_packages"] == str(site_packages)
        assert "001_earlier_hook.pth" in bootstrap["earlier_pth_files"]
        path_hook_events = [event["data"] for event in document["timeline"] if event["kind"] == "path_hooks_change"]
        assert any(reference["name"] == "later_hook" for event in path_hook_events for reference in event["added"])
        assert not any(
            reference["name"] == "earlier_hook" for event in path_hook_events for reference in event["added"]
        )
        initial_names = {
            reference["name"]
            for snapshot in document["snapshots"]
            if snapshot["kind"] == "path_hooks" and snapshot["phase"] == "install"
            for reference in snapshot["data"]["entries"]
        }
        assert "earlier_hook" in initial_names
        cache_events = [event["data"] for event in document["timeline"] if event["kind"] == "importer_cache_change"]
        assert any(
            entry["path"] == "metapathology-after-bootstrap" for event in cache_events for entry in event["added"]
        )

    disabled_environment = dict(os.environ)
    disabled_environment["METAPATHOLOGY_REPORT"] = str(tmp_path / "disabled.json")
    disabled = subprocess.run(
        [str(target_python), "-c", "import sys; print('metapathology' in sys.modules)"],
        capture_output=True,
        text=True,
        env=disabled_environment,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert disabled.returncode == 0, disabled.stderr
    assert disabled.stdout.strip() == "False"
    assert not list(tmp_path.glob("disabled*.json"))

    without_site = subprocess.run(
        [str(target_python), "-S", "-c", "import sys; print('metapathology' in sys.modules)"],
        capture_output=True,
        text=True,
        env=process_environment,
        check=False,
        timeout=_SUBPROCESS_TIMEOUT,
    )
    assert without_site.returncode == 0, without_site.stderr
    assert without_site.stdout.strip() == "False"
