"""Isolated child scenarios extracted from tests/monitoring/test_detailed_path_machinery.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "detailed_capture":
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
            if self.fullname == "detailed_broken":
                raise RuntimeError("target failure")
            module.VALUE = self.fullname

    class Finder:
        def __init__(self):
            self.loaders = []

        def find_spec(self, fullname, target=None):
            if fullname == "detailed_outer":
                __import__("detailed_nested")
            if fullname not in {"detailed_outer", "detailed_nested", "detailed_broken"}:
                return None
            loader = Loader(fullname)
            self.loaders.append(loader)
            return importlib.machinery.ModuleSpec(fullname, loader)

    finder = Finder()
    sentinel = sys.path[0] + "\\detailed-sentinel"

    def hook(path):
        if path != sentinel:
            raise ImportError
        return finder

    sys.path_hooks.insert(0, hook)
    sys.path_importer_cache.pop(sentinel, None)
    sys.path.insert(0, sentinel)
    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            detailed=metapathology.DetailedCaptureConfig(
                path_hooks=True,
                path_entry_finders=True,
                loaders=True,
            )
        ),
    )
    # The wrapper shadows the hook by identity but compares equal to it, so
    # ``in`` (which uses ``==``) still finds it while ``is`` sees the wrapper.
    assert hook in sys.path_hooks
    assert not any(candidate is hook for candidate in sys.path_hooks)
    assert any(getattr(candidate, "__wrapped__", None) is hook for candidate in sys.path_hooks)

    def later_hook(path):
        raise ImportError

    sys.path_hooks.append(later_hook)
    assert later_hook in sys.path_hooks
    assert not any(candidate is later_hook for candidate in sys.path_hooks)
    assert any(getattr(candidate, "__wrapped__", None) is later_hook for candidate in sys.path_hooks)
    sys.path_importer_cache.pop(sentinel, None)
    __import__("detailed_outer")
    try:
        __import__("detailed_broken")
    except RuntimeError:
        pass
    else:
        raise AssertionError("loader exception changed")
    assert isinstance(sys.path_importer_cache[sentinel], Finder)
    document = metapathology.get_report()
    detailed = [event["data"] for event in document["timeline"] if event["kind"] == "import_mechanism_call"]
    assert {event["boundary"] for event in detailed} == {
        "path_hook",
        "path_entry_finder",
        "loader_create_module",
        "loader_exec_module",
    }, detailed
    assert any(event["outcome"] == "unobserved_reentrant" for event in detailed), detailed
    # A successful path hook captures the identity of the finder it returned, so the
    # report can link an accepting hook to the finder it installed in the cache.
    assert any(
        event["boundary"] == "path_hook"
        and event["outcome"] == "returned"
        and event["returned_finder"] is not None
        and event["returned_finder"]["type_name"] == "Finder"
        for event in detailed
    ), detailed
    assert any(event["fullname"] == "detailed_broken" and event["outcome"] == "raised" for event in detailed), detailed
    assert document["capture"]["mechanisms"][4]["enabled"] is True
    text = metapathology.render_report()
    assert "WARNING: opt-in detailed capture" in text
    metapathology.uninstall()
    assert hook in sys.path_hooks
    assert later_hook in sys.path_hooks
    assert "find_spec" not in finder.__dict__
    assert all(
        "create_module" not in loader.__dict__ and "exec_module" not in loader.__dict__ for loader in finder.loaders
    )
    print(json.dumps({"detailed": detailed, "mechanisms": monitor.detailed_capture}))

elif _scenario == "distinct_hooks_accepting_one_path_report_structural_shadow":
    import json, sys, metapathology

    class Finder:
        def find_spec(self, fullname, target=None):
            return None

    def first(path):
        if path == sys.argv[1]:
            return Finder()
        raise ImportError

    def second(path):
        if path == sys.argv[1]:
            return Finder()
        raise ImportError

    sys.path_hooks[:0] = [first, second]
    sys.path.insert(0, sys.argv[1])
    sys.path_importer_cache.pop(sys.argv[1], None)
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(path_hooks=True)),
    )
    sys.path_importer_cache.pop(sys.argv[1], None)
    try:
        __import__("shadow_first_missing")
    except ModuleNotFoundError:
        pass
    list.__setitem__(sys.path_hooks, slice(0, 2), [sys.path_hooks[1], sys.path_hooks[0]])
    sys.path_importer_cache.pop(sys.argv[1], None)
    try:
        __import__("shadow_second_missing")
    except ModuleNotFoundError:
        pass
    document = metapathology.get_report()
    finding = next(item for item in document["findings"] if item["kind"] == "competing_path_hooks")
    assert finding["subject"] == {"kind": "path", "value": sys.argv[1]}
    assert finding["evidence"]["level"] == "inferred_from_state"
    assert len(finding["evidence"]["event_refs"]) == 2
    text = metapathology.render_report()
    assert "[competing-path-hooks]" in text
    assert "further investigation:" in text
    assert "--unsafe-explore-import-branches" in text

elif _scenario == "detailed_path_hook_wrapper_survives_equality_scan":
    import sys, metapathology

    created = []
    sentinel = sys.argv[1]

    class FallbackFinder:
        def find_spec(self, fullname, target=None):
            return None

    def anchor_hook(path):
        raise ImportError

    def tail_hook(path):
        if path != sentinel:
            raise ImportError
        created.append(path)
        return FallbackFinder()

    sys.path_hooks[:0] = [anchor_hook, tail_hook]
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(path_hooks=True)),
    )
    anchor_seen = False
    fallback = None
    for hook in sys.path_hooks:
        if hook == anchor_hook:
            anchor_seen = True
            continue
        if not anchor_seen:
            continue
        try:
            fallback = hook(sentinel)
            break
        except ImportError:
            pass
    assert anchor_seen, "anchor hook not found via == scan"
    assert isinstance(fallback, FallbackFinder), fallback
    assert created == [sentinel], created

elif _scenario == "discord_10017_module_replacement":
    import importlib.machinery
    import json
    import sys
    import types

    import metapathology

    class ReplacingLoader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.LOADED = True

    class Finder:
        def __init__(self):
            self.loader = ReplacingLoader()

        def find_spec(self, fullname, path=None, target=None):
            if fullname != "detailed_identity_ext":
                return None
            return importlib.machinery.ModuleSpec(fullname, self.loader, origin="shared.ext")

    finder = Finder()
    sys.meta_path.insert(0, finder)
    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(loaders=True)),
    )
    import detailed_identity_ext

    first = detailed_identity_ext
    spec = first.__spec__
    second = types.ModuleType("detailed_identity_ext")
    second.__spec__ = spec
    second.__loader__ = spec.loader
    sys.modules["detailed_identity_ext"] = second
    spec.loader.exec_module(second)
    sys.modules["detailed_identity_ext"] = first

    events = [
        event
        for event in monitor.events()
        if isinstance(event, metapathology.ImportMechanismCall)
        and event.boundary == "loader_exec_module"
        and event.fullname == "detailed_identity_ext"
    ]
    assert len(events) == 2, events
    assert events[0].module_state_before.object_id == id(first)
    assert events[0].module_state_after.object_id == id(first)
    assert events[0].target_state.object_id == id(first)
    assert events[1].module_state_before.object_id == id(second)
    assert events[1].module_state_after.object_id == id(second)
    assert events[1].target_state.object_id == id(second)
    assert first.__spec__.origin == second.__spec__.origin == "shared.ext"
    document = metapathology.get_report()
    finding = next(item for item in document["findings"] if item["kind"] == "module_executed_again")
    assert finding["module"] == "detailed_identity_ext"
    assert finding["evidence"]["level"] == "observed"
    assert set(finding["evidence"]["event_refs"]) == {
        "event:" + str(events[0].sequence),
        "event:" + str(events[1].sequence),
    }
    assert finding["data"]["import_mechanism_call"]["event_ref"] == "event:" + str(events[1].sequence)
    assert finding["data"]["module_state_baseline"]["object_id"] == hex(id(first))
    assert finding["data"]["import_mechanism_call"]["module_state_before"]["object_id"] == hex(id(second))
    assert finding["data"]["import_mechanism_call"]["module_state_after"]["object_id"] == hex(id(second))
    assert finding["data"]["import_mechanism_call"]["target_state"]["object_id"] == hex(id(second))
    explanation = next(item for item in document["explanations"] if item["kind"] == "module_executed_again")
    assert explanation["confidence"] == "observed"
    assert explanation["cause_finding_ref"] == finding["id"]
    assert explanation["state_before"]["object_id"] == hex(id(first))
    assert explanation["state_after"]["object_id"] == hex(id(second))
    assert explanation["effect_status"] == "separate_module_executed"
    text = metapathology.render_report()
    assert "[module-executed-again] 'detailed_identity_ext'" in text, text
    assert "internal steps and temporary objects are unknown" in text, text
    assert "[observed] ReplacingLoader executed 'detailed_identity_ext' again with a different module object" in text, (
        text
    )
    metapathology.uninstall()

elif _scenario == "detailed_mechanisms_are_independently_toggleable":
    import metapathology

    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            path_hooks=False,
            importer_cache=False,
            detailed=metapathology.DetailedCaptureConfig(path_entry_finders=True),
        ),
    )
    assert monitor.detailed_capture == ("path_entry_finders",)
    assert type(__import__("sys").path_hooks) is list
    metapathology.uninstall()

elif _scenario == "detailed_path_entry_finder_preserves_later_shadow":
    import sys, metapathology

    class Finder:
        def find_spec(self, fullname, target=None):
            return None

    finder = Finder()
    sys.path_importer_cache["owned-finder-check"] = finder
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(path_entry_finders=True)),
    )
    replacement = lambda fullname, target=None: None
    finder.find_spec = replacement
    metapathology.uninstall()
    assert finder.__dict__["find_spec"] is replacement

elif _scenario == "detailed_loader_preserves_later_method_shadows":
    import importlib.util, sys, metapathology

    class Loader:
        def create_module(self, spec):
            return None

        def exec_module(self, module):
            module.VALUE = 1

    class Finder:
        def __init__(self, loader):
            self.loader = loader

        def find_spec(self, fullname, path=None, target=None):
            if fullname == "owned_loader_check":
                return importlib.util.spec_from_loader(fullname, self.loader)
            return None

    loader = Loader()
    finder = Finder(loader)
    sys.meta_path.insert(0, finder)
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(loaders=True)),
    )
    import owned_loader_check

    replacement_create = lambda spec: None
    replacement_exec = lambda module: None
    loader.create_module = replacement_create
    loader.exec_module = replacement_exec
    metapathology.uninstall()
    assert loader.__dict__["create_module"] is replacement_create
    assert loader.__dict__["exec_module"] is replacement_exec
    sys.meta_path.remove(finder)

elif _scenario == "detailed_path_hooks_restore_only_owned_wrappers":
    import sys, metapathology

    original_hooks = list(sys.path_hooks)
    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            path_hooks=True,
            detailed=metapathology.DetailedCaptureConfig(path_hooks=True),
        ),
    )
    replacement = lambda path: (_ for _ in ()).throw(ImportError)
    sys.path_hooks[0] = replacement
    metapathology.uninstall()
    assert sys.path_hooks[0] is replacement
    assert all(current is original for current, original in zip(sys.path_hooks[1:], original_hooks[1:]))
    sys.path_hooks = original_hooks
else:
    raise ValueError(f"unknown scenario: {_scenario}")
