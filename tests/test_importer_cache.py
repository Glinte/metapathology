"""Passive ``sys.path_importer_cache`` observation."""

import subprocess
from collections.abc import Callable

from hypothesis import given
from hypothesis import strategies as st

from metapathology import ImportObjectRef
from metapathology._monitor import _diff_importer_cache

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


def _snapshot(values: dict[str, int | None]) -> dict[str, ImportObjectRef | None]:
    return {
        path: None if object_id is None else ImportObjectRef(object_id=object_id, type_name="Finder")
        for path, object_id in values.items()
    }


def _plain(snapshot: dict[str, ImportObjectRef | None]) -> dict[str, int | None]:
    return {path: None if finder is None else finder.object_id for path, finder in snapshot.items()}


@given(
    before=st.dictionaries(st.text(max_size=8), st.one_of(st.none(), st.integers(min_value=0, max_value=20))),
    after=st.dictionaries(st.text(max_size=8), st.one_of(st.none(), st.integers(min_value=0, max_value=20))),
)
def test_generated_diffs_reconstruct_each_next_snapshot(
    before: dict[str, int | None],
    after: dict[str, int | None],
) -> None:
    reconstructed = _snapshot(before)
    added, removed, replaced = _diff_importer_cache(reconstructed, _snapshot(after))

    for entry in removed:
        del reconstructed[entry.path]
    for entry in added:
        reconstructed[entry.path] = entry.finder
    for replacement in replaced:
        reconstructed[replacement.path] = replacement.after

    assert _plain(reconstructed) == after


def test_cache_monitoring_is_default_on_and_later_enableable(run_python: RunPython) -> None:
    proc = run_python(
        "import metapathology\n"
        "monitor = metapathology.install(\n"
        "    report_at_exit=False, monitor_importer_cache=False,\n"
        ")\n"
        "assert not monitor.importer_cache_enabled\n"
        "metapathology.install(\n"
        "    report_at_exit=False, monitor_importer_cache=True,\n"
        ")\n"
        "assert monitor.importer_cache_enabled\n"
        "metapathology.install(\n"
        "    report_at_exit=False, monitor_importer_cache=False,\n"
        ")\n"
        "assert monitor.importer_cache_enabled\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_cache_monitoring_never_replaces_the_dictionary(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "import metapathology\n"
        "cache = sys.path_importer_cache\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "assert sys.path_importer_cache is cache\n"
        "assert isinstance(monitor.initial_importer_cache, tuple)\n"
        "metapathology.uninstall()\n"
        "assert sys.path_importer_cache is cache\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_path_hook_boundaries_record_add_replace_and_clear(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "import metapathology\n"
        "from metapathology import ImporterCacheDiff\n"
        "class FirstFinder: pass\n"
        "path = 'metapathology-cache-probe'\n"
        "monitor = metapathology.install(report_at_exit=False)\n"
        "first = FirstFinder()\n"
        "sys.path_importer_cache[path] = first\n"
        "sys.path_hooks.append(lambda value: None)\n"
        "sys.path_importer_cache[path] = None\n"
        "sys.path_hooks.append(lambda value: None)\n"
        "sys.path_importer_cache.clear()\n"
        "sys.path_hooks.append(lambda value: None)\n"
        "diffs = [event for event in monitor.events() if isinstance(event, ImporterCacheDiff)]\n"
        "added = next(entry for diff in diffs for entry in diff.added if entry.path == path)\n"
        "assert added.finder is not None and added.finder.object_id == id(first)\n"
        "replaced = next(item for diff in diffs for item in diff.replaced if item.path == path)\n"
        "assert replaced.before is not None and replaced.after is None\n"
        "removed = next(entry for diff in diffs for entry in diff.removed if entry.path == path)\n"
        "assert removed.finder is None\n"
        "assert all(left.seq < right.seq for left, right in zip(diffs, diffs[1:]))\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_report_observes_negative_entries_and_ignores_non_string_keys(run_python: RunPython) -> None:
    proc = run_python(
        "import json\n"
        "import sys\n"
        "import metapathology\n"
        "path = 'metapathology-negative-probe'\n"
        "metapathology.install(report_at_exit=False)\n"
        "sys.path_importer_cache[path] = None\n"
        "sys.path_importer_cache[object()] = object()\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "events = [event for event in document['timeline'] if event['kind'] == 'importer_cache_diff']\n"
        "entry = next(entry for event in events for entry in event['added'] if entry['path'] == path)\n"
        "assert entry['finder'] is None\n"
        "report_snapshot = next(\n"
        "    snapshot for snapshot in document['snapshots']\n"
        "    if snapshot['id'] == 'snapshot:importer-cache:report'\n"
        ")\n"
        "assert report_snapshot['non_string_keys'] >= 1\n"
        "assert any(item['path'] == path and item['finder'] is None for item in report_snapshot['entries'])\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_import_audit_uses_only_cache_fingerprint(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "import metapathology\n"
        "class CountingCache(dict):\n"
        "    items_calls = 0\n"
        "    def items(self):\n"
        "        type(self).items_calls += 1\n"
        "        return super().items()\n"
        "cache = CountingCache(sys.path_importer_cache)\n"
        "sys.path_importer_cache = cache\n"
        "metapathology.install(report_at_exit=False)\n"
        "CountingCache.items_calls = 0\n"
        "import colorsys\n"
        "assert CountingCache.items_calls == 0, CountingCache.items_calls\n"
        "metapathology.render_report()\n"
        "assert CountingCache.items_calls >= 1\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_real_missing_path_creates_a_negative_cache_entry(run_python: RunPython) -> None:
    proc = run_python(
        "import json\n"
        "import sys\n"
        "import metapathology\n"
        "missing_path = 'metapathology-path-that-does-not-exist'\n"
        "sys.path.insert(0, missing_path)\n"
        "metapathology.install(report_at_exit=False)\n"
        "try:\n"
        "    import metapathology_module_that_does_not_exist\n"
        "except ModuleNotFoundError:\n"
        "    pass\n"
        "assert sys.path_importer_cache[missing_path] is None\n"
        "document = json.loads(metapathology.render_report(format='json'))\n"
        "snapshot = next(\n"
        "    item for item in document['snapshots']\n"
        "    if item['id'] == 'snapshot:importer-cache:report'\n"
        ")\n"
        "assert any(\n"
        "    entry['path'] == missing_path and entry['finder'] is None\n"
        "    for entry in snapshot['entries']\n"
        ")\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_text_report_describes_negative_and_replaced_cache_entries(run_python: RunPython) -> None:
    proc = run_python(
        "import sys\n"
        "import metapathology\n"
        "class Finder: pass\n"
        "path = 'metapathology-text-cache-probe'\n"
        "metapathology.install(report_at_exit=False)\n"
        "sys.path_importer_cache[path] = Finder()\n"
        "sys.path_hooks.append(lambda value: None)\n"
        "sys.path_importer_cache[path] = None\n"
        "text = metapathology.render_report()\n"
        "assert '-- sys.path_importer_cache changes' in text, text\n"
        "assert repr(path) in text, text\n"
        "assert 'Finder' in text and 'None (no importer for this path)' in text, text\n"
        "print('OK')\n"
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
