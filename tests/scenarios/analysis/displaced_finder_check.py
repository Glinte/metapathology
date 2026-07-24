"""Isolated child scenarios extracted from tests/analysis/test_displaced_finder_check.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "off_by_default":
    import json

    import metapathology

    class Finder:
        def find_spec(self, fullname, target=None):
            raise AssertionError("disabled checks must not call foreign finders")

    metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(
            importer_cache=True,
            detailed=metapathology.DetailedCaptureConfig(path_entry_finders=True),
        ),
    )
    document = metapathology.get_report()
    run = next(check for check in document["checks"] if check["kind"] == "displaced_finder")
    assert run["status"] == "disabled"
    assert run["foreign_calls"] == 0
    assert not [result for result in document["finder_results"] if result["kind"] == "displaced_finder_check"]
else:
    raise ValueError(f"unknown scenario: {_scenario}")
