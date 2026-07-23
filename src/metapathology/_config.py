"""Pure installation-configuration resolution.

This module turns API values, environment values, and active report settings
into one immutable request before the monitor mutates process-global import
state. It deliberately does not import the monitor or reporting pipeline.
"""

import os

from metapathology._record import _Record

TYPE_CHECKING = False

if TYPE_CHECKING:
    from collections.abc import Sequence
    from os import PathLike
    from typing import Literal

    _ColorMode = Literal["auto", "always", "never"]
    _ReportFormat = Literal["text", "json"]


REPORT_DESTINATION_ENV = "METAPATHOLOGY_REPORT"
REPORT_COLOR_ENV = "METAPATHOLOGY_COLOR"
IMPORT_AUDIT_ENV = "METAPATHOLOGY_IMPORT_AUDIT"
META_PATH_ENV = "METAPATHOLOGY_META_PATH"
FINDER_ATTRIBUTION_ENV = "METAPATHOLOGY_FINDER_ATTRIBUTION"
PATH_HOOKS_ENV = "METAPATHOLOGY_PATH_HOOKS"
IMPORTER_CACHE_ENV = "METAPATHOLOGY_IMPORTER_CACHE"
SYS_PATH_ENV = "METAPATHOLOGY_SYS_PATH"
DETAILED_CAPTURE_ENV = "METAPATHOLOGY_DETAILED_CAPTURE"
CAPTURE_PATH_HOOK_CALLS_ENV = "METAPATHOLOGY_CAPTURE_PATH_HOOK_CALLS"
CAPTURE_PATH_ENTRY_FINDER_CALLS_ENV = "METAPATHOLOGY_CAPTURE_PATH_ENTRY_FINDER_CALLS"
CAPTURE_LOADER_CALLS_ENV = "METAPATHOLOGY_CAPTURE_LOADER_CALLS"
CAPTURE_IMPORT_RESULTS_ENV = "METAPATHOLOGY_CAPTURE_IMPORT_RESULTS"
CAPTURE_IMPORT_CALLS_ENV = "METAPATHOLOGY_CAPTURE_IMPORT_CALLS"
CHECKS_ENV = "METAPATHOLOGY_CHECKS"
STANDARD_PATH_CHECK_ENV = "METAPATHOLOGY_STANDARD_PATH_CHECK"
DISPLACED_FINDER_CHECK_ENV = "METAPATHOLOGY_DISPLACED_FINDER_CHECK"

_TRUE_ENV_VALUES = frozenset(("1", "true", "yes", "on"))
_FALSE_ENV_VALUES = frozenset(("0", "false", "no", "off"))
_REPORT_FORMATS = frozenset(("json", "text"))
_REPORT_FORMAT_BY_EXTENSION = {".json": "json", ".text": "text", ".txt": "text"}
_COLOR_MODES = frozenset(("always", "auto", "never"))


class ReportTarget(_Record):
    """One validated automatic-report projection."""

    destination: str | None
    format: "_ReportFormat"
    color: "_ColorMode"


class _ConfigRecord(_Record):
    """Value-comparable base for immutable public configuration records."""

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and all(
            getattr(self, field) == getattr(other, field) for field in self._fields
        )

    def __hash__(self) -> int:
        return hash((type(self), *(getattr(self, field) for field in self._fields)))


class DetailedCaptureConfig(_ConfigRecord):
    """Fine-grained settings for the slower, more intrusive capture mechanisms."""

    enabled: bool | None = None
    path_hooks: bool | None = None
    path_entry_finders: bool | None = None
    loaders: bool | None = None
    import_results: bool | None = None
    import_calls: bool | None = None


class CaptureConfig(_ConfigRecord):
    """Capture settings for one installation.

    ``detailed=True`` enables every detailed mechanism without requiring a
    nested configuration object. Pass :class:`DetailedCaptureConfig` only to
    choose individual detailed mechanisms.
    """

    import_audit: bool | None = None
    meta_path: bool | None = None
    finder_attribution: bool | None = None
    path_hooks: bool | None = None
    importer_cache: bool | None = None
    sys_path: bool | None = None
    detailed: bool | DetailedCaptureConfig | None = None


class AnalysisConfig(_ConfigRecord):
    """Settings for current-state checks performed while building a report."""

    checks: bool | None = None
    standard_path_check: bool | None = None
    displaced_finder_check: bool | None = None


