"""Pure command-line grammar and invocation projection."""

from collections.abc import Sequence

import pytest

from metapathology.__main__ import _Arguments, _Invocation, _make_parser

_BOOLEAN_OPTIONS = (
    ("--import-audit", "import_audit"),
    ("--meta-path", "meta_path"),
    ("--finder-attribution", "finder_attribution"),
    ("--path-hooks", "path_hooks"),
    ("--importer-cache", "importer_cache"),
    ("--sys-path", "sys_path"),
    ("--detailed-capture", "detailed_capture"),
    ("--capture-path-hook-calls", "capture_path_hook_calls"),
    ("--capture-path-entry-finder-calls", "capture_path_entry_finder_calls"),
    ("--capture-loader-calls", "capture_loader_calls"),
    ("--capture-import-results", "capture_import_results"),
    ("--capture-import-calls", "capture_import_calls"),
    ("--checks", "checks"),
    ("--standard-path-check", "standard_path_check"),
    ("--displaced-finder-check", "displaced_finder_check"),
    ("--unsafe-explore-import-branches", "unsafe_explore_import_branches"),
)


def _parse(arguments: Sequence[str]) -> _Invocation:
    parsed = _Arguments()
    _make_parser().parse_args([*arguments, "target.py"], namespace=parsed)
    return parsed.invocation()


@pytest.mark.parametrize(("option", "field"), _BOOLEAN_OPTIONS)
def test_boolean_options_project_both_values(option: str, field: str) -> None:
    assert getattr(_parse([option]), field) is True
    assert getattr(_parse([f"--no-{option[2:]}"]), field) is False


def test_report_options_and_target_remainder_are_projected() -> None:
    invocation = _parse(
        [
            "--report",
            "inferred.json",
            "--report-text",
            "plain.data",
            "--report-json",
            "structured.data",
            "--color",
            "always",
            "--",
            "--target-name.py",
            "--target-option",
        ]
    )

    assert invocation.report_destination == ["inferred.json"]
    assert invocation.report_text == ["plain.data"]
    assert invocation.report_json == ["structured.data"]
    assert invocation.report_color == "always"
    assert invocation.target == "--target-name.py"
    assert invocation.target_args == ("--target-option", "target.py")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--report-format", "yaml"),
        ("--deep",),
        ("--deep-loaders",),
        ("--probes",),
        ("--standard-path-probe",),
        ("--color", "sometimes"),
    ],
)
def test_removed_and_invalid_options_are_rejected_without_abbreviation(
    arguments: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        _parse(arguments)

    assert raised.value.code == 2
    assert "error:" in capsys.readouterr().err
