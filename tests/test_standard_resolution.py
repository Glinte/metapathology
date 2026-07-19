"""Standard finder attribution coverage."""

import subprocess
import sys
from collections.abc import Callable
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

from metapathology._report_data import (
    ImportAttempt,
    StandardResolution,
    _repeated_load_failure_findings,
    _standard_spec_classification,
)
from metapathology._spec import summarize_spec

RunPython = Callable[..., subprocess.CompletedProcess[str]]


def test_repeated_load_failure_attaches_failed_attempt_without_own_resolution() -> None:
    attempts = (
        ImportAttempt(1, "native", 1, (1, 3), 1, "main", "loaded", "absent_at_report"),
        ImportAttempt(2, "native", 10, (10, 15), 1, "main", "failed", "absent_at_report"),
        ImportAttempt(3, "native", 11, (11, 12, 13), 1, "main", "failed", "absent_at_report"),
    )
    resolutions = (
        StandardResolution(
            attempt_id=1,
            fullname="native",
            finder_type_name="PathFinder",
            category="extension",
            loader_type_name="ExtensionFileLoader",
            origin="native.pyd",
            evidence_level="captured",
            state_phase="import",
            event_seq=2,
            component_event_seqs=(),
            later_finders=(),
        ),
        StandardResolution(
            attempt_id=3,
            fullname="native",
            finder_type_name="PathFinder",
            category="extension",
            loader_type_name="ExtensionFileLoader",
            origin="native.pyd",
            evidence_level="captured",
            state_phase="import",
            event_seq=12,
            component_event_seqs=(),
            later_finders=(),
        ),
    )

    finding = _repeated_load_failure_findings(attempts, resolutions, 0)[0]

    assert {10, 15} <= set(finding.supporting_event_seqs)


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