class ResolvedDetailedCaptureConfig(_ConfigRecord):
    """Resolved settings for the detailed capture mechanisms."""

    path_hooks: bool
    path_entry_finders: bool
    loaders: bool
    import_results: bool
    import_calls: bool


class ResolvedCaptureConfig(_ConfigRecord):
    """Concrete capture settings fixed for one installation."""

    import_audit: bool
    meta_path: bool
    finder_attribution: bool
    path_hooks: bool
    importer_cache: bool
    sys_path: bool
    detailed: ResolvedDetailedCaptureConfig


class ResolvedAnalysisConfig(_ConfigRecord):
    """Concrete current-state check policy fixed for one installation."""

    standard_path_check: bool
    displaced_finder_check: bool


class InstallRequest(_Record):
    """Fully resolved configuration consumed by one monitor installation."""

    report_at_exit: bool
    report_targets: tuple[ReportTarget, ...]
    capture: ResolvedCaptureConfig
    analysis: ResolvedAnalysisConfig
    issues: tuple[str, ...]


class MonitoringRequest(_Record):
    """Resolved capture mechanisms consumed by Monitor alone."""

    import_audit: bool
    meta_path: bool
    finder_attribution: bool
    path_hooks: bool
    importer_cache: bool
    sys_path: bool
    capture_path_hook_calls: bool
    capture_path_entry_finder_calls: bool
    capture_loader_calls: bool
    capture_import_results: bool
    capture_import_calls: bool
    issues: tuple[str, ...]


class _UnresolvedReportingOptions(_Record):
    """Normalized output options awaiting environment and lifecycle resolution."""

    report_at_exit: bool
    destinations: list[str]
    text_destinations: list[str]
    json_destinations: list[str]
    color: "_ColorMode | str | None"


def monitoring_request(request: InstallRequest) -> MonitoringRequest:
    """Project a process installation request onto Monitor-owned options."""
    return MonitoringRequest(
        import_audit=request.capture.import_audit,
        meta_path=request.capture.meta_path,
        finder_attribution=request.capture.finder_attribution,
        path_hooks=request.capture.path_hooks,
        importer_cache=request.capture.importer_cache,
        sys_path=request.capture.sys_path,
        capture_path_hook_calls=request.capture.detailed.path_hooks,
        capture_path_entry_finder_calls=request.capture.detailed.path_entry_finders,
        capture_loader_calls=request.capture.detailed.loaders,
        capture_import_results=request.capture.detailed.import_results,
        capture_import_calls=request.capture.detailed.import_calls,
        issues=request.issues,
    )


def normalize_report_destination(
    destination: "str | bytes | PathLike[str] | Sequence[str | bytes | PathLike[str]] | None",
) -> list[str]:
    """Reduce explicit destinations before any lifecycle lock is acquired."""
    if destination is None:
        return []
    destinations = (destination,) if isinstance(destination, (str, bytes, os.PathLike)) else destination
    normalized: list[str] = []
    for item in destinations:
        path = os.fspath(item)
        if not isinstance(path, str):
            raise TypeError("report destinations must resolve to str, not bytes")
        normalized.append(path)
    return normalized


def infer_report_format(destination: str) -> "_ReportFormat":
    """Infer a report format from a destination or raise with forced-flag guidance."""
    if destination == "-":
        return "text"
    extension = os.path.splitext(destination)[1].lower()
    try:
        return _REPORT_FORMAT_BY_EXTENSION[extension]  # type: ignore[return-value]
    except KeyError:
        raise ValueError(
            f"cannot infer report format from destination {destination!r}; "
            "use --report-text or --report-json to force the format"
        ) from None


