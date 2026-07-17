"""T15 deterministic joins from atomic findings to import effects."""

import subprocess
from collections.abc import Callable
from pathlib import Path

from metapathology._report_data import CausalExplanation, _append_ambiguous_explanations
from metapathology._report_text import _primary_explanations

RunPython = Callable[..., subprocess.CompletedProcess[str]]


def _explanation(explanation_id: str, kind: str, effect_status: str) -> CausalExplanation:
    return CausalExplanation(
        explanation_id=explanation_id,
        kind=kind,
        confidence="captured",
        subject="contended.module",
        effect_status=effect_status,
        cause_finding_id=None,
        finder_type_name="Finder",
        omitted_location="",
        candidate_path="",
        event_seqs=(7,),
        next_observation=None,
    )


def test_equal_conflicting_explanations_remain_explicit_alternatives() -> None:
    explanations = _append_ambiguous_explanations(
        (
            _explanation("explanation:1", "first_mechanism", "first_effect"),
            _explanation("explanation:2", "second_mechanism", "second_effect"),
        )
    )

    ambiguity = explanations[-1]
    assert ambiguity.kind == "ambiguous_contention"
    assert ambiguity.confidence == "unknown"
    assert ambiguity.alternatives == ("explanation:1", "explanation:2")
    assert _primary_explanations(explanations) == (ambiguity,)


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
comparison = next(
    item for item in document["route_comparisons"] if item["only_in_right_route"]
)
if sys.argv[3] == "deep":
    finding = next(item for item in document["findings"] if item["kind"] == "namespace_truncation")
    assert finding["severity"] == "actionable"
    assert "meta_path_short_circuit" in finding["signals"]
    assert finding["route_comparison_ref"] == comparison["id"]
    explanation = document["explanations"][0]
    assert explanation["kind"] == "namespace_truncation_failure"
    assert explanation["subject"] == "synthesis_ns.child"
    assert explanation["cause_finding_ref"] == finding["id"]
    assert explanation["omitted_location"] == os.path.join(sys.argv[1], "synthesis_ns")
    assert explanation["candidate_path"] == os.path.join(explanation["omitted_location"], "child")
    assert explanation["confidence"] == "correlated"
    assert explanation["effect_status"] == "failed"
    assert explanation["next_observation"] is None
    text = metapathology.render_report()
    assert "[1] [correlated] 'synthesis_ns.child' failed" in text
    assert "    [namespace-truncation] 'synthesis_ns':" in text
    assert "why it matters:" in text
    assert "this is an actionable finding based on correlated evidence" in text
    assert "cause: finding:" not in text
else:
    assert not any(item["kind"] == "namespace_truncation" for item in document["findings"])
    assert not any(item["kind"] == "namespace_truncation_failure" for item in document["explanations"])
    text = metapathology.render_report()
    assert "resolution route divergences" in text
    assert "[namespace-truncation]" not in text
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
