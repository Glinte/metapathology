"""Passive ``sys.path_importer_cache`` observation."""

from hypothesis import given
from hypothesis import strategies as st
from support import PythonRunner

from metapathology import ObjectIdentity
from metapathology._importer_cache import _diff_importer_cache


def _snapshot(values: dict[str, int | None]) -> dict[str, ObjectIdentity | None]:
    return {
        path: None if object_id is None else ObjectIdentity(object_id=object_id, type_name="Finder")
        for path, object_id in values.items()
    }


def _plain(snapshot: dict[str, ObjectIdentity | None]) -> dict[str, int | None]:
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


def test_cache_monitoring_configuration_is_fixed_for_an_active_installation(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/importer_cache.py", "cache_monitoring_configuration_is_fixed_for_an_active_installation"
    )


def test_cache_monitoring_never_replaces_the_dictionary(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/importer_cache.py", "cache_monitoring_never_replaces_the_dictionary")


def test_path_hook_boundaries_record_add_replace_and_clear(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/importer_cache.py", "path_hook_boundaries_record_add_replace_and_clear")


def test_report_observes_negative_entries_and_ignores_non_string_keys(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/importer_cache.py", "report_observes_negative_entries_and_ignores_non_string_keys"
    )


def test_import_audit_uses_only_cache_fingerprint(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/importer_cache.py", "import_audit_uses_only_cache_fingerprint")


def test_real_missing_path_creates_a_negative_cache_entry(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok("monitoring/importer_cache.py", "real_missing_path_creates_a_negative_cache_entry")


def test_text_report_describes_negative_and_replaced_cache_entries(python_runner: PythonRunner) -> None:
    python_runner.run_scenario_ok(
        "monitoring/importer_cache.py", "text_report_describes_negative_and_replaced_cache_entries"
    )
