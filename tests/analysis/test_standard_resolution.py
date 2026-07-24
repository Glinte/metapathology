"""Standard finder attribution coverage."""

import subprocess
import sys
from importlib.abc import Loader
from importlib.machinery import (
    BuiltinImporter,
    ExtensionFileLoader,
    FrozenImporter,
    ModuleSpec,
    SourceFileLoader,
    SourcelessFileLoader,
)
from pathlib import Path
from typing import cast

from support import PythonRunner

from metapathology._report_analysis import (
    _IdAllocator,
    _module_failed_after_loading_findings,
    _standard_spec_classification,
)
from metapathology._report_model import CorrelationEvidence, ImportSearch, StandardResolution
from metapathology._spec import summarize_spec


def test_module_failed_after_loading_attaches_failed_attempt_without_own_resolution() -> None:
    searches = (
        ImportSearch(1, "native", 1, (1, 3), 1, "main", "loaded", "absent_at_report"),
        ImportSearch(2, "native", 10, (10, 15), 1, "main", "failed", "absent_at_report"),
        ImportSearch(3, "native", 11, (11, 12, 13), 1, "main", "failed", "absent_at_report"),
    )
    resolutions = (
        StandardResolution(
            search_id=1,
            fullname="native",
            finder_type_name="PathFinder",
            category="extension",
            loader_type_name="ExtensionFileLoader",
            origin="native.pyd",
            evidence_level="observed",
            state_phase="import",
            event_seq=2,
            component_event_seqs=(),
            later_finders=(),
        ),
        StandardResolution(
            search_id=3,
            fullname="native",
            finder_type_name="PathFinder",
            category="extension",
            loader_type_name="ExtensionFileLoader",
            origin="native.pyd",
            evidence_level="observed",
            state_phase="import",
            event_seq=12,
            component_event_seqs=(),
            later_finders=(),
        ),
    )

    finding = _module_failed_after_loading_findings(searches, resolutions, _IdAllocator("finding"))[0]

    assert {10, 15} <= set(finding.supporting_event_seqs)
    assert isinstance(finding.evidence, CorrelationEvidence)
    assert finding.evidence.search_ids == (1, 2, 3)


def test_standard_loader_categories_are_explicit() -> None:
    zip_loader_type = type("zipimporter", (), {})
    specs = (
        (ModuleSpec("built_in", cast("Loader", BuiltinImporter), origin="built-in"), "built_in"),
        (ModuleSpec("frozen", cast("Loader", FrozenImporter), origin="frozen"), "frozen"),
        (ModuleSpec("source", SourceFileLoader("source", "source.py")), "source"),
        (ModuleSpec("bytecode", SourcelessFileLoader("bytecode", "bytecode.pyc")), "bytecode"),
        (ModuleSpec("extension", ExtensionFileLoader("extension", "extension.pyd")), "extension"),
        (ModuleSpec("zipped", cast("Loader", zip_loader_type())), "zip"),
        (ModuleSpec("namespace", None, is_package=True), "namespace"),
    )
    specs[-1][0].submodule_search_locations = []
    for spec, expected in specs:
        summary, _loader = summarize_spec(spec, iterate_foreign_locations=False)
        classified = _standard_spec_classification(summary)
        assert classified is not None
        assert classified[1] == expected


def test_default_report_infers_namespace_before_later_finder(python_runner: PythonRunner, tmp_path: Path) -> None:
    (tmp_path / "standard_namespace").mkdir()
    python_runner.run_scenario_ok(
        "analysis/standard_resolution.py", "default_report_infers_namespace_before_later_finder"
    )


def test_ordinary_standard_modules_do_not_finder_call_later_finders_are_relevant(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    (tmp_path / "ordinary_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    python_runner.run_scenario_ok(
        "analysis/standard_resolution.py",
        "ordinary_standard_modules_do_not_finder_call_later_finders_are_relevant",
        str(tmp_path),
    )


def test_detailed_report_captures_source_resolution(python_runner: PythonRunner, tmp_path: Path) -> None:
    module_dir = tmp_path / "detailed_standard"
    module_dir.mkdir()
    (module_dir / "standard_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    python_runner.run_scenario_ok(
        "analysis/standard_resolution.py",
        "detailed_report_captures_source_resolution",
        str(module_dir),
    )


def test_detailed_report_explains_namespace_candidate_hidden_by_later_regular_module(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    namespace_root = tmp_path / "namespace"
    regular_root = tmp_path / "regular"
    (namespace_root / "candidate_pkg").mkdir(parents=True)
    regular_root.mkdir()
    regular_module = regular_root / "candidate_pkg.py"
    regular_module.write_text("VALUE = 1\n", encoding="utf-8")

    python_runner.run_scenario_ok(
        "analysis/standard_resolution.py",
        "detailed_report_explains_namespace_candidate_hidden_by_later_regular_module",
        str(namespace_root),
        str(regular_root),
        str(regular_module),
    )


def test_detailed_report_correlates_repeated_failure_at_same_origin(
    python_runner: PythonRunner, tmp_path: Path
) -> None:
    module_dir = tmp_path / "repeated"
    module_dir.mkdir()
    module_file = module_dir / "repeated_source.py"
    module_file.write_text(
        "import os\n"
        "marker = __file__ + '.loaded'\n"
        "if os.path.exists(marker):\n"
        "    raise RuntimeError('second execution')\n"
        "open(marker, 'w').close()\n",
        encoding="utf-8",
    )

    python_runner.run_scenario_ok(
        "analysis/standard_resolution.py",
        "detailed_report_correlates_repeated_failure_at_same_origin",
        str(module_dir),
        str(module_file),
    )


def test_distributed_7782_fixture_explains_unreachable_editable_finder() -> None:
    fixture = Path(__file__).parents[2] / "reproductions" / "distributed-7782"
    proc = subprocess.run(
        [sys.executable, "-m", "metapathology", "invoke.py"],
        cwd=fixture,
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert proc.returncode == 0, proc.stderr
    assert "editable marker: None" in proc.stdout
    assert "[inferred] 'distributed': PathFinder produced namespace" in proc.stderr
    assert "later meta path entries were never reached: [_EditableFinder]" in proc.stderr
    assert "[inferred] PathFinder likely produced namespace for 'distributed' before later finders" in proc.stderr
    assert "next step: rerun with --capture-import-results to record the actual PathFinder result" in proc.stderr
