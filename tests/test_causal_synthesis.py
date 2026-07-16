"""T15 deterministic joins from atomic findings to import effects."""

import subprocess
from collections.abc import Callable
from pathlib import Path

RunPython = Callable[..., subprocess.CompletedProcess[str]]


NAMESPACE_FAILURE = r"""
import importlib.machinery
import json
import os
import sys

import metapathology

class TruncatingFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "synthesis_ns":
            return None
        spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
        spec.submodule_search_locations = [sys.argv[2]]
        return spec

sys.path.insert(0, sys.argv[1])
metapathology.install(report_at_exit=False, deep_import_outcomes=sys.argv[3] == "deep")
sys.meta_path.insert(0, TruncatingFinder())
try:
    import synthesis_ns.child
except ModuleNotFoundError:
    pass
else:
    raise AssertionError("truncated namespace unexpectedly found child")

document = json.loads(metapathology.render_report(format="json"))
finding = next(item for item in document["findings"] if item["kind"] == "namespace_truncation")
assert finding["severity"] == "actionable"
assert "meta_path_short_circuit" in finding["signals"]
explanation = document["explanations"][0]
assert explanation["kind"] == "namespace_truncation_failure"
assert explanation["subject"] == "synthesis_ns.child"
assert explanation["cause_finding_ref"] == finding["id"]
assert explanation["omitted_location"] == os.path.join(sys.argv[1], "synthesis_ns")
assert explanation["candidate_path"] == os.path.join(explanation["omitted_location"], "child")
if sys.argv[3] == "deep":
    assert explanation["confidence"] == "correlated"
    assert explanation["effect_status"] == "failed"
    assert explanation["next_observation"] is None
    assert "[correlated] 'synthesis_ns.child' failed" in metapathology.render_report()
else:
    assert explanation["confidence"] == "counterfactual"
    assert explanation["effect_status"] == "outcome_unknown"
    assert explanation["next_observation"] == "enable_deep_import_outcomes"
    assert "next observation: enable deep import outcomes" in metapathology.render_report()
print("OK")
"""


def test_namespace_truncation_explains_descendant_failure_with_honest_confidence(
    run_python: RunPython, tmp_path: Path
) -> None:
    omitted_root = tmp_path / "installed"
    child = omitted_root / "synthesis_ns" / "child"
    child.mkdir(parents=True)
    (child / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    claimed = tmp_path / "editable" / "synthesis_ns"
    claimed.mkdir(parents=True)

    for mode in ("default", "deep"):
        proc = run_python(NAMESPACE_FAILURE, str(omitted_root), str(claimed), mode)
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "OK"