def resolve_install_request(
    *,
    reporting: _UnresolvedReportingOptions,
    capture: CaptureConfig,
    analysis: AnalysisConfig,
    use_environment: bool,
    configure_report: bool,
    current_report_targets: tuple[ReportTarget, ...],
) -> InstallRequest:
    """Resolve one complete request without mutating monitor or import state."""
    issues: list[str] = []
    _validate_configs(capture, analysis)
    checks_enabled = _resolve_bool(analysis.checks, CHECKS_ENV, False, issues)
    checks_configured = analysis.checks is not None or os.environ.get(CHECKS_ENV) is not None
    resolved_standard_path_check = _resolve_bool(
        analysis.standard_path_check,
        STANDARD_PATH_CHECK_ENV,
        checks_enabled if checks_configured else True,
        issues,
    )
    resolved_displaced_finder_check = _resolve_bool(
        analysis.displaced_finder_check, DISPLACED_FINDER_CHECK_ENV, checks_enabled, issues
    )
    detailed = (
        capture.detailed
        if isinstance(capture.detailed, DetailedCaptureConfig)
        else DetailedCaptureConfig(enabled=capture.detailed)
    )
    detailed_enabled = _resolve_bool(detailed.enabled, DETAILED_CAPTURE_ENV, False, issues)
    resolved_import_audit = _resolve_bool(capture.import_audit, IMPORT_AUDIT_ENV, True, issues)
    resolved_meta_path = _resolve_bool(capture.meta_path, META_PATH_ENV, True, issues)
    resolved_finder_attribution = _resolve_bool(capture.finder_attribution, FINDER_ATTRIBUTION_ENV, True, issues)
    resolved_path_hooks = _resolve_bool(capture.path_hooks, PATH_HOOKS_ENV, True, issues)
    resolved_importer_cache = _resolve_bool(capture.importer_cache, IMPORTER_CACHE_ENV, True, issues)
    resolved_sys_path = _resolve_bool(capture.sys_path, SYS_PATH_ENV, detailed_enabled, issues)
    resolved_path_hook_calls = _resolve_bool(detailed.path_hooks, CAPTURE_PATH_HOOK_CALLS_ENV, detailed_enabled, issues)
    resolved_path_entry_finder_calls = _resolve_bool(
        detailed.path_entry_finders,
        CAPTURE_PATH_ENTRY_FINDER_CALLS_ENV,
        detailed_enabled or resolved_displaced_finder_check,
        issues,
    )
    resolved_loader_calls = _resolve_bool(detailed.loaders, CAPTURE_LOADER_CALLS_ENV, detailed_enabled, issues)
    resolved_import_results = _resolve_bool(
        detailed.import_results,
        CAPTURE_IMPORT_RESULTS_ENV,
        detailed_enabled,
        issues,
    )
    resolved_import_calls = _resolve_bool(
        detailed.import_calls,
        CAPTURE_IMPORT_CALLS_ENV,
        detailed_enabled,
        issues,
    )
    targets = _resolve_report_targets(
        destinations=reporting.destinations,
        text_destinations=reporting.text_destinations,
        json_destinations=reporting.json_destinations,
        color=reporting.color,
        use_environment=use_environment,
        configure=configure_report,
        current_targets=current_report_targets,
        issues=issues,
    )
    return InstallRequest(
        report_at_exit=reporting.report_at_exit,
        report_targets=targets,
        capture=ResolvedCaptureConfig(
            import_audit=resolved_import_audit,
            meta_path=resolved_meta_path,
            finder_attribution=resolved_finder_attribution,
            path_hooks=resolved_path_hooks,
            importer_cache=resolved_importer_cache,
            sys_path=resolved_sys_path,
            detailed=ResolvedDetailedCaptureConfig(
                path_hooks=resolved_path_hook_calls,
                path_entry_finders=resolved_path_entry_finder_calls,
                loaders=resolved_loader_calls,
                import_results=resolved_import_results,
                import_calls=resolved_import_calls,
            ),
        ),
        analysis=ResolvedAnalysisConfig(
            standard_path_check=resolved_standard_path_check,
            displaced_finder_check=resolved_displaced_finder_check,
        ),
        issues=tuple(issues),
    )


def _validate_configs(capture: CaptureConfig, analysis: AnalysisConfig) -> None:
    """Reject malformed public configuration before installation mutates state."""
    if not isinstance(capture, CaptureConfig):
        raise TypeError("capture must be a CaptureConfig")
    if (
        capture.detailed is not None
        and type(capture.detailed) is not bool
        and not isinstance(capture.detailed, DetailedCaptureConfig)
    ):
        raise TypeError("capture.detailed must be bool, DetailedCaptureConfig, or None")
    if not isinstance(analysis, AnalysisConfig):
        raise TypeError("analysis must be an AnalysisConfig")
    detailed_values = (
        ()
        if not isinstance(capture.detailed, DetailedCaptureConfig)
        else tuple(getattr(capture.detailed, field) for field in capture.detailed._fields)
    )
    values = (
        *(getattr(capture, field) for field in capture._fields if field != "detailed"),
        *detailed_values,
        *(getattr(analysis, field) for field in analysis._fields),
    )
    if any(value is not None and type(value) is not bool for value in values):
        raise TypeError("configuration fields must be bool or None")


