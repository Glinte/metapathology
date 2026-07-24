"""Pure installation-configuration resolution."""

import os
import string
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class _BooleanSetting:
    """One independently configurable boolean contract."""

    name: str
    environment: str
    default: bool
    configure: Callable[[bool | None], tuple[CaptureConfig, AnalysisConfig, bool | None]]
    resolved: Callable[[InstallRequest], bool]


def _capture(field: str, value: bool | None) -> CaptureConfig:
    values: dict[str, bool | None] = {field: value}
    return CaptureConfig(**values)  # type: ignore[arg-type]


def _detailed(field: str, value: bool | None) -> CaptureConfig:
    values: dict[str, bool | None] = {field: value}
    return CaptureConfig(detailed=DetailedCaptureConfig(**values))  # type: ignore[arg-type]


def _analysis(field: str, value: bool | None) -> AnalysisConfig:
    values: dict[str, bool | None] = {field: value}
    return AnalysisConfig(**values)  # type: ignore[arg-type]


def _request(
    *,
    capture: CaptureConfig = CaptureConfig(),
    analysis: AnalysisConfig = AnalysisConfig(),
    unsafe: bool | None = None,
) -> tuple[CaptureConfig, AnalysisConfig, bool | None]:
    return capture, analysis, unsafe


_BOOLEAN_SETTINGS = (
    *(
        _BooleanSetting(
            name,
            environment,
            default,
            lambda value, field=name: _request(capture=_capture(field, value)),
            lambda request, field=name: cast(bool, getattr(request.capture, field)),
        )
        for name, environment, default in (
            ("import_audit", "METAPATHOLOGY_IMPORT_AUDIT", True),
            ("meta_path", "METAPATHOLOGY_META_PATH", True),
            ("finder_attribution", "METAPATHOLOGY_FINDER_ATTRIBUTION", True),
            ("path_hooks", "METAPATHOLOGY_PATH_HOOKS", True),
            ("importer_cache", "METAPATHOLOGY_IMPORTER_CACHE", True),
            ("sys_path", "METAPATHOLOGY_SYS_PATH", False),
        )
    ),
    *(
        _BooleanSetting(
            f"detailed.{name}",
            environment,
            False,
            lambda value, field=name: _request(capture=_detailed(field, value)),
            lambda request, field=name: cast(bool, getattr(request.capture.detailed, field)),
        )
        for name, environment in (
            ("path_hooks", "METAPATHOLOGY_CAPTURE_PATH_HOOK_CALLS"),
            ("path_entry_finders", "METAPATHOLOGY_CAPTURE_PATH_ENTRY_FINDER_CALLS"),
            ("loaders", "METAPATHOLOGY_CAPTURE_LOADER_CALLS"),
            ("import_results", "METAPATHOLOGY_CAPTURE_IMPORT_RESULTS"),
            ("import_calls", "METAPATHOLOGY_CAPTURE_IMPORT_CALLS"),
        )
    ),
    *(
        _BooleanSetting(
            f"analysis.{name}",
            environment,
            default,
            lambda value, field=name: _request(analysis=_analysis(field, value)),
            lambda request, field=name: cast(bool, getattr(request.analysis, field)),
        )
        for name, environment, default in (
            ("standard_path_check", "METAPATHOLOGY_STANDARD_PATH_CHECK", True),
            ("displaced_finder_check", "METAPATHOLOGY_DISPLACED_FINDER_CHECK", False),
        )
    ),
    _BooleanSetting(
        "unsafe_explore_import_branches",
        "METAPATHOLOGY_UNSAFE_EXPLORE_IMPORT_BRANCHES",
        False,
        lambda value: _request(unsafe=value),
        lambda request: request.unsafe_explore_import_branches,
    ),
)
_BOOLEAN_FIELDS = st.sampled_from(_BOOLEAN_SETTINGS)
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
    unsafe_explore_import_branches: bool | None = None,
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
        unsafe_explore_import_branches=unsafe_explore_import_branches,
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


