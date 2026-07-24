"""Isolated child scenarios extracted from tests/monitoring/test_importer_cache.py."""

import sys

_scenario = sys.argv.pop(1)

if _scenario == "cache_monitoring_configuration_is_fixed_for_an_active_installation":
    import metapathology

    monitor = metapathology.install(
        report_at_exit=False,
        capture=metapathology.CaptureConfig(importer_cache=False),
    )
    assert not monitor.importer_cache_enabled
    try:
        metapathology.install(
            report_at_exit=False,
            capture=metapathology.CaptureConfig(importer_cache=True),
        )
    except RuntimeError:
        pass
    else:
        raise AssertionError("active capture configuration changed")
    assert not monitor.importer_cache_enabled

elif _scenario == "cache_monitoring_never_replaces_the_dictionary":
    import sys
    import metapathology

    cache = sys.path_importer_cache
    monitor = metapathology.install(report_at_exit=False)
    assert sys.path_importer_cache is cache
    assert isinstance(monitor.initial_importer_cache, tuple)
    metapathology.uninstall()
    assert sys.path_importer_cache is cache

elif _scenario == "path_hook_boundaries_record_add_replace_and_clear":
    import sys
    import metapathology
    from metapathology import ImporterCacheChange

    class FirstFinder:
        pass

    path = "metapathology-cache-check"
    monitor = metapathology.install(report_at_exit=False)
    first = FirstFinder()
    sys.path_importer_cache[path] = first
    sys.path_hooks.append(lambda value: None)
    sys.path_importer_cache[path] = None
    sys.path_hooks.append(lambda value: None)
    sys.path_importer_cache.clear()
    sys.path_hooks.append(lambda value: None)
    diffs = [event for event in monitor.events() if isinstance(event, ImporterCacheChange)]
    added = next(entry for diff in diffs for entry in diff.added if entry.path == path)
    assert added.finder is not None and added.finder.object_id == id(first)
    replaced = next(item for diff in diffs for item in diff.replaced if item.path == path)
    assert replaced.before is not None and replaced.after is None
    removed = next(entry for diff in diffs for entry in diff.removed if entry.path == path)
    assert removed.finder is None
    assert all(left.sequence < right.sequence for left, right in zip(diffs, diffs[1:]))

elif _scenario == "report_observes_negative_entries_and_ignores_non_string_keys":
    import json
    import sys
    import metapathology

    path = "metapathology-negative-check"
    metapathology.install(report_at_exit=False)
    sys.path_importer_cache[path] = None
    sys.path_importer_cache[object()] = object()
    document = metapathology.get_report()
    events = [event for event in document["timeline"] if event["kind"] == "importer_cache_change"]
    entry = next(entry for event in events for entry in event["data"]["added"] if entry["path"] == path)
    assert entry["finder"] is None
    report_snapshot = next(
        snapshot for snapshot in document["snapshots"] if snapshot["id"] == "snapshot:importer-cache:report"
    )
    assert report_snapshot["data"]["non_string_keys"] >= 1
    assert any(item["path"] == path and item["finder"] is None for item in report_snapshot["data"]["entries"])

elif _scenario == "import_audit_uses_only_cache_fingerprint":
    import sys
    import metapathology

    class CountingCache(dict):
        items_calls = 0

        def items(self):
            type(self).items_calls += 1
            return super().items()

    cache = CountingCache(sys.path_importer_cache)
    sys.path_importer_cache = cache
    metapathology.install(report_at_exit=False)
    CountingCache.items_calls = 0
    import colorsys

    assert CountingCache.items_calls == 0, CountingCache.items_calls
    metapathology.render_report()
    assert CountingCache.items_calls >= 1

elif _scenario == "real_missing_path_creates_a_negative_cache_entry":
    import json
    import sys
    import metapathology

    missing_path = "metapathology-path-that-does-not-exist"
    sys.path.insert(0, missing_path)
    metapathology.install(report_at_exit=False)
    try:
        import metapathology_module_that_does_not_exist
    except ModuleNotFoundError:
        pass
    assert sys.path_importer_cache[missing_path] is None
    document = metapathology.get_report()
    snapshot = next(item for item in document["snapshots"] if item["id"] == "snapshot:importer-cache:report")
    assert any(entry["path"] == missing_path and entry["finder"] is None for entry in snapshot["data"]["entries"])

elif _scenario == "text_report_describes_negative_and_replaced_cache_entries":
    import sys
    import metapathology

    class Finder:
        pass

    path = "metapathology-text-cache-check"
    metapathology.install(report_at_exit=False)
    sys.path_importer_cache[path] = Finder()
    sys.path_hooks.append(lambda value: None)
    sys.path_importer_cache[path] = None
    text = metapathology.render_report()
    assert "-- sys.path_importer_cache changes" in text, text
    assert repr(path) in text, text
    assert "Finder" in text and "None (no importer for this path)" in text, text
else:
    raise ValueError(f"unknown scenario: {_scenario}")
