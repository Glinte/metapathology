"""Isolated child scenarios extracted from tests/runtime/test_package.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "package_import_defers_monitor_and_distribution_metadata":
    import sys

    assert "importlib.metadata" not in sys.modules
    import metapathology

    deferred = ("importlib.metadata", "metapathology._monitor", "metapathology._records", "typing")
    print([name for name in deferred if name in sys.modules])

elif _scenario == "record_api_does_not_load_dataclasses_or_typing":
    import sys
    from metapathology import MonitoringError

    deferred = ("dataclasses", "traceback", "typing")
    print([name for name in deferred if name in sys.modules])
else:
    raise ValueError(f"unknown scenario: {_scenario}")
