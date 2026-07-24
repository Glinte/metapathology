"""Isolated child scenarios extracted from tests/monitoring/test_detailed_import_results.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "shared_loader_uses_each_call_actual_module_name":
    import importlib.machinery
    import json
    import sys
    import metapathology

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.VALUE = module.__name__

    class Finder:
        def __init__(self, loader):
            self.loader = loader

        def find_spec(self, fullname, path=None, target=None):
            if fullname in {"shared_first", "shared_second"}:
                return importlib.machinery.ModuleSpec(fullname, self.loader)
            return None

    loader = Loader()
    finder = Finder(loader)
    sys.meta_path.insert(0, finder)
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(loaders=True)),
    )
    import shared_first, shared_second

    document = metapathology.get_report()
    calls = [
        event["data"]
        for event in document["timeline"]
        if event["kind"] == "import_mechanism_call" and event["data"]["boundary"].startswith("loader_")
    ]
    assert {(event["boundary"], event["fullname"]) for event in calls} == {
        ("loader_create_module", "shared_first"),
        ("loader_exec_module", "shared_first"),
        ("loader_create_module", "shared_second"),
        ("loader_exec_module", "shared_second"),
    }, calls
    assert shared_first.VALUE == "shared_first" and shared_second.VALUE == "shared_second"
    metapathology.uninstall()
    assert "create_module" not in loader.__dict__ and "exec_module" not in loader.__dict__

elif _scenario == "partial_detailed_install_and_hostile_cleanup_are_isolated":
    import sys
    import metapathology

    class HostileFinder:
        def __getattribute__(self, name):
            if name == "__dict__":
                raise RuntimeError("hostile")
            return object.__getattribute__(self, name)

        def find_spec(self, fullname, target=None):
            return None

    class OrdinaryFinder:
        def find_spec(self, fullname, target=None):
            return None

    ordinary = OrdinaryFinder()
    sys.path_importer_cache["hostile"] = HostileFinder()
    sys.path_importer_cache["ordinary"] = ordinary
    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            detailed=metapathology.DetailedCaptureConfig(
                path_hooks=True,
                path_entry_finders=True,
            )
        ),
    )
    assert "find_spec" in ordinary.__dict__

    class HostileHooks:
        def __iter__(self):
            raise RuntimeError("hostile cleanup")

    sys.path_hooks = HostileHooks()
    metapathology.uninstall()
    assert "find_spec" not in ordinary.__dict__
    where = [event.where for event in monitor.events() if isinstance(event, metapathology.MonitoringError)]
    assert "detailed_path_entry_finder" in where
    assert "uninstall_detailed_path_hooks" in where

elif _scenario == "import_results":
    import json
    import sys
    import threading
    from importlib.machinery import ModuleSpec
    import metapathology

    class BrokenLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            raise RuntimeError("broken")

    class BrokenFinder:
        def find_spec(self, fullname, path=None, target=None):
            return ModuleSpec(fullname, BrokenLoader()) if fullname == "detailed_outcome_broken" else None

    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    sys.meta_path.insert(0, BrokenFinder())
    import detailed_outcome_loaded

    del sys.modules["detailed_outcome_loaded"]
    try:
        import detailed_outcome_missing
    except ModuleNotFoundError:
        pass
    try:
        import detailed_outcome_broken
    except RuntimeError:
        pass
    import detailed_outcome_cached

    __import__("detailed_outcome_cached")
    thread = threading.Thread(target=lambda: __import__("detailed_outcome_threaded"))
    thread.start()
    thread.join()

    document = metapathology.get_report()
    by_name = {}
    for attempt in document["import_searches"]:
        by_name.setdefault(attempt["fullname"], []).append(attempt)
    assert by_name["detailed_outcome_loaded"][0]["progress"] == "loaded"
    assert by_name["detailed_outcome_loaded"][0]["presence"] == "absent_at_report"
    assert by_name["detailed_outcome_nested"][0]["progress"] == "loaded"
    assert by_name["detailed_outcome_missing"][0]["progress"] == "failed"
    assert by_name["detailed_outcome_broken"][0]["progress"] == "failed"
    assert by_name["detailed_outcome_threaded"][0]["progress"] == "loaded"
    assert len(by_name["detailed_outcome_cached"]) == 1
    assert not any(item["kind"] == "import_failed_after_state_change" for item in document["findings"])
    mechanism = next(item for item in document["capture"]["mechanisms"] if item["name"] == "import_results")
    assert mechanism["completeness"].endswith("cache_hits_not_observed")
    assert "import outcome observation:" in metapathology.render_report()
    assert "[import-failed-after-state-change]" not in metapathology.render_report()
    metapathology.uninstall()
    assert sys.getprofile() is None and threading.getprofile() is None

elif _scenario == "import_failed_after_state_change_requires_mutation_inside_failed_attempt":
    import json, sys, metapathology

    sys.path.insert(0, sys.argv[1])
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    try:
        import mutated_during_resolution
    except RuntimeError:
        pass
    document = metapathology.get_report()
    finding = next(item for item in document["findings"] if item["kind"] == "import_failed_after_state_change")
    assert finding["module"] == "mutated_during_resolution"
    assert len(finding["evidence"]["event_refs"]) == 3

elif _scenario == "exact_outcomes_refuse_an_existing_profiler":
    import sys, metapathology

    profile = lambda frame, event, arg: None
    sys.setprofile(profile)
    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    assert monitor.import_results_capture_status == "refused_existing_profiler"
    assert monitor.path_finder_capture_status == "unavailable_existing_profiler"
    assert sys.getprofile() is profile
    metapathology.uninstall()
    assert sys.getprofile() is profile
    sys.setprofile(None)

elif _scenario == "exact_outcomes_preserve_later_profiler_replacements":
    import sys, threading, metapathology

    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    sys_profile = lambda frame, event, arg: None
    thread_profile = lambda frame, event, arg: None
    sys.setprofile(sys_profile)
    threading.setprofile(thread_profile)
    metapathology.uninstall()
    assert sys.getprofile() is sys_profile
    assert threading.getprofile() is thread_profile
    sys.setprofile(None)
    threading.setprofile(None)

elif _scenario == "equivalent_outcomes":
    import importlib.machinery
    import json
    import sys

    import metapathology

    class Loader:
        def __init__(self, fullname):
            self.fullname = fullname

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            if self.fullname == "equivalent_broken":
                raise LookupError("same failure")
            if self.fullname == "equivalent_recursive":
                __import__("equivalent_nested")
            module.VALUE = self.fullname

    class Finder:
        def find_spec(self, fullname, target=None):
            if fullname not in {"equivalent_ok", "equivalent_broken", "equivalent_recursive", "equivalent_nested"}:
                return None
            return importlib.machinery.ModuleSpec(fullname, Loader(fullname))

    finder = Finder()
    sentinel = sys.path[0] + "\\equivalence"

    def hook(path):
        if path == sentinel:
            return finder
        raise ImportError

    sys.path_hooks.insert(0, hook)
    sys.path_importer_cache.pop(sentinel, None)
    sys.path.insert(0, sentinel)
    mode = sys.argv[1]
    if mode == "default":
        metapathology.install(report_at_exit=False)
    elif mode == "detailed":
        metapathology.install(
            report_at_exit=False,
            capture=metapathology.CaptureConfig(
                detailed=metapathology.DetailedCaptureConfig(
                    path_hooks=True,
                    path_entry_finders=True,
                    loaders=True,
                )
            ),
        )
        sys.path_importer_cache.pop(sentinel, None)
    outcomes = []
    for name in ("equivalent_ok", "equivalent_missing", "equivalent_broken", "equivalent_recursive"):
        try:
            first = __import__(name)
            second = __import__(name)
            outcomes.append((name, "returned", first.VALUE, first is second))
        except BaseException as exc:
            outcomes.append((name, "raised", type(exc).__name__))
    print(json.dumps(outcomes))
elif _scenario == "detailed_outcomes_capture_path_finder_aggregate":
    import sys

    import metapathology

    sys.path.insert(0, sys.argv[1])
    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    import standard_aggregate_target

    events = [
        event
        for event in monitor.events()
        if isinstance(event, metapathology.PathFinderCall) and event.fullname == "standard_aggregate_target"
    ]
    assert len(events) == 1, events
    event = events[0]
    assert event.finder_type_name == "PathFinder"
    assert event.spec_summary.loader.type_name == "SourceFileLoader"
    assert event.search_id > 0
    metapathology.uninstall()
else:
    raise ValueError(f"unknown scenario: {_scenario}")
