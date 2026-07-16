"""Pin the contention evidence expected from the beartype#556 fixture."""

import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
findings = document["findings"]
assert any(
    finding["kind"] == "meta_bypass"
    and finding["module"].startswith("myproject")
    and finding["claim"]["finder_type_name"] == "ScikitBuildRedirectingFinder"
    and finding["evidence"]["level"] == "captured"
    for finding in findings
), findings
assert any(
    finding["kind"] in {"bypass", "unfindable"}
    and finding["module"].startswith("myproject")
    and finding["evidence"]["level"] == "live_replay"
    for finding in findings
), findings
