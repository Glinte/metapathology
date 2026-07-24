"""Isolated child scenarios extracted from tests/reporting/test_timeline_rendering.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "collapse_run":
    import sys

    import metapathology
    from metapathology import MetaPathFinderCall, ImportSearchStarted

    class DecliningFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, DecliningFinder())

    for name in ("collapse_missing_one", "collapse_missing_two", "collapse_missing_three"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError(f"{name} unexpectedly resolved")

    events = monitor.events()
    collapsible_lines = []
    for event in events:
        if isinstance(event, ImportSearchStarted):
            collapsible_lines.append(f"#{event.sequence} import started")
        elif isinstance(event, MetaPathFinderCall) and not event.found and event.exception_type_name is None:
            collapsible_lines.append(f"#{event.sequence} DecliningFinder checked")

    text = metapathology.render_report()
    timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path changes", 1)[0]
    assert "(collapsed)" in timeline, timeline
    for line in collapsible_lines:
        assert line not in timeline, (line, timeline)

elif _scenario == "collapse_detailed_run":
    import importlib.machinery
    import sys

    import metapathology

    class Loader:
        def __init__(self, fullname):
            self.fullname = fullname

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            if self.fullname == "detailed_collapse_outer":
                for name in (
                    "detailed_collapse_m1",
                    "detailed_collapse_m2",
                    "detailed_collapse_m3",
                    "detailed_collapse_m4",
                ):
                    __import__(name)

    class Finder:
        def find_spec(self, fullname, target=None):
            if not fullname.startswith("detailed_collapse"):
                return None
            return importlib.machinery.ModuleSpec(fullname, Loader(fullname))

    finder = Finder()
    sentinel = sys.path[0] + "\\detailed-collapse-sentinel"

    def hook(path):
        if path != sentinel:
            raise ImportError
        return finder

    sys.path_hooks.insert(0, hook)
    sys.path_importer_cache.pop(sentinel, None)
    sys.path.insert(0, sentinel)
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
    __import__("detailed_collapse_outer")
    text = metapathology.render_report()
    timeline = text.split("-- event timeline", 1)[1]
    assert "unobserved_reentrant" not in timeline, timeline
    assert "not_found" not in timeline, timeline
    assert "nested; result not recorded" in text, text
    assert "nested calls (collapsed)" in timeline, timeline

elif _scenario == "claim_splits_run":
    import sys
    from importlib.machinery import PathFinder

    import metapathology
    from metapathology import MetaPathFinderCall

    class DecliningFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "finder_call_split_target":
                return PathFinder.find_spec(fullname, path, target)
            return None

    metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, DecliningFinder())

    for name in ("finder_call_split_missing_one", "finder_call_split_missing_two"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass

    import finder_call_split_target

    for name in ("finder_call_split_missing_three", "finder_call_split_missing_four"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass

    monitor = metapathology.get_monitor()
    events = monitor.events()
    finder_call = next(
        event
        for event in events
        if isinstance(event, MetaPathFinderCall) and event.fullname == "finder_call_split_target" and event.found
    )

    text = metapathology.render_report()
    timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path changes", 1)[0]
    assert f"#{finder_call.sequence} DecliningFinder.find_spec('finder_call_split_target'): found it" in timeline, (
        timeline
    )

elif _scenario == "finding_referenced_event":
    import importlib.machinery
    import json
    import sys

    import metapathology

    class TruncatingFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname != "ref_ns":
                return None
            spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
            spec.submodule_search_locations = [sys.argv[2]]
            return spec

    class DecliningFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    sys.path.insert(0, sys.argv[1])
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(import_results=True)),
    )
    sys.meta_path.insert(0, TruncatingFinder())
    sys.meta_path.insert(0, DecliningFinder())

    for name in ("ref_pad_one", "ref_pad_two"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass

    try:
        import ref_ns.child
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("truncated namespace unexpectedly found child")

    for name in ("ref_pad_three", "ref_pad_four"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass

    document = metapathology.get_report()
    explanation = next(
        item for item in document["explanations"] if item["kind"] == "missing_namespace_locations_failure"
    )
    referenced_seqs = [int(ref.split(":", 1)[1]) for ref in explanation["event_refs"]]
    assert referenced_seqs, explanation

    text = metapathology.render_report()
    assert "further investigation:" in text
    assert "--unsafe-explore-import-branches" in text
    timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path changes", 1)[0]
    for sequence in referenced_seqs:
        assert f"#{sequence} " in timeline, (sequence, timeline)

elif _scenario == "timeline_full_escape_hatch":
    import os
    import sys

    import metapathology
    from metapathology import MetaPathFinderCall, ImportSearchStarted

    os.environ["METAPATHOLOGY_TEXT_TIMELINE"] = "full"

    class DecliningFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, DecliningFinder())

    for name in ("full_missing_one", "full_missing_two", "full_missing_three"):
        try:
            __import__(name)
        except ModuleNotFoundError:
            pass

    events = monitor.events()
    text = metapathology.render_report()
    timeline = text.split("-- event timeline", 1)[1].split("-- sys.meta_path changes", 1)[0]
    assert "(collapsed)" not in timeline, timeline
    for event in events:
        assert f"#{event.sequence} " in timeline, (event, timeline)
else:
    raise ValueError(f"unknown scenario: {_scenario}")
