"""Pure installation-configuration resolution."""

import os
import string
from os import PathLike
from pathlib import Path
from typing import cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from metapathology._config import (
    AnalysisConfig,
    CaptureConfig,
    DetailedCaptureConfig,
    InstallRequest,
    ReportTarget,
    _UnresolvedReportingOptions,
    infer_report_format,
    monitoring_request,
    normalize_report_destination,
    resolve_install_request,
)

_BOOLEAN_FIELDS = st.sampled_from(
    (
        ("import_audit", "METAPATHOLOGY_IMPORT_AUDIT", True),
        ("path_hooks", "METAPATHOLOGY_PATH_HOOKS", True),
        ("sys_path", "METAPATHOLOGY_SYS_PATH", False),
        ("displaced_finder_check", "METAPATHOLOGY_DISPLACED_FINDER_CHECK", False),
    )
)
_ENVIRONMENT_BOOLEAN = st.one_of(
    st.none(),
    st.sampled_from(("1", " true ", "yes", "on", "0", " false ", "no", "off", "invalid")),
)


def _resolve(
    *,
    report_at_exit: bool = False,
    report_destination: list[str] | None = None,
    report_text: list[str] | None = None,
    report_json: list[str] | None = None,
    report_color: str | None = None,
    capture: CaptureConfig = CaptureConfig(),
    analysis: AnalysisConfig = AnalysisConfig(),
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
        capture=capture,
        analysis=analysis,
        use_environment=use_environment,
        configure_report=configure_report,
        current_report_targets=current_report_targets,
    )


def _targets(request: InstallRequest) -> list[tuple[str | None, str, str]]:
    return [(target.destination, target.format, target.color) for target in request.report_targets]


@given(
    stem=st.text(alphabet=string.ascii_letters, min_size=1, max_size=12),
    extension=st.sampled_from((".json", ".JSON", ".txt", ".TXT", ".text", ".TEXT")),
)
def test_report_format_inference_is_case_insensitive(stem: str, extension: str) -> None:
    expected = "json" if extension.lower() == ".json" else "text"
    assert infer_report_format(stem + extension) == expected
    assert infer_report_format("-") == "text"


@given(
    stem=st.text(alphabet=string.ascii_letters, min_size=1, max_size=12),
    extension=st.text(alphabet=string.ascii_letters, min_size=1, max_size=8).filter(
        lambda value: value.lower() not in {"json", "text", "txt"}
    ),
)
def test_report_format_inference_rejects_unknown_extensions(stem: str, extension: str) -> None:
    with pytest.raises(ValueError, match="--report-text or --report-json"):
        infer_report_format(f"{stem}.{extension}")


def test_defaults_resolve_to_one_immutable_request() -> None:
    request = _resolve(use_environment=False)

    assert request.report_at_exit is False
    assert _targets(request) == [(None, "text", "auto")]
    assert request.capture.import_audit is True
    assert request.capture.meta_path is True
    assert request.capture.finder_attribution is True
    assert request.capture.path_hooks is True
    assert request.capture.importer_cache is True
    assert request.capture.sys_path is False
    assert request.analysis.standard_path_check is True
    assert request.analysis.displaced_finder_check is False
    assert request.issues == ()
    with pytest.raises(AttributeError):
        setattr(request.capture, "sys_path", True)

    monitoring = monitoring_request(request)
    assert monitoring.path_hooks is True
    assert not hasattr(monitoring, "report_targets")


def test_detailed_and_check_group_settings_respect_explicit_overrides() -> None:
    request = _resolve(
        capture=CaptureConfig(detailed=DetailedCaptureConfig(enabled=True, loaders=False)),
        analysis=AnalysisConfig(checks=True, standard_path_check=False),
        use_environment=False,
    )

    assert request.capture.sys_path is True
    assert request.capture.detailed.path_hooks is True
    assert request.capture.detailed.path_entry_finders is True
    assert request.capture.detailed.loaders is False
    assert request.capture.detailed.import_results is True
    assert request.capture.detailed.import_calls is True
    assert request.analysis.standard_path_check is False
    assert request.analysis.displaced_finder_check is True


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(field=_BOOLEAN_FIELDS, explicit=st.one_of(st.none(), st.booleans()), raw=_ENVIRONMENT_BOOLEAN)
def test_boolean_configuration_obeys_explicit_environment_default_precedence(
    monkeypatch: pytest.MonkeyPatch,
    field: tuple[str, str, bool],
    explicit: bool | None,
    raw: str | None,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("METAPATHOLOGY_"):
            monkeypatch.delenv(name, raising=False)
    attribute, environment_name, default = field
    if raw is not None:
        monkeypatch.setenv(environment_name, raw)

    capture = CaptureConfig()
    analysis = AnalysisConfig()
    if attribute == "import_audit":
        capture = CaptureConfig(import_audit=explicit)
    elif attribute == "path_hooks":
        capture = CaptureConfig(path_hooks=explicit)
    elif attribute == "sys_path":
        capture = CaptureConfig(sys_path=explicit)
    else:
        analysis = AnalysisConfig(displaced_finder_check=explicit)

    request = _resolve(capture=capture, analysis=analysis)
    resolved = (
        getattr(request.analysis, attribute)
        if attribute == "displaced_finder_check"
        else getattr(request.capture, attribute)
    )
    normalized = None if raw is None else raw.strip().lower()
    environment_value = normalized in {"1", "true", "yes", "on"}
    valid_environment = normalized in {"1", "true", "yes", "on", "0", "false", "no", "off"}
    expected = explicit if explicit is not None else environment_value if valid_environment else default

    assert resolved is expected
    issue = f"environment_configuration.{environment_name}"
    assert (issue in request.issues) is (explicit is None and raw is not None and not valid_environment)


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

    request = _resolve()

    assert _targets(request) == [
        ("capture.json", "json", "always"),
        ("unknown.log", "text", "always"),
        ("notes.txt", "text", "always"),
    ]
    assert request.issues == ("environment_configuration.METAPATHOLOGY_REPORT",)


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


def test_explicit_invalid_report_and_configuration_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="--report-text or --report-json"):
        _resolve(report_destination=["capture.yaml"])
    with pytest.raises(ValueError, match="unknown color mode"):
        _resolve(report_color="sometimes")
    with pytest.raises(TypeError, match="configuration fields must be bool or None"):
        _resolve(capture=CaptureConfig(path_hooks=cast("bool", 1)))
    with pytest.raises(TypeError, match=r"capture\.detailed must be bool, DetailedCaptureConfig, or None"):
        _resolve(capture=CaptureConfig(detailed=cast("DetailedCaptureConfig", object())))


def test_report_destinations_are_reduced_separately(tmp_path: Path) -> None:
    first = tmp_path / "report.json"
    second = tmp_path / "report.txt"
    assert normalize_report_destination(first) == [str(first)]
    assert normalize_report_destination([first, second]) == [str(first), str(second)]
    assert normalize_report_destination(None) == []
    with pytest.raises(TypeError, match="str, not bytes"):
        normalize_report_destination(cast(PathLike[str], b"report.json"))
