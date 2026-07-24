"""Run pytest#12179 under metapathology and verify its diagnostic evidence."""

import sys

import metapathology


def main() -> int:
    """Run the failing collection and assert the legacy-finder report semantics."""
    metapathology.install(report_at_exit=False)

    import pytest

    result = pytest.main(
        [
            "-c",
            "pyproject.toml",
            "--import-mode=importlib",
            "tests/repro_pkg/test_value.py",
        ]
    )
    document = metapathology.get_report()
    contracts = document["finder_contracts"]
    contract = next(item for item in contracts if item["finder_type_name"] == "_SixMetaPathImporter")
    assert contract["category"] == "legacy_only", contract
    event_ref = contract["observation_event_ref"]
    mutation = next(event for event in document["timeline"] if event["id"] == event_ref)
    assert mutation["kind"] == "meta_path_mutation", mutation
    assert mutation["added"] == ["_SixMetaPathImporter"], mutation
    assert any(frame["filename"].replace("\\", "/").endswith("boto/vendored/six.py") for frame in mutation["stack"])
    sys.stderr.write("legacy-finder evidence verified\n")
    sys.stderr.write(metapathology.render_report())
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
