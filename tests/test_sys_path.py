"""Opt-in ``sys.path`` mutation and reassignment observation."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., subprocess.CompletedProcess[str]]


def test_sys_path_monitor_is_opt_in_reversible_and_reports_mutations(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "sys_path_recovery_target.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = run_python(
        "import json, sys, metapathology\n"
        "original = sys.path\n"
        "monitor = metapathology.install(report_at_exit=False, monitor_sys_path=True)\n"
        "instrumented = sys.path\n"
        "assert instrumented is not original and isinstance(instrumented, list)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "sys.path.append(object())\n"
        "sys.path.pop()\n"
        "replacement = list(sys.path)\n"
        "sys.path = replacement\n"
        "import sys_path_recovery_target\n"
        "assert sys.path is not replacement and isinstance(sys.path, list)\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "mutations = [event for event in document['timeline'] if event['kind'] == 'sys_path_mutation']\n"
        "assert [event['op'] for event in mutations] == ['insert', 'append', 'pop']\n"
        "assert mutations[1]['added'] == ['<object>']\n"
        "reassignment = next(event for event in document['timeline'] "
        "if event['kind'] == 'sys_path_reassignment')\n"
        "assert reassignment['during_import'] == 'sys_path_recovery_target'\n"
        "mechanism = next(item for item in document['capture']['mechanisms'] "
        "if item['name'] == 'sys_path_mutations')\n"
        "assert mechanism['enabled'] and mechanism['retained'] == 3\n"
        "text = metapathology.render_report()\n"
        "assert '-- sys.path mutations (3) --' in text\n"
        "assert '-- sys.path reassignments (1) --' in text\n"
        "metapathology.uninstall()\n"
        "assert type(sys.path) is list\n"
        "print('OK')\n",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_default_install_preserves_plain_sys_path(run_python: RunPython) -> None:
    proc = run_python(
        "import sys, metapathology\n"
        "original = sys.path\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "assert sys.path is original and type(sys.path) is list\n"
        "assert not monitor.sys_path_enabled\n"
        "metapathology.uninstall()\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
