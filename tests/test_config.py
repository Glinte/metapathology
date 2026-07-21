"""Pure installation-configuration resolution."""

from os import PathLike
from pathlib import Path
from typing import Literal, cast

import pytest

from metapathology._config import InstallRequest, normalize_report_destination, resolve_install_request


def _resolve(
    *,
    report_at_exit: bool = False,
    report_destination: str | None = None,
    report_destination_explicit: bool = False,
    report_format: str | None = None,
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
    use_environment: bool = True,
    configure_report: bool = True,
    current_report_destination: str | None = None,
    current_report_format: Literal["text", "json"] = "text",
    current_report_color: Literal["auto", "always", "never"] = "auto",
) -> InstallRequest:
    return resolve_install_request(
        report_at_exit=report_at_exit,
        report_destination=report_destination,
        report_destination_explicit=report_destination_explicit,
        report_format=report_format,
        report_color=report_color,
        monitor_path_hooks=monitor_path_hooks,
        monitor_importer_cache=monitor_importer_cache,
        monitor_sys_path=monitor_sys_path,
        deep=deep,
        deep_path_hooks=deep_path_hooks,
        deep_path_entry_finders=deep_path_entry_finders,
        deep_loaders=deep_loaders,
        deep_import_outcomes=deep_import_outcomes,
        deep_import_calls=deep_import_calls,
        use_environment=use_environment,
        configure_report=configure_report,
        current_report_destination=current_report_destination,
        current_report_format=current_report_format,
        current_report_color=current_report_color,
    )


def test_defaults_resolve_to_one_immutable_request() -> None:
    request = _resolve()

    assert request.report_at_exit is False
    assert request.report_destination is None
    assert request.report_format == "text"
    assert request.report_color == "auto"
    assert request.monitor_path_hooks is True
    assert request.monitor_importer_cache is True
    assert request.monitor_sys_path is False
    assert request.issues == ()
    with pytest.raises(AttributeError):
        setattr(request, "monitor_sys_path", True)


def test_deep_defaults_and_explicit_overrides_are_resolved_together() -> None:
    request = _resolve(deep=True, deep_loaders=False)

    assert request.monitor_sys_path is True
    assert request.deep_path_hooks is True
    assert request.deep_path_entry_finders is True
    assert request.deep_loaders is False
    assert request.deep_import_outcomes is True
    assert request.deep_import_calls is True


def test_environment_precedence_and_invalid_values_are_plain_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPATHOLOGY_REPORT", "capture.json")
    monkeypatch.setenv("METAPATHOLOGY_REPORT_FORMAT", "invalid")
    monkeypatch.setenv("METAPATHOLOGY_COLOR", "always")
    monkeypatch.setenv("METAPATHOLOGY_MONITOR_PATH_HOOKS", "sometimes")
    monkeypatch.setenv("METAPATHOLOGY_DEEP", "yes")
    monkeypatch.setenv("METAPATHOLOGY_DEEP_LOADERS", "0")

    request = _resolve(monitor_path_hooks=None)

    assert request.report_destination == "capture.json"
    assert request.report_format == "json"
    assert request.report_color == "always"
    assert request.monitor_path_hooks is True
    assert request.monitor_sys_path is True
    assert request.deep_loaders is False
    assert request.issues == (
        "environment_configuration.METAPATHOLOGY_MONITOR_PATH_HOOKS",
        "report_configuration",
    )


def test_repeat_without_report_configuration_preserves_active_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METAPATHOLOGY_REPORT", "ignored.json")
    request = _resolve(
        use_environment=False,
        configure_report=False,
        current_report_destination="active.txt",
        current_report_format="text",
        current_report_color="never",
    )

    assert request.report_destination == "active.txt"
    assert request.report_format == "text"
    assert request.report_color == "never"


def test_explicit_destination_recomputes_default_format_on_repeat() -> None:
    request = _resolve(
        report_destination="new.json",
        report_destination_explicit=True,
        use_environment=False,
        current_report_destination=None,
        current_report_format="text",
        current_report_color="always",
    )

    assert request.report_destination == "new.json"
    assert request.report_format == "json"
    assert request.report_color == "always"


def test_explicit_invalid_report_values_raise_before_request_creation() -> None:
    with pytest.raises(ValueError, match="unknown report format"):
        _resolve(report_format="yaml")
    with pytest.raises(ValueError, match="unknown color mode"):
        _resolve(report_color="sometimes")


def test_report_destination_is_reduced_separately(tmp_path: Path) -> None:
    destination = tmp_path / "report.json"
    assert normalize_report_destination(destination) == str(destination)
    with pytest.raises(TypeError, match="str, not bytes"):
        normalize_report_destination(cast(PathLike[str], b"report.json"))
