"""Isolated child scenarios extracted from tests/analysis/test_spec_comparison.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "namespace_truncation":
    import json
    import os
    import sys
    from importlib.machinery import ModuleSpec

    import metapathology

    class NamespaceLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            return None

    class TruncatingFinder:
        def __init__(self, name, location):
            self.name = name
            self.location = location

        def find_spec(self, fullname, path=None, target=None):
            if fullname != self.name:
                return None
            spec = ModuleSpec(fullname, NamespaceLoader(), is_package=True)
            spec.submodule_search_locations = [self.location]
            return spec

    name, first, second = sys.argv[1:]
    sys.path[:0] = [first, second]
    metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, TruncatingFinder(name, first + "/" + name))
    __import__(name)

    document = metapathology.get_report()
    results = [item for item in document["finder_results"] if item["module"] == name]
    captured = next(item for item in results if item["kind"] == "observed_finder_result")
    check = next(item for item in results if item["kind"] == "standard_path_check")
    assert captured["search_path_kind"] == "sys_path"
    assert captured["spec"]["locations_state"] == "captured"
    assert check["spec"]["locations_state"] == "current_state"
    comparison = next(
        item
        for item in document["finder_result_comparisons"]
        if item["left_result_ref"] == captured["id"] and item["right_result_ref"] == check["id"]
    )
    standard_only = comparison["only_in_right_result"]
    assert len(standard_only) == 1
    assert os.path.normcase(os.path.normpath(standard_only[0])) == os.path.normcase(
        os.path.normpath(second + "/" + name)
    )
    assert not any(item["module"] == name for item in document["findings"])
    text = metapathology.render_report()
    assert "modules found by a custom finder" in text, text
    assert "[missing-namespace-locations]" not in text, text

elif _scenario == "deferred_locations":
    import sys
    from importlib.machinery import ModuleSpec

    import metapathology
    from metapathology import MetaPathFinderCall

    class HostileLocations:
        def __iter__(self):
            raise AssertionError("hot-path iteration")

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            spec = ModuleSpec(fullname, loader=None, is_package=True)
            spec.submodule_search_locations = HostileLocations()
            return spec

    finder = Finder()
    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, finder)
    finder.find_spec("deferred_check")
    call = next(event for event in reversed(monitor.events()) if isinstance(event, MetaPathFinderCall))
    assert call.spec_summary.locations_state == "deferred"
    assert call.spec_summary.submodule_search_locations is None
else:
    raise ValueError(f"unknown scenario: {_scenario}")
