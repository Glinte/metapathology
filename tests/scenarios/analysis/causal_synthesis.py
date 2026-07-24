"""Isolated child scenarios extracted from tests/analysis/test_causal_synthesis.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "namespace_failure":
    import importlib.machinery
    import json
    import os
    import sys

    import metapathology

    class TruncatingFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "synthesis_ns":
                return None
            spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [sys.argv[2]]
            return spec

    sys.path.insert(0, sys.argv[1])
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            detailed=metapathology.DetailedCaptureConfig(import_results=sys.argv[3] == "detailed"),
        ),
    )
    sys.meta_path.insert(0, TruncatingFinder())
    try:
        import synthesis_ns.child
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("truncated namespace unexpectedly found child")

    document = metapathology.get_report()
    comparison = next(item for item in document["finder_result_comparisons"] if item["only_in_right_result"])
    if sys.argv[3] == "detailed":
        finding = next(item for item in document["findings"] if item["kind"] == "missing_namespace_locations")
        assert finding["severity"] == "problem"
        assert "meta_path_short_circuit" in finding["signals"]
        assert finding["data"]["finder_result_comparison_ref"] == comparison["id"]
        explanation = document["explanations"][0]
        assert explanation["kind"] == "missing_namespace_locations_failure"
        assert explanation["subject"] == "synthesis_ns.child"
        assert explanation["cause_finding_ref"] == finding["id"]
        assert explanation["omitted_location"] == os.path.join(sys.argv[1], "synthesis_ns")
        assert explanation["candidate_path"] == os.path.join(explanation["omitted_location"], "child")
        assert explanation["confidence"] == "correlated"
        assert explanation["effect_status"] == "failed"
        assert explanation["next_observation"] is None
        text = metapathology.render_report()
        assert "[1] [correlated] 'synthesis_ns.child' failed" in text
        assert "    [missing-namespace-locations] 'synthesis_ns':" in text
        assert "why it matters:" in text
        assert "this is a problem finding based on correlated evidence" in text
        assert "cause: finding:" not in text
    else:
        assert not any(item["kind"] == "missing_namespace_locations" for item in document["findings"])
        assert not any(item["kind"] == "missing_namespace_locations_failure" for item in document["explanations"])
        text = metapathology.render_report()
        assert "modules found by a custom finder" in text
        assert "[missing-namespace-locations]" not in text
else:
    raise ValueError(f"unknown scenario: {_scenario}")
