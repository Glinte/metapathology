"""Isolated child scenarios extracted from tests/monitoring/test_finder_apis.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "contract_report":
    import json
    import sys

    import metapathology

    class LegacyFinder:
        def find_module(self, fullname, path=None):
            return None

    finder = LegacyFinder()
    before = dict(finder.__dict__)
    metapathology.install(report_at_exit=False)
    sys.meta_path.append(finder)
    replacement_finder = LegacyFinder()
    sys.meta_path = [replacement_finder, *sys.meta_path]
    import fractions

    document = metapathology.get_report()
    legacy_apis = [item for item in document["finder_apis"] if item["finder_type_name"] == "LegacyFinder"]
    observation = next(item for item in legacy_apis if item["finder_id"] == hex(id(finder)))
    assert observation["category"] == "legacy_only", observation
    assert observation["find_spec"] == {
        "availability": "absent",
        "defined_by": None,
        "evidence": "class_mro",
    }
    assert observation["find_module"]["availability"] == "callable"
    assert observation["observation"] == "change"
    assert observation["observation_event_ref"].startswith("event:")
    event_id = observation["observation_event_ref"]
    mutation = next(event for event in document["timeline"] if event["id"] == event_id)
    assert mutation["kind"] == "meta_path_change"
    assert mutation["data"]["added"] == ["LegacyFinder"]
    replacement_observation = next(item for item in legacy_apis if item["finder_id"] == hex(id(replacement_finder)))
    assert replacement_observation["observation"] == "replacement"
    assert replacement_observation["observation_event_ref"] is None
    finding = next(item for item in document["findings"] if item["kind"] == "legacy_finder_api")
    assert finding["subject"] == {"kind": "finder", "value": "LegacyFinder"}
    assert finding["data"]["finder_api_ref"] == f"finder-api:{hex(id(finder))}"
    assert any(
        observation["id"] == finding["data"]["finder_api_ref"] and observation["finder_id"] == hex(id(finder))
        for observation in document["finder_apis"]
    )
    assert finding["evidence"]["level"] == "observed"
    assert finding["evidence"]["event_refs"] == [event_id]
    text = metapathology.render_report()
    assert "[legacy-only] LegacyFinder" in text, text
    assert "[legacy-finder-api] LegacyFinder" in text, text
    assert "CPython 3.12+" in text, text
    metapathology.uninstall()
    assert finder.__dict__ == before
else:
    raise ValueError(f"unknown scenario: {_scenario}")
