"""Isolated child scenarios extracted from tests/reporting/test_structured_report.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "json_report":
    import json
    import sys
    import types

    import metapathology

    class CheckFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    metapathology.install(report_at_exit=False)
    sys.path_importer_cache["structured-cache-check"] = None
    sys.meta_path = list(sys.meta_path)
    sys.path_hooks = list(sys.path_hooks)
    import reassignment_mod

    def check_hook(path):
        return None

    sys.path_hooks.append(check_hook)
    sys.meta_path.insert(0, CheckFinder())
    import observed_mod

    sys.modules["ghost_mod"] = types.ModuleType("ghost_mod")

    document = metapathology.get_report()
    assert document["schema"] == {"major": 3, "minor": 1, "name": "metapathology.report"}
    assert document["report_status"] == "complete"
    assert isinstance(document["finder_results"], list)
    assert isinstance(document["finder_result_comparisons"], list)
    assert document["capture"]["early_site_bootstrap"] is None
    assert document["capture"]["frozen_bootstrap"] is None
    assert document["capture"]["cutoff_seq"] == max(event["sequence"] for event in document["timeline"])
    assert document["snapshots"][0]["id"] == "snapshot:install"
    assert document["snapshots"][1]["id"] == "snapshot:report"
    kinds = {event["kind"] for event in document["timeline"]}
    assert "meta_path_change" in kinds, kinds
    assert "meta_path_replacement" in kinds, kinds
    assert "path_hooks_change" in kinds, kinds
    assert "path_hooks_replacement" in kinds, kinds
    assert "meta_path_finder_call" in kinds, kinds
    assert "importer_cache_change" in kinds, kinds
    assert "import_search_started" in kinds, kinds
    assert all(event["id"] == f"event:{event['sequence']}" for event in document["timeline"])
    stack_event = next(event for event in document["timeline"] if event["kind"] == "meta_path_change")
    assert set(stack_event["data"]["stack"][0]) == {"filename", "function", "lineno"}
    path_hook_event = next(event for event in document["timeline"] if event["kind"] == "path_hooks_change")
    assert path_hook_event["data"]["added"][0]["name"] == "check_hook"
    assert path_hook_event["data"]["added"][0]["object_id"].startswith("0x")
    snapshot_ids = {snapshot["id"] for snapshot in document["snapshots"]}
    assert "snapshot:path-hooks:install" in snapshot_ids
    assert "snapshot:path-hooks:report" in snapshot_ids
    assert "snapshot:importer-cache:install" in snapshot_ids
    assert "snapshot:importer-cache:report" in snapshot_ids
    mechanisms = {mechanism["name"]: mechanism for mechanism in document["capture"]["mechanisms"]}
    assert mechanisms["path_hooks_changes"]["overflow_policy"] == "retain_all"
    assert mechanisms["path_hooks_changes"]["retained"] >= 1
    assert mechanisms["finder_apis"]["overflow_policy"] == "retain_all"
    assert mechanisms["finder_apis"]["retained"] >= 4
    assert mechanisms["finder_result_analysis"]["overflow_policy"] == "retain_all"
    assert mechanisms["finder_result_analysis"]["shutdown"] == "synchronous_no_retry"
    assert all(mechanism["shutdown"] == "synchronous_no_retry" for mechanism in mechanisms.values())
    assert mechanisms["finder_result_analysis"]["retained"] == len(document["finder_results"])
    assert mechanisms["finder_result_analysis"]["comparison_count"] == len(document["finder_result_comparisons"])
    assert mechanisms["importer_cache_snapshots"]["capacity"] == 2
    assert mechanisms["importer_cache_snapshots"]["overflow_policy"] == "replace_latest"
    module_without_spec = next(finding for finding in document["findings"] if finding["kind"] == "module_without_spec")
    assert module_without_spec["module"] == "ghost_mod"
    assert module_without_spec["evidence"] == {
        "event_refs": [],
        "finder_call_status": "not_recorded",
        "level": "current_state",
        "limitations": ["report_snapshot_cannot_identify_load_mechanism"],
        "module_spec_status": "missing",
    }
    assert document["diagnostics"]["report_errors"] == []
    assert document["loader_inventory"]["evidence"] == "current_state"
    assert document["loader_inventory"]["phase"] == "report"
    assert document["loader_inventory"]["available"] is True

elif _scenario == "explicit_file":
    import json
    import pathlib
    import sys

    import metapathology

    destination = pathlib.Path(sys.argv[1])
    metapathology.install(report_at_exit=False)
    metapathology.write_report(destination, format="json")
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["process"]["pid"] > 0
    assert destination.exists()

elif _scenario == "report_contracts":
    import importlib.resources
    import json

    import metapathology
    from metapathology import _report
    from metapathology._report_json import failed_json_document
    from metapathology._runtime import _capture_report

    monitor = metapathology.install(report_at_exit=False)
    complete = metapathology.get_report()
    failed = failed_json_document("BrokenReport")
    assert set(failed) == set(complete)
    assert failed["report_status"] == "generation_failed"
    assert failed["standard_resolutions"] == []

    schema = json.loads(importlib.resources.files("metapathology").joinpath("report.schema.json").read_text())
    version = schema["$defs"]["SchemaVersion"]["properties"]
    assert version["major"]["const"] == complete["schema"]["major"]
    assert version["minor"]["const"] == complete["schema"]["minor"]
    assert set(schema["required"]) == set(complete)

    import colorsys

    artifact = _capture_report(monitor)
    cutoff = artifact.capture.cutoff_seq
    import fractions

    text = _report.render_report(artifact)
    document = _report.get_report(artifact)
    assert document["capture"]["cutoff_seq"] == cutoff
    assert not any(item["fullname"] == "fractions" for item in document["import_searches"])
    assert "metapathology report" in text

elif _scenario == "reference_report":
    import importlib
    import json
    import sys

    import metapathology

    metapathology.install(report_at_exit=False)
    for index in range(int(sys.argv[1])):
        importlib.import_module(f"generated_reference_{index}")
    document = metapathology.get_report()

    ids = set()
    for section_name in (
        "snapshots",
        "finder_apis",
        "import_searches",
        "finder_results",
        "finder_result_comparisons",
        "timeline",
        "findings",
        "explanations",
    ):
        for item in document[section_name]:
            assert item["id"] not in ids, item["id"]
            ids.add(item["id"])

    def check(value, location="report"):
        if isinstance(value, dict):
            for key, child in value.items():
                child_location = f"{location}.{key}"
                if key.endswith("_ref"):
                    assert child is None or child in ids, (child_location, child)
                elif key.endswith("_refs"):
                    assert isinstance(child, list)
                    assert all(reference in ids for reference in child), (child_location, child)
                else:
                    check(child, child_location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                check(child, f"{location}[{index}]")

    check(document)

elif _scenario == "write_failure":
    import pathlib
    import sys

    import metapathology
    from metapathology import MonitoringError

    monitor = metapathology.install(report_at_exit=False)
    try:
        metapathology.write_report(pathlib.Path(sys.argv[1]), format="json")
    except OSError:
        pass
    else:
        raise AssertionError("explicit write failure did not propagate")
    assert any(isinstance(event, MonitoringError) and event.where == "report_write" for event in monitor.events())
    document = metapathology.get_report()
    assert "monitoring_error" in {event["kind"] for event in document["timeline"]}
    assert document["diagnostics"]["monitoring_error_refs"]

else:
    raise ValueError(f"unknown scenario: {_scenario}")
