"""Isolated child scenarios extracted from tests/monitoring/test_sys_path.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "sys_path_monitor_is_opt_in_reversible_and_reports_mutations":
    import json, sys, metapathology

    original = sys.path
    monitor = metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(sys_path=True))
    instrumented = sys.path
    assert instrumented is not original and isinstance(instrumented, list)
    sys.path.insert(0, sys.argv[1])
    sys.path.append(object())
    sys.path.pop()
    replacement = list(sys.path)
    sys.path = replacement
    import sys_path_recovery_target

    assert sys.path is not replacement and isinstance(sys.path, list)
    document = metapathology.get_report()
    mutations = [event for event in document["timeline"] if event["kind"] == "sys_path_change"]
    assert [event["data"]["op"] for event in mutations] == ["insert", "append", "pop"]
    assert mutations[1]["data"]["added"] == ["<object>"]
    reassignment = next(event for event in document["timeline"] if event["kind"] == "sys_path_replacement")
    assert reassignment["data"]["during_import"] == "sys_path_recovery_target"
    mechanism = next(item for item in document["capture"]["mechanisms"] if item["name"] == "sys_path_changes")
    assert mechanism["enabled"] and mechanism["retained"] == 3
    text = metapathology.render_report()
    assert "-- sys.path changes (3) --" in text
    assert "-- sys.path replacements (1) --" in text
    metapathology.uninstall()
    assert type(sys.path) is list

elif _scenario == "default_install_preserves_plain_sys_path":
    import sys, metapathology

    original = sys.path
    monitor = metapathology.install(report_at_exit=False)
    assert sys.path is original and type(sys.path) is list
    assert not monitor.sys_path_enabled
    metapathology.uninstall()
else:
    raise ValueError(f"unknown scenario: {_scenario}")
