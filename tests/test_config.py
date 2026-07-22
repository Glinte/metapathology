"""Pure installation-configuration resolution."""

import os
from os import PathLike
from pathlib import Path
from typing import cast

import pytest

from metapathology._config import (
    InstallRequest,
    ReportTarget,
    _UnresolvedMonitoringOptions,
    _UnresolvedReportingOptions,
    infer_report_format,
    monitoring_request,
    normalize_report_destination,
    resolve_install_request,
)


def _resolve(
    *,
    report_at_exit: bool = False,
    report_destination: list[str] | None = None,
    report_text: list[str] | None = None,
    report_json: list[str] | None = None,
    report_color: str | None = None,
    monitor_path_hooks: bool | None = None,
    monitor_importer_cache: bool | None = None,
    monitor_sys_path: bool | None = None,
    deep: bool | None = None,
    deep_path_hooks: bool | None = None,
    deep_path_entry_finders: bool | None = None,
    deep_loaders: bool | None = None,
    deep_import_outcomes: bool | None = None,
    deep_import_calls: bool | None = None,
    speculative_replay: bool | None = None,
    use_environment: bool = True,
    configure_report: bool = True,
    current_report_targets: tuple[ReportTarget, ...] = (ReportTarget(None, "text", "auto"),),
) -> InstallRequest:
    return resolve_install_request(
        reporting=_UnresolvedReportingOptions(
            report_at_exit=report_at_exit,
            destinations=report_destination or [],
            text_destinations=report_text or [],
            json_destinations=report_json or [],
            color=report_color,
        ),
        monitoring=_UnresolvedMonitoringOptions(
            monitor_path_hooks=monitor_path_hooks,
            monitor_importer_cache=monitor_importer_cache,
            monitor_sys_path=monitor_sys_path,
            deep=deep,
            deep_path_hooks=deep_path_hooks,
            deep_path_entry_finders=deep_path_entry_finders,
            deep_loaders=deep_loaders,
            deep_import_outcomes=deep_import_outcomes,
            deep_import_calls=deep_import_calls,
            speculative_replay=speculative_replay,
        ),
        use_environment=use_environment,
        configure_report=configure_report,
        current_report_targets=current_report_targets,
    )


def _targets(request: InstallRequest) -> list[tuple[str | None, str, str]]:
    return [(target.destination, target.format, target.color) for target in request.report_targets]


@pytest.mark.parametrize(
    ("destination", "expected"),
    [("report.json", "json"), ("report.txt", "text"), ("report.text", "text"), ("-", "text")],
)
def test_report_format_inference(destination: str, expected: str) -> None:
    assert infer_report_format(destination) == expected


@pytest.mark.parametrize("destination", ["report", "report.log", ".report"])
def test_report_format_inference_rejects_unknown_extensions(destination: str) -> None:
    with pytest.raises(ValueError, match="--report-text or --report-json"):
        infer_report_format(destination)


def test_defaults_resolve_to_one_immutable_request() -> None:
    request = _resolve()

    assert request.report_at_exit is False
    assert _targets(request) == [(None, "text", "auto")]
    assert request.monitor_path_hooks is True
    assert request.monitor_importer_cache is True
    assert request.monitor_sys_path is False
    assert request.issues == ()
    with pytest.raises(AttributeError):
        setattr(request, "monitor_sys_path", True)

    monitoring = monitoring_request(request)
    assert monitoring.monitor_path_hooks is True
    assert not hasattr(monitoring, "report_targets")


def test_deep_defaults_and_explicit_overrides_are_resolved_together() -> None:
    request = _resolve(deep=True, deep_loaders=False)

    assert request.monitor_sys_path is True
    assert request.deep_path_hooks is True
    assert request.deep_path_entry_finders is True
    assert request.deep_loaders is False
    assert request.deep_import_outcomes is True
    assert request.deep_import_calls is True
    assert request.speculative_replay is False


def test_speculative_replay_resolves_from_param_and_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _resolve().speculative_replay is False
    assert _resolve(speculative_replay=True).speculative_replay is True

    monkeypatch.setenv("METAPATHOLOGY_SPECULATIVE_REPLAY", "on")
    assert _resolve().speculative_replay is True
    assert _resolve(speculative_replay=False).speculative_replay is False


def test_multiple_report_channels_are_resolved_and_deduplicated() -> None:
    request = _resolve(
        report_destination=["capture.json", "notes.txt"],
        report_text=["-", "capture.json", "-"],
        report_json=["raw.data", "raw.data"],
        report_color="always",
    )

    assert _targets(request) == [
        ("capture.json", "json", "always"),
        ("notes.txt", "text", "always"),
        (None, "text", "always"),
        ("capture.json", "text", "always"),
        ("raw.data", "json", "always"),
    ]


def test_environment_destinations_are_split_and_invalid_entries_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "METAPATHOLOGY_REPORT",
        os.pathsep.join(("capture.json", "unknown.log", "notes.txt")),
    )
    monkeypatch.setenv("METAPATHOLOGY_COLOR", "always")
    monkeypatch.setenv("METAPATHOLOGY_MONITOR_PATH_HOOKS", "sometimes")

    request = _resolve()

    assert _targets(request) == [
        ("capture.json", "json", "always"),
        ("unknown.log", "text", "always"),
        ("notes.txt", "text", "always"),
    ]
    assert request.issues == (
        "environment_configuration.METAPATHOLOGY_MONITOR_PATH_HOOKS",
        "environment_configuration.METAPATHOLOGY_REPORT",
    )


def test_repeat_without_report_configuration_preserves_active_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPATHOLOGY_REPORT", "ignored.json")
    current = (ReportTarget("active.txt", "text", "never"), ReportTarget("active.json", "json", "never"))

    request = _resolve(use_environment=False, configure_report=False, current_report_targets=current)

    assert request.report_targets is current


def test_explicit_destination_infers_format_and_preserves_color_on_repeat() -> None:
    request = _resolve(
        report_destination=["new.json"],
        use_environment=False,
        current_report_targets=(ReportTarget(None, "text", "always"),),
    )

    assert _targets(request) == [("new.json", "json", "always")]


def test_explicit_invalid_report_values_raise_before_request_creation() -> None:
    with pytest.raises(ValueError, match="--report-text or --report-json"):
        _resolve(report_destination=["capture.yaml"])
    with pytest.raises(ValueError, match="unknown color mode"):
        _resolve(report_color="sometimes")


def test_report_destinations_are_reduced_separately(tmp_path: Path) -> None:
    first = tmp_path / "report.json"
    second = tmp_path / "report.txt"
    assert normalize_report_destination(first) == [str(first)]
    assert normalize_report_destination([first, second]) == [str(first), str(second)]
    assert normalize_report_destination(None) == []
    with pytest.raises(TypeError, match="str, not bytes"):
        normalize_report_destination(cast(PathLike[str], b"report.json"))
