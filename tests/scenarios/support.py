"""Self-tests for isolated scenario dispatch."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "arguments":
    import json

    print(json.dumps({"argv": sys.argv, "filename": __file__}))
else:
    raise ValueError(f"unknown scenario: {_scenario}")
