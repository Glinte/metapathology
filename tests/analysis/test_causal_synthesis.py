"""Causal synthesis: deterministic joins from atomic findings to import effects."""

from pathlib import Path

from support import PythonRunner

from metapathology._report_analysis import _append_ambiguous_explanations, _IdAllocator
from metapathology._report_model import CausalExplanation
from metapathology._report_text import _primary_explanations

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._report_model import EffectStatus, ExplanationKind


def _explanation(explanation_id: str, kind: "ExplanationKind", effect_status: "EffectStatus") -> CausalExplanation:
    return CausalExplanation(
        explanation_id=explanation_id,
        kind=kind,
        confidence="observed",
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
            _explanation("explanation:1", "finder", "regular_module_selected"),
            _explanation("explanation:2", "path", "descendant_failed"),
        ),
        _IdAllocator("explanation"),
    )

    ambiguity = explanations[-1]
    assert ambiguity.kind == "ambiguous_contention"
    assert ambiguity.confidence == "unknown"
    assert ambiguity.alternatives == ("explanation:1", "explanation:2")
    assert _primary_explanations(explanations) == (ambiguity,)


def test_missing_namespace_locations_explains_descendant_failure_with_honest_confidence(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    omitted_root = tmp_path / "installed"
    child = omitted_root / "synthesis_ns" / "child"
    child.mkdir(parents=True)
    (child / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    finder_called = tmp_path / "editable" / "synthesis_ns"
    finder_called.mkdir(parents=True)

    for mode in ("default", "detailed"):
        python_runner.run_scenario_ok(
            "analysis/causal_synthesis.py", "namespace_failure", str(omitted_root), str(finder_called), mode
        )