def validate_report_format(format: str) -> "_ReportFormat":
    """Validate and narrow a public report-format value."""
    if format not in _REPORT_FORMATS:
        choices = ", ".join(sorted(_REPORT_FORMATS))
        raise ValueError(f"unknown report format {format!r}; expected one of: {choices}")
    return format  # type: ignore[return-value]


def validate_report_color(color: str) -> "_ColorMode":
    """Validate and narrow a public color-mode value."""
    if color not in _COLOR_MODES:
        choices = ", ".join(sorted(_COLOR_MODES))
        raise ValueError(f"unknown color mode {color!r}; expected one of: {choices}")
    return color  # type: ignore[return-value]


def _resolve_bool(
    explicit: bool | None,
    environment_name: str,
    default: bool,
    issues: list[str],
) -> bool:
    """Resolve explicit/environment/default precedence for one switch."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(environment_name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_ENV_VALUES:
        return True
    if normalized in _FALSE_ENV_VALUES:
        return False
    issues.append(f"environment_configuration.{environment_name}")
    return default


def _resolve_report_targets(
    *,
    destinations: list[str],
    text_destinations: list[str],
    json_destinations: list[str],
    color: "_ColorMode | str | None",
    use_environment: bool,
    configure: bool,
    current_targets: tuple[ReportTarget, ...],
    issues: list[str],
) -> tuple[ReportTarget, ...]:
    """Resolve automatic report settings while preserving repeat-install semantics."""
    if not configure:
        return current_targets

    validated_color = _resolve_report_color(color, use_environment, current_targets, issues)

    explicit_destinations = bool(destinations or text_destinations or json_destinations)
    inferred_destinations = destinations
    from_environment = False
    if not explicit_destinations and use_environment:
        raw_destinations = os.environ.get(REPORT_DESTINATION_ENV)
        if raw_destinations:
            inferred_destinations = [item for item in raw_destinations.split(os.pathsep) if item]
            from_environment = True

    resolved: list[ReportTarget] = []
    if explicit_destinations or inferred_destinations:
        for destination in inferred_destinations:
            try:
                format = infer_report_format(destination)
            except ValueError:
                if not from_environment:
                    raise
                format = "text"
                issue = f"environment_configuration.{REPORT_DESTINATION_ENV}"
                if issue not in issues:
                    issues.append(issue)
            resolved.append(ReportTarget(_stderr_destination(destination), format, validated_color))
        resolved.extend(
            ReportTarget(_stderr_destination(destination), "text", validated_color) for destination in text_destinations
        )
        resolved.extend(
            ReportTarget(_stderr_destination(destination), "json", validated_color) for destination in json_destinations
        )
    elif use_environment:
        resolved.append(ReportTarget(None, "text", validated_color))
    else:
        resolved.extend(ReportTarget(target.destination, target.format, validated_color) for target in current_targets)

    return _deduplicate_report_targets(resolved)


def _resolve_report_color(
    color: "_ColorMode | str | None",
    use_environment: bool,
    current_targets: tuple[ReportTarget, ...],
    issues: list[str],
) -> "_ColorMode":
    """Resolve one color mode, degrading only an ambient invalid value."""
    resolved_color: str | None = color
    color_from_environment = False
    if resolved_color is None and use_environment:
        resolved_color = os.environ.get(REPORT_COLOR_ENV) or None
        color_from_environment = resolved_color is not None
    if resolved_color is None:
        resolved_color = "auto" if use_environment or not current_targets else current_targets[0].color
    try:
        return validate_report_color(resolved_color)
    except ValueError:
        if not color_from_environment:
            raise
        issues.append(f"environment_configuration.{REPORT_COLOR_ENV}")
        return "auto"


def _deduplicate_report_targets(targets: list[ReportTarget]) -> tuple[ReportTarget, ...]:
    """Preserve target order while removing identical output projections."""
    deduplicated: list[ReportTarget] = []
    seen: set[tuple[str | None, str, str]] = set()
    for target in targets:
        key = (target.destination, target.format, target.color)
        if key not in seen:
            seen.add(key)
            deduplicated.append(target)
    return tuple(deduplicated)


def _stderr_destination(destination: str) -> str | None:
    """Normalize the public stderr sentinel to the report writer representation."""
    return None if destination == "-" else destination
