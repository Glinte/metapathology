"""Standard finder attribution coverage."""

from pathlib import Path

from conftest import RunPython


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
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