def test_default_report_infers_namespace_before_later_finder(run_python: RunPython, tmp_path: Path) -> None:
    (tmp_path / "standard_namespace").mkdir()
    proc = run_python(
        "import json, sys, metapathology\n"
        "class LaterFinder:\n"
        "    def find_spec(self, fullname, path=None, target=None): return None\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "sys.meta_path.append(LaterFinder())\n"
        "import standard_namespace\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "resolution = next(item for item in document['standard_resolutions'] "
        "if item['fullname'] == 'standard_namespace')\n"
        "assert resolution['finder_type_name'] == 'PathFinder'\n"
        "assert resolution['category'] == 'namespace'\n"
        "assert resolution['evidence_level'] == 'inferred'\n"
        "assert resolution['state_phase'] == 'report'\n"
        "assert resolution['event_ref'] is None\n"
        "assert resolution['later_finders'] == ['LaterFinder']\n"
        "assert not any(event['kind'] == 'find_spec_call' "
        "and event['fullname'] == 'standard_namespace' "
        "and event['finder_type_name'] == 'LaterFinder' for event in document['timeline'])\n"
        "text = metapathology.render_report()\n"
        "assert '[inferred]' in text\n"
        "assert 'later meta path entries were never reached: [LaterFinder]' in text\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_ordinary_standard_modules_do_not_claim_later_finders_are_relevant(
    run_python: RunPython, tmp_path: Path
) -> None:
    (tmp_path / "ordinary_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = run_python(
        "import json, sys, metapathology\n"
        "class LaterFinder:\n"
        "    def find_spec(self, fullname, path=None, target=None): return None\n"
        "metapathology.install(report_at_exit=False, deep_import_outcomes=True)\n"
        "sys.meta_path.append(LaterFinder())\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import ordinary_source\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "resolution = next(item for item in document['standard_resolutions'] "
        "if item['fullname'] == 'ordinary_source')\n"
        "assert resolution['later_finders'] == []\n"
        "assert not any(item['kind'] == 'standard_winner_precedence' "
        "and item['subject'] == 'ordinary_source' for item in document['explanations'])\n"
        "print('OK')\n",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_deep_report_captures_source_resolution(run_python: RunPython, tmp_path: Path) -> None:
    module_dir = tmp_path / "deep_standard"
    module_dir.mkdir()
    (module_dir / "standard_source.py").write_text("VALUE = 1\n", encoding="utf-8")
    proc = run_python(
        "import json, sys, metapathology\n"
        "metapathology.install(report_at_exit=False, deep=True)\n"
        f"sys.path.insert(0, {str(module_dir)!r})\n"
        "import standard_source\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "resolution = next(item for item in document['standard_resolutions'] "
        "if item['fullname'] == 'standard_source')\n"
        "assert resolution['category'] == 'source'\n"
        "assert resolution['loader_type_name'] == 'SourceFileLoader'\n"
        "assert resolution['evidence_level'] == 'captured'\n"
        "assert resolution['state_phase'] == 'import'\n"
        "assert resolution['event_ref'] is not None\n"
        "assert resolution['component_event_refs']\n"
        "component = next(event for event in document['timeline'] "
        "if event['id'] == resolution['component_event_refs'][0])\n"
        f"assert component['path'] == {str(module_dir)!r}\n"
        "text = metapathology.render_report()\n"
        "assert 'PathFinder result capture: active path finder aggregate' in text\n"
        "assert '[captured]' in text\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_deep_report_explains_namespace_candidate_displaced_by_later_regular_module(
    run_python: RunPython, tmp_path: Path
) -> None:
    namespace_root = tmp_path / "namespace"
    regular_root = tmp_path / "regular"
    (namespace_root / "candidate_pkg").mkdir(parents=True)
    regular_root.mkdir()
    regular_module = regular_root / "candidate_pkg.py"
    regular_module.write_text("VALUE = 1\n", encoding="utf-8")

    proc = run_python(
        "import json, sys, metapathology\n"
        "sys.path[:0] = sys.argv[1:3]\n"
        "metapathology.install(report_at_exit=False, deep=True)\n"
        "import candidate_pkg\n"
        "try:\n"
        "    import candidate_pkg.child\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('regular module unexpectedly allowed a child import')\n"
        "try:\n"
        "    import candidate_pkg.other\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('regular module unexpectedly allowed another child import')\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "finding = next(item for item in document['findings'] "
        "if item['kind'] == 'regular_module_shadows_namespace')\n"
        "assert finding['module'] == 'candidate_pkg'\n"
        "assert finding['severity'] == 'actionable'\n"
        "explanations = [item for item in document['explanations'] "
        "if item['kind'] == 'namespace_candidate_displaced']\n"
        "assert {item['subject'] for item in explanations} == "
        "{'candidate_pkg.child', 'candidate_pkg.other'}\n"
        "explanation = explanations[0]\n"
        "assert explanation['effect_status'] == 'descendant_failed'\n"
        "assert explanation['cause_finding_ref'] == finding['id']\n"
        "assert explanation['candidate_path'] == sys.argv[1]\n"
        "assert explanation['origin'] == sys.argv[3]\n"
        "assert explanation['confidence'] == 'correlated'\n"
        "assert len(explanation['event_refs']) >= 3\n"
        "text = metapathology.render_report()\n"
        "assert 'namespace candidate from' in text\n"
        "assert 'continued searching and selected the regular module' in text\n"
        "print('OK')\n",
        str(namespace_root),
        str(regular_root),
        str(regular_module),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_deep_report_correlates_repeated_failure_at_same_origin(run_python: RunPython, tmp_path: Path) -> None:
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

    proc = run_python(
        "import json, sys, metapathology\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "metapathology.install(report_at_exit=False, deep=True)\n"
        "import repeated_source\n"
        "del sys.modules['repeated_source']\n"
        "try:\n"
        "    import repeated_source\n"
        "except RuntimeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('second import unexpectedly loaded')\n"
        "try:\n"
        "    import repeated_source\n"
        "except RuntimeError:\n"
        "    pass\n"
        "else:\n"
        "    raise AssertionError('third import unexpectedly loaded')\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "finding = next(item for item in document['findings'] "
        "if item['kind'] == 'repeated_load_failure')\n"
        "assert finding['module'] == 'repeated_source'\n"
        "assert finding['severity'] == 'actionable'\n"
        "assert finding['evidence']['level'] == 'correlated'\n"
        "assert {'same_loader', 'same_origin', 'earlier_attempt_loaded', "
        "'later_attempt_failed'} <= set(finding['signals'])\n"
        "explanation = next(item for item in document['explanations'] "
        "if item['kind'] == 'repeated_load_failure')\n"
        "assert explanation['cause_finding_ref'] == finding['id']\n"
        "assert explanation['finder_type_name'] == 'SourceFileLoader'\n"
        "assert explanation['origin'] == sys.argv[2]\n"
        "assert explanation['effect_status'] == 'later_import_failed'\n"
        "failed_attempts = [item for item in document['import_attempts'] "
        "if item['fullname'] == 'repeated_source' and item['progress'] == 'failed']\n"
        "assert len(failed_attempts) == 2\n"
        "assert {ref for attempt in failed_attempts for ref in attempt['evidence_event_refs']} "
        "<= set(explanation['event_refs'])\n"
        "text = metapathology.render_report()\n"
        "assert \"same origin was selected again for 'repeated_source'\" in text\n"
        "assert 'the earlier import loaded, but the later import failed' in text\n"
        "print('OK')\n",
        str(module_dir),
        str(module_file),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_distributed_7782_fixture_explains_unreachable_editable_finder() -> None:
    fixture = Path(__file__).parents[1] / "reproductions" / "distributed-7782"
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
    assert "next step: rerun with --deep-import-outcomes to record the actual PathFinder result" in proc.stderr
