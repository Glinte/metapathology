"""Isolated child scenarios extracted from tests/reporting/test_schema_vocabulary.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "conformance_report":
    import importlib
    import json
    import sys
    import types

    import metapathology

    class CheckFinder:
        def find_spec(self, fullname, path=None, target=None):
            return None

    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(enabled=True)),
    )
    sys.path_importer_cache["conformance-cache-check"] = None
    sys.meta_path = list(sys.meta_path)
    sys.path_hooks = list(sys.path_hooks)

    def check_hook(path):
        return None

    sys.path_hooks.append(check_hook)
    sys.meta_path.insert(0, CheckFinder())
    importlib.import_module("colorsys")
    importlib.import_module("fractions")
    sys.modules["ghost_mod"] = types.ModuleType("ghost_mod")

    print(json.dumps(metapathology.get_report()))
else:
    raise ValueError(f"unknown scenario: {_scenario}")
