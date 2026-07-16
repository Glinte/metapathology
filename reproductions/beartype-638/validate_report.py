"""Pin the contention evidence expected from the beartype#638 fixture."""

import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
findings = document["findings"]
assert any(
    finding["kind"] == "meta_bypass"
    and finding["claim"]["finder_type_name"] == "AssertionRewritingHook"
    and finding["evidence"]["level"] == "captured"
    for finding in findings
), findings
assert any(
    finding["kind"] in {"bypass", "unfindable", "loader_displacement"}
    and finding.get("claim", {}).get("finder_type_name") == "AssertionRewritingHook"
    for finding in findings
), findings