def test_unsafe_exploration_is_independent_and_auto_enables_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _resolve(use_environment=False).unsafe_explore_import_branches is False
    request = _resolve(unsafe_explore_import_branches=True, use_environment=False)
    assert request.unsafe_explore_import_branches is True
    assert request.capture.finder_attribution is True
    assert request.capture.path_hooks is True
    assert request.capture.importer_cache is True
    assert request.capture.detailed.path_hooks is True
    assert request.capture.detailed.path_entry_finders is True
    assert request.capture.detailed.import_results is True
    assert request.capture.detailed.loaders is False

    monkeypatch.setenv("METAPATHOLOGY_UNSAFE_EXPLORE_IMPORT_BRANCHES", "on")
    assert _resolve().unsafe_explore_import_branches is True
    assert _resolve(unsafe_explore_import_branches=False).unsafe_explore_import_branches is False

    with pytest.raises(TypeError, match="unsafe_explore_import_branches"):
        _resolve(unsafe_explore_import_branches=cast(bool, 1))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(field=_BOOLEAN_FIELDS, explicit=st.one_of(st.none(), st.booleans()), raw=_ENVIRONMENT_BOOLEAN)
def test_boolean_configuration_obeys_explicit_environment_default_precedence(
    monkeypatch: pytest.MonkeyPatch,
    field: _BooleanSetting,
    explicit: bool | None,
    raw: str | None,
) -> None:
    for name in tuple(os.environ):
        if name.startswith("METAPATHOLOGY_"):
            monkeypatch.delenv(name, raising=False)
    if raw is not None:
        monkeypatch.setenv(field.environment, raw)

    capture, analysis, unsafe = field.configure(explicit)
    request = _resolve(
        capture=capture,
        analysis=analysis,
        unsafe_explore_import_branches=unsafe,
    )
    resolved = field.resolved(request)
    normalized = None if raw is None else raw.strip().lower()
    environment_value = normalized in {"1", "true", "yes", "on"}
    valid_environment = normalized in {"1", "true", "yes", "on", "0", "false", "no", "off"}
    expected = explicit if explicit is not None else environment_value if valid_environment else field.default

    assert resolved is expected
    issue = f"environment_configuration.{field.environment}"
    assert (issue in request.issues) is (explicit is None and raw is not None and not valid_environment)


def test_boolean_setting_table_is_exhaustive() -> None:
    assert {setting.name for setting in _BOOLEAN_SETTINGS} == {
        "import_audit",
        "meta_path",
        "finder_attribution",
        "path_hooks",
        "importer_cache",
        "sys_path",
        "detailed.path_hooks",
        "detailed.path_entry_finders",
        "detailed.loaders",
        "detailed.import_results",
        "detailed.import_calls",
        "analysis.standard_path_check",
        "analysis.displaced_finder_check",
        "unsafe_explore_import_branches",
    }
    assert len({setting.environment for setting in _BOOLEAN_SETTINGS}) == len(_BOOLEAN_SETTINGS)


@pytest.mark.parametrize(
    ("field", "environment"),
    [
        ("path_hooks", "METAPATHOLOGY_CAPTURE_PATH_HOOK_CALLS"),
        ("path_entry_finders", "METAPATHOLOGY_CAPTURE_PATH_ENTRY_FINDER_CALLS"),
        ("loaders", "METAPATHOLOGY_CAPTURE_LOADER_CALLS"),
        ("import_results", "METAPATHOLOGY_CAPTURE_IMPORT_RESULTS"),
        ("import_calls", "METAPATHOLOGY_CAPTURE_IMPORT_CALLS"),
    ],
)
def test_detailed_leaf_values_override_the_group(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    environment: str,
) -> None:
    monkeypatch.setenv("METAPATHOLOGY_DETAILED_CAPTURE", "on")
    monkeypatch.setenv(environment, "off")
    from_environment = _resolve()
    assert getattr(from_environment.capture.detailed, field) is False

    monkeypatch.delenv(environment)
    from_group = _resolve()
    assert getattr(from_group.capture.detailed, field) is True

    explicit_leaf = _resolve(capture=_detailed(field, False))
    assert getattr(explicit_leaf.capture.detailed, field) is False


@pytest.mark.parametrize(
    ("field", "environment"),
    [
        ("standard_path_check", "METAPATHOLOGY_STANDARD_PATH_CHECK"),
        ("displaced_finder_check", "METAPATHOLOGY_DISPLACED_FINDER_CHECK"),
    ],
)
def test_analysis_leaf_values_override_the_group(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    environment: str,
) -> None:
    monkeypatch.setenv("METAPATHOLOGY_CHECKS", "on")
    monkeypatch.setenv(environment, "off")
    from_environment = _resolve()
    assert getattr(from_environment.analysis, field) is False

    monkeypatch.delenv(environment)
    from_group = _resolve()
    assert getattr(from_group.analysis, field) is True

    explicit_leaf = _resolve(analysis=_analysis(field, False))
    assert getattr(explicit_leaf.analysis, field) is False


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
