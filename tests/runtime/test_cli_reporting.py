"""CLI report presentation, destinations, and target correlation."""

import json
import subprocess
import sys
from pathlib import Path

from runtime.cli_support import run_cli
from support.process import DEFAULT_SUBPROCESS_TIMEOUT


def test_cli_color_modes_control_captured_text_output(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")

    automatic = run_cli(str(script), cwd=tmp_path)
    forced = run_cli("--color", "always", str(script), cwd=tmp_path)
    disabled = run_cli("--color", "never", str(script), cwd=tmp_path)

    assert "\x1b[" not in automatic.stderr
    assert "\x1b[1;36m== metapathology report ==\x1b[0m" in forced.stderr
    assert "\x1b[32mverdict:\x1b[0m" in forced.stderr
    assert "\x1b[" not in disabled.stderr


def test_failed_warning_report_colors_outcome_and_finding_separately(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import sys\n"
        "class LegacyFinder:\n"
        "    def find_module(self, fullname, path=None):\n"
        "        return None\n"
        "sys.meta_path.insert(0, LegacyFinder())\n"
        "import missing_color_dependency\n"
    )

    proc = run_cli("--color", "always", str(script), cwd=tmp_path)

    assert proc.returncode == 1
    assert "\x1b[1;31mtarget outcome:\x1b[0m" in proc.stderr
    assert "\x1b[33mverdict:\x1b[0m" in proc.stderr
    assert "\x1b[33m[legacy-finder-contract]\x1b[0m" in proc.stderr


def test_actionable_report_uses_red_compact_markers(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import importlib.machinery, sys, types\n"
        "class ReplacingLoader:\n"
        "    def create_module(self, spec):\n"
        "        return None\n"
        "    def exec_module(self, module):\n"
        "        module.LOADED = True\n"
        "class Finder:\n"
        "    def __init__(self):\n"
        "        self.loader = ReplacingLoader()\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname != 'color_replacement':\n"
        "            return None\n"
        "        return importlib.machinery.ModuleSpec(fullname, self.loader, origin='color.ext')\n"
        "finder = Finder()\n"
        "sys.meta_path.insert(0, finder)\n"
        "import color_replacement\n"
        "first = color_replacement\n"
        "spec = first.__spec__\n"
        "second = types.ModuleType('color_replacement')\n"
        "second.__spec__ = spec\n"
        "second.__loader__ = spec.loader\n"
        "sys.modules['color_replacement'] = second\n"
        "spec.loader.exec_module(second)\n"
        "sys.modules['color_replacement'] = first\n"
    )

    proc = run_cli("--deep-loaders", "--color", "always", str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "\x1b[32mtarget outcome:\x1b[0m" in proc.stderr
    assert "\x1b[1;31mverdict:\x1b[0m" in proc.stderr
    assert "\x1b[1;31m[repeated-loader-execution]\x1b[0m" in proc.stderr


def test_text_report_files_are_plain_in_auto_and_colored_when_forced(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    automatic_path = tmp_path / "automatic.txt"
    forced_path = tmp_path / "forced.txt"

    automatic = run_cli(
        "--report",
        str(automatic_path),
        str(script),
        cwd=tmp_path,
    )
    forced = run_cli(
        "--report",
        str(forced_path),
        "--color",
        "always",
        str(script),
        cwd=tmp_path,
    )

    assert automatic.returncode == 0, automatic.stderr
    assert forced.returncode == 0, forced.stderr
    automatic_report = next(tmp_path.glob("automatic.*.txt")).read_text(encoding="utf-8")
    forced_report = next(tmp_path.glob("forced.*.txt")).read_text(encoding="utf-8")
    assert "\x1b[" not in automatic_report
    assert "\x1b[1;36m== metapathology report ==\x1b[0m" in forced_report


def test_automatic_json_filename_includes_pid(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    destination = tmp_path / "worker.json"

    proc = run_cli("--report", str(destination), str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    assert "target ran" in proc.stdout
    assert proc.stderr == ""
    reports = list(tmp_path.glob("worker.*.json"))
    assert len(reports) == 1
    document = json.loads(reports[0].read_text(encoding="utf-8"))
    assert reports[0].name == f"worker.{document['process']['pid']}.json"


def test_text_file_report_and_target_option_passthrough(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import sys\nprint(sys.argv[1:])\n")
    destination = tmp_path / "worker.txt"

    proc = run_cli(
        "--report",
        str(destination),
        str(script),
        "--report",
        "target-value",
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "['--report', 'target-value']" in proc.stdout
    reports = list(tmp_path.glob("worker.*.txt"))
    assert len(reports) == 1
    assert "== metapathology report ==" in reports[0].read_text(encoding="utf-8")


def test_automatic_write_failure_does_not_replace_target_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("raise SystemExit(6)\n")
    destination = tmp_path / "missing" / "worker.json"

    proc = run_cli("--report", str(destination), str(script), cwd=tmp_path)

    assert proc.returncode == 6


def test_cli_writes_multiple_inferred_report_formats(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import json\nprint('target ran')\n")
    text_path = tmp_path / "diagnostic.txt"
    json_path = tmp_path / "diagnostic.json"

    proc = run_cli(
        "--report",
        str(text_path),
        "--report",
        str(json_path),
        str(script),
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    text_report = next(tmp_path.glob("diagnostic.*.txt")).read_text(encoding="utf-8")
    json_report = json.loads(next(tmp_path.glob("diagnostic.*.json")).read_text(encoding="utf-8"))
    assert "== metapathology report ==" in text_report
    assert json_report["process"]["pid"] > 0


def test_forced_stderr_text_and_json_file_are_both_written(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")
    destination = tmp_path / "machine.anything"

    proc = run_cli(
        "--report-text",
        "-",
        "--report-json",
        str(destination),
        str(script),
        cwd=tmp_path,
    )

    assert proc.returncode == 0, proc.stderr
    assert "== metapathology report ==" in proc.stderr
    report = next(tmp_path.glob("machine.*.anything"))
    assert json.loads(report.read_text(encoding="utf-8"))["process"]["pid"] > 0


def test_concurrent_workers_do_not_overwrite_reports(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("import time\ntime.sleep(0.2)\n")
    destination = tmp_path / "shared.json"
    commands = [
        sys.executable,
        "-m",
        "metapathology",
        "--report",
        str(destination),
        str(script),
    ]
    workers = [
        subprocess.Popen(
            commands,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=tmp_path,
        )
        for _ in range(2)
    ]

    completed = [worker.communicate(timeout=DEFAULT_SUBPROCESS_TIMEOUT) for worker in workers]

    assert [worker.returncode for worker in workers] == [0, 0], completed
    reports = list(tmp_path.glob("shared.*.json"))
    assert len(reports) == 2
    pids = {json.loads(report.read_text(encoding="utf-8"))["process"]["pid"] for report in reports}
    assert len(pids) == 2


def test_target_failure_is_correlated_with_unresolved_imports(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text(
        "import sys\n"
        "class LazyImporter:\n"
        "    def find_module(self, fullname, path=None):\n"
        "        return None\n"
        "sys.meta_path.insert(0, LazyImporter())\n"
        "import missing_lazy_dependency\n"
    )
    proc = run_cli(str(script), cwd=tmp_path)

    assert proc.returncode == 1
    assert "target outcome: raised ModuleNotFoundError for 'missing_lazy_dependency'" in proc.stderr
    assert "the failed module appears under unresolved imports below" in proc.stderr
    assert "most severe is [legacy-finder-contract] 'LazyImporter'" in proc.stderr
    assert "-- imports that started but produced no module" in proc.stderr
    assert "'missing_lazy_dependency': import started at event #" in proc.stderr
    assert "(the failed module)" in proc.stderr
    assert "LazyImporter was on sys.meta_path but accepts only the legacy find_module protocol" in proc.stderr

    destination = tmp_path / "report.json"
    proc = run_cli("--report", str(destination), str(script), cwd=tmp_path)
    assert proc.returncode == 1
    document = json.loads(next(tmp_path.glob("report.*.json")).read_text(encoding="utf-8"))
    assert document["target_outcome"] == {
        "kind": "raised",
        "exception_type_name": "ModuleNotFoundError",
        "missing_module": "missing_lazy_dependency",
        "exit_code": 1,
    }
    assert document["summary"]["unresolved_import_count"] >= 1
    assert document["summary"]["top_finding_ref"] == "finding:1"


def test_clean_target_reports_a_clean_verdict_in_the_first_two_lines(tmp_path: Path) -> None:
    script = tmp_path / "prog.py"
    script.write_text("print('target ran')\n")

    proc = run_cli(str(script), cwd=tmp_path)

    assert proc.returncode == 0, proc.stderr
    lines = proc.stderr.splitlines()
    start = lines.index("== metapathology report ==")
    assert lines[start + 1] == "target outcome: completed (exit status 0)"
    assert lines[start + 2].startswith("verdict: no import-hook interference detected across")
