"""Isolated child scenarios extracted from tests/reporting/test_report_text.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "bypass":
    import json
    import sys
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location

    import metapathology

    class SneakyLoader(SourceFileLoader):
        pass

    class SneakyFinder:
        def __init__(self, name, path):
            self.name = name
            self.path = path

        def find_spec(self, fullname, path=None, target=None):
            if fullname == self.name:
                return spec_from_file_location(fullname, self.path, loader=SneakyLoader(fullname, self.path))
            return None

    target_name, target_file = sys.argv[1], sys.argv[2]
    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, SneakyFinder(target_name, target_file))

    module = __import__(target_name)
    assert module.VALUE == 7

    text = metapathology.render_report()
    document = metapathology.get_report()
    results = [item for item in document["finder_results"] if item["module"] == target_name]
    captured = next(item for item in results if item["kind"] == "observed_finder_result")
    check = next(item for item in results if item["kind"] == "standard_path_check")
    assert check["evidence_level"] == "current_state_check", check
    assert check["state_phase"] == "report", check
    assert check["predicts_alternative_winner"] is False, check
    assert "meta_path_short_circuit" in captured["signals"], captured
    assert not any(item["module"] == target_name for item in document["findings"])
    print(text)

elif _scenario == "source_and_archive_results_are_compared_without_classifying_a_defect":
    import json, sys
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location
    import metapathology

    class Finder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "archive_conflict":
                return spec_from_file_location(fullname, sys.argv[2], loader=SourceFileLoader(fullname, sys.argv[2]))
            return None

    sys.path.insert(0, sys.argv[1])
    metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, Finder())
    import archive_conflict

    document = metapathology.get_report()
    results = [item for item in document["finder_results"] if item["module"] == "archive_conflict"]
    check = next(item for item in results if item["kind"] == "standard_path_check")
    assert check["spec"]["loader"]["type_name"] == "zipimporter"
    assert not any(item["module"] == "archive_conflict" for item in document["findings"])
    assert "[frozen-source-conflict]" not in metapathology.render_report()

elif _scenario == "bypass_2":
    import json
    import sys
    from importlib.machinery import SourceFileLoader
    from importlib.util import spec_from_file_location

    import metapathology

    class SneakyLoader(SourceFileLoader):
        pass

    class SneakyFinder:
        def __init__(self, name, path):
            self.name = name
            self.path = path

        def find_spec(self, fullname, path=None, target=None):
            if fullname == self.name:
                return spec_from_file_location(fullname, self.path, loader=SneakyLoader(fullname, self.path))
            return None

    target_name, target_file = sys.argv[1], sys.argv[2]
    monitor = metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, SneakyFinder(target_name, target_file))

    module = __import__(target_name)
    assert module.VALUE == 7

    text = metapathology.render_report()
    document = metapathology.get_report()
    results = [item for item in document["finder_results"] if item["module"] == target_name]
    captured = next(item for item in results if item["kind"] == "observed_finder_result")
    check = next(item for item in results if item["kind"] == "standard_path_check")
    assert check["evidence_level"] == "current_state_check", check
    assert check["state_phase"] == "report", check
    assert check["predicts_alternative_winner"] is False, check
    assert "meta_path_short_circuit" in captured["signals"], captured
    assert not any(item["module"] == target_name for item in document["findings"])
    print(text)

elif _scenario == "no_spec":
    import sys
    import types

    import metapathology

    metapathology.install(report_at_exit=False)
    sys.modules["ghost_mod"] = types.ModuleType("ghost_mod")

    text = metapathology.render_report()
    assert "note (1):" in text, text
    assert "[module-without-spec]" in text, text
    assert "ghost_mod" in text, text

elif _scenario == "default_finders":
    import metapathology

    metapathology.install(report_at_exit=False)
    print(metapathology.render_report())

elif _scenario == "path_removed_after_import":
    import sys
    from importlib.machinery import PathFinder

    import metapathology

    class DelegatingFinder:
        def find_spec(self, fullname, path=None, target=None):
            return PathFinder.find_spec(fullname, path, target)

    module_dir = sys.argv[1]
    sys.path.insert(0, module_dir)
    metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, DelegatingFinder())

    import transient_path_mod

    assert transient_path_mod.VALUE == 7
    sys.path.remove(module_dir)

    text = metapathology.render_report()
    assert "[unfindable] 'transient_path_mod'" not in text, text
    assert "[loader-displacement] 'transient_path_mod'" not in text, text

elif _scenario == "cwd_changed_after_import":
    import os
    import sys
    from importlib.machinery import PathFinder

    import metapathology

    class DelegatingFinder:
        def find_spec(self, fullname, path=None, target=None):
            return PathFinder.find_spec(fullname, path, target)

    new_cwd = sys.argv[1]
    metapathology.install(report_at_exit=False)
    sys.meta_path.insert(0, DelegatingFinder())

    import cwd_sensitive_mod

    assert cwd_sensitive_mod.VALUE == 7
    os.chdir(new_cwd)

    text = metapathology.render_report()
    assert "[unfindable] 'cwd_sensitive_mod'" not in text, text
    assert "[loader-displacement] 'cwd_sensitive_mod'" not in text, text

elif _scenario == "hostile_module_spec":
    import sys

    import metapathology

    class HostileModule:
        touched = False

        def __getattribute__(self, name):
            type(self).touched = True
            raise SystemExit(94)
            return super().__getattribute__(name)

    metapathology.install(report_at_exit=False)
    hostile = HostileModule()
    sys.modules["hostile_module"] = hostile
    text = metapathology.render_report()
    assert "metadata unavailable" in text, text
    del sys.modules["hostile_module"]
    assert not HostileModule.touched
else:
    raise ValueError(f"unknown scenario: {_scenario}")
