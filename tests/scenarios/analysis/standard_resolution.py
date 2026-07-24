"""Isolated child scenarios extracted from tests/analysis/test_standard_resolution.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "default_report_infers_namespace_before_later_finder":
    import json, sys, metapathology

    class LaterFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.append(LaterFinder())
    import standard_namespace

    document = metapathology.get_report()
    resolution = next(item for item in document["standard_resolutions"] if item["fullname"] == "standard_namespace")
    assert resolution["finder_type_name"] == "PathFinder"
    assert resolution["category"] == "namespace"
    assert resolution["evidence_level"] == "inferred"
    assert resolution["state_phase"] == "report"
    assert resolution["event_ref"] is None
    assert resolution["later_finders"] == ["LaterFinder"]
    assert not any(
        event["kind"] == "meta_path_finder_call"
        and event["data"]["fullname"] == "standard_namespace"
        and event["data"]["finder_type_name"] == "LaterFinder"
        for event in document["timeline"]
    )
    text = metapathology.render_report()
    assert "[inferred]" in text
    assert "later meta path entries were never reached: [LaterFinder]" in text

elif _scenario == "ordinary_standard_modules_do_not_finder_call_later_finders_are_relevant":
    import json, sys, metapathology

    class LaterFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    sys.meta_path.append(LaterFinder())
    sys.path.insert(0, sys.argv[1])
    import ordinary_source

    document = metapathology.get_report()
    resolution = next(item for item in document["standard_resolutions"] if item["fullname"] == "ordinary_source")
    assert resolution["later_finders"] == []
    assert not any(
        item["kind"] == "path_finder_precedence" and item["subject"] == "ordinary_source"
        for item in document["explanations"]
    )

elif _scenario == "detailed_report_explains_namespace_candidate_hidden_by_later_regular_module":
    import json, sys, metapathology

    sys.path[:0] = sys.argv[1:3]
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(enabled=True)),
    )
    import candidate_pkg

    try:
        import candidate_pkg.child
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("regular module unexpectedly allowed a child import")
    try:
        import candidate_pkg.other
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("regular module unexpectedly allowed another child import")
    document = metapathology.get_report()
    finding = next(item for item in document["findings"] if item["kind"] == "module_hides_namespace")
    assert finding["module"] == "candidate_pkg"
    assert finding["severity"] == "problem"
    explanations = [item for item in document["explanations"] if item["kind"] == "namespace_candidate_hidden"]
    assert {item["subject"] for item in explanations} == {"candidate_pkg.child", "candidate_pkg.other"}
    explanation = explanations[0]
    assert explanation["effect_status"] == "descendant_failed"
    assert explanation["cause_finding_ref"] == finding["id"]
    assert explanation["candidate_path"] == sys.argv[1]
    assert explanation["origin"] == sys.argv[3]
    assert explanation["confidence"] == "correlated"
    assert len(explanation["event_refs"]) >= 3
    text = metapathology.render_report()
    assert "namespace candidate from" in text
    assert "continued searching and selected the regular module" in text

elif _scenario == "detailed_report_correlates_repeated_failure_at_same_origin":
    import json, sys, metapathology

    sys.path.insert(0, sys.argv[1])
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(enabled=True)),
    )
    import repeated_source

    del sys.modules["repeated_source"]
    try:
        import repeated_source
    except RuntimeError:
        pass
    else:
        raise AssertionError("second import unexpectedly loaded")
    try:
        import repeated_source
    except RuntimeError:
        pass
    else:
        raise AssertionError("third import unexpectedly loaded")
    document = metapathology.get_report()
    finding = next(item for item in document["findings"] if item["kind"] == "module_failed_after_loading")
    assert finding["module"] == "repeated_source"
    assert finding["severity"] == "problem"
    assert finding["evidence"]["level"] == "correlated"
    assert {"same_loader", "same_origin", "earlier_search_loaded", "later_search_failed"} <= set(finding["signals"])
    explanation = next(item for item in document["explanations"] if item["kind"] == "module_failed_after_loading")
    assert explanation["cause_finding_ref"] == finding["id"]
    assert explanation["finder_type_name"] == "SourceFileLoader"
    assert explanation["origin"] == sys.argv[2]
    assert explanation["effect_status"] == "later_import_failed"
    failed_searches = [
        item
        for item in document["import_searches"]
        if item["fullname"] == "repeated_source" and item["progress"] == "failed"
    ]
    assert len(failed_searches) == 2
    assert {item["id"] for item in failed_searches} < set(finding["data"]["search_refs"])
    assert {ref for attempt in failed_searches for ref in attempt["evidence_event_refs"]} <= set(
        explanation["event_refs"]
    )
    text = metapathology.render_report()
    assert "same origin was selected again for 'repeated_source'" in text
    assert "the earlier import loaded, but the later import failed" in text
elif _scenario == "detailed_report_captures_source_resolution":
    import sys

    import metapathology

    module_dir = sys.argv[1]
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(enabled=True)),
    )
    sys.path.insert(0, module_dir)
    import standard_source

    document = metapathology.get_report()
    resolution = next(item for item in document["standard_resolutions"] if item["fullname"] == "standard_source")
    assert resolution["category"] == "source"
    assert resolution["loader_type_name"] == "SourceFileLoader"
    assert resolution["evidence_level"] == "observed"
    assert resolution["state_phase"] == "import"
    assert resolution["event_ref"] is not None
    assert resolution["component_event_refs"]
    component = next(event for event in document["timeline"] if event["id"] == resolution["component_event_refs"][0])
    assert component["data"]["path"] == module_dir
    text = metapathology.render_report()
    assert "PathFinder result capture: active path finder aggregate" in text
    assert "[observed]" in text
else:
    raise ValueError(f"unknown scenario: {_scenario}")
