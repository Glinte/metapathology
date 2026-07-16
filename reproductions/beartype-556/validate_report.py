"""Pin the contention evidence expected from the beartype#556 fixture."""

import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
findings = document["findings"]
assert any(
    finding["module"].startswith("myproject")
    and finding["claim"]["finder_type_name"] == "ScikitBuildRedirectingFinder"
    and "meta_path_short_circuit" in finding["signals"]
    for finding in findings
), findings
assert any(
    finding["kind"] in {"loader_displacement", "origin_displacement", "unfindable"}
    and finding["module"].startswith("myproject")
    and finding["evidence"]["level"] == "live_replay"
    and finding["severity"] == "informational"
    and "expected_editable_redirection" in finding["signals"]
    for finding in findings
), findings
