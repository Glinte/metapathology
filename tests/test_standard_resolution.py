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

from metapathology._report_data import _standard_spec_classification
from metapathology._spec import summarize_spec

RunPython = Callable[..., subprocess.CompletedProcess[str]]


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
        "assert '[inferred standard resolution]' in text\n"
        "assert 'later meta-path entries were unreachable: [LaterFinder]' in text\n"
        "print('OK')\n"
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
        "assert 'standard finder aggregate coverage: active_path_finder_aggregate' in text\n"
        "assert '[captured standard resolution]' in text\n"
        "print('OK')\n"
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
    assert "[inferred standard resolution] 'distributed': PathFinder produced namespace" in proc.stderr
    assert "later meta-path entries were unreachable: [_EditableFinder]" in proc.stderr
