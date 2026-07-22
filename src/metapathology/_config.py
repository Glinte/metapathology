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
MONITOR_PATH_HOOKS_ENV = "METAPATHOLOGY_MONITOR_PATH_HOOKS"
MONITOR_IMPORTER_CACHE_ENV = "METAPATHOLOGY_MONITOR_IMPORTER_CACHE"
MONITOR_SYS_PATH_ENV = "METAPATHOLOGY_MONITOR_SYS_PATH"
DEEP_ENV = "METAPATHOLOGY_DEEP"
DEEP_PATH_HOOKS_ENV = "METAPATHOLOGY_DEEP_PATH_HOOKS"
DEEP_PATH_ENTRY_FINDERS_ENV = "METAPATHOLOGY_DEEP_PATH_ENTRY_FINDERS"
DEEP_LOADERS_ENV = "METAPATHOLOGY_DEEP_LOADERS"
DEEP_IMPORT_OUTCOMES_ENV = "METAPATHOLOGY_DEEP_IMPORT_OUTCOMES"
DEEP_IMPORT_CALLS_ENV = "METAPATHOLOGY_DEEP_IMPORT_CALLS"
SPECULATIVE_REPLAY_ENV = "METAPATHOLOGY_SPECULATIVE_REPLAY"

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


class InstallRequest(_Record):
    """Fully resolved configuration consumed by one monitor installation."""

    report_at_exit: bool
    report_targets: tuple[ReportTarget, ...]
    monitor_path_hooks: bool
    monitor_importer_cache: bool
    monitor_sys_path: bool
    deep_path_hooks: bool
    deep_path_entry_finders: bool
    deep_loaders: bool
    deep_import_outcomes: bool
    deep_import_calls: bool
    speculative_replay: bool
    issues: tuple[str, ...]


class MonitoringRequest(_Record):
    """Resolved capture mechanisms consumed by Monitor alone."""

    monitor_path_hooks: bool
    monitor_importer_cache: bool
    monitor_sys_path: bool
    deep_path_hooks: bool
    deep_path_entry_finders: bool
    deep_loaders: bool
    deep_import_outcomes: bool
    deep_import_calls: bool
    speculative_replay: bool
    issues: tuple[str, ...]


def monitoring_request(request: InstallRequest) -> MonitoringRequest:
    """Project a process installation request onto Monitor-owned options."""
    return MonitoringRequest(
        monitor_path_hooks=request.monitor_path_hooks,
        monitor_importer_cache=request.monitor_importer_cache,
        monitor_sys_path=request.monitor_sys_path,
        deep_path_hooks=request.deep_path_hooks,
        deep_path_entry_finders=request.deep_path_entry_finders,
        deep_loaders=request.deep_loaders,
        deep_import_outcomes=request.deep_import_outcomes,
        deep_import_calls=request.deep_import_calls,
        speculative_replay=request.speculative_replay,
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
    report_at_exit: bool,
    report_destination: list[str],
    report_text: list[str],
    report_json: list[str],
    report_color: "_ColorMode | str | None",
    monitor_path_hooks: bool | None,
    monitor_importer_cache: bool | None,
    monitor_sys_path: bool | None,
    deep: bool | None,
    deep_path_hooks: bool | None,
    deep_path_entry_finders: bool | None,
    deep_loaders: bool | None,
    deep_import_outcomes: bool | None,
    deep_import_calls: bool | None,
    speculative_replay: bool | None,
    use_environment: bool,
    configure_report: bool,
    current_report_targets: tuple[ReportTarget, ...],
) -> InstallRequest:
    """Resolve one complete request without mutating monitor or import state."""
    issues: list[str] = []
    deep_enabled = _resolve_bool(deep, DEEP_ENV, False, issues)
    resolved_path_hooks = _resolve_bool(monitor_path_hooks, MONITOR_PATH_HOOKS_ENV, True, issues)
    resolved_importer_cache = _resolve_bool(
        monitor_importer_cache,
        MONITOR_IMPORTER_CACHE_ENV,
        True,
        issues,
    )
    resolved_sys_path = _resolve_bool(monitor_sys_path, MONITOR_SYS_PATH_ENV, deep_enabled, issues)
    resolved_deep_path_hooks = _resolve_bool(deep_path_hooks, DEEP_PATH_HOOKS_ENV, deep_enabled, issues)
    resolved_deep_path_entry_finders = _resolve_bool(
        deep_path_entry_finders,
        DEEP_PATH_ENTRY_FINDERS_ENV,
        deep_enabled,
        issues,
    )
    resolved_deep_loaders = _resolve_bool(deep_loaders, DEEP_LOADERS_ENV, deep_enabled, issues)
    resolved_deep_import_outcomes = _resolve_bool(
        deep_import_outcomes,
        DEEP_IMPORT_OUTCOMES_ENV,
        deep_enabled,
        issues,
    )
    resolved_deep_import_calls = _resolve_bool(
        deep_import_calls,
        DEEP_IMPORT_CALLS_ENV,
        deep_enabled,
        issues,
    )
    # Independent of --deep: deep capture delegates along paths the target
    # actually took, while speculative replay invokes a path it did not.
    resolved_speculative_replay = _resolve_bool(
        speculative_replay,
        SPECULATIVE_REPLAY_ENV,
        False,
        issues,
    )
    targets = _resolve_report_targets(
        destinations=report_destination,
        text_destinations=report_text,
        json_destinations=report_json,
        color=report_color,
        use_environment=use_environment,
        configure=configure_report,
        current_targets=current_report_targets,
        issues=issues,
    )
    return InstallRequest(
        report_at_exit=report_at_exit,
        report_targets=targets,
        monitor_path_hooks=resolved_path_hooks,
        monitor_importer_cache=resolved_importer_cache,
        monitor_sys_path=resolved_sys_path,
        deep_path_hooks=resolved_deep_path_hooks,
        deep_path_entry_finders=resolved_deep_path_entry_finders,
        deep_loaders=resolved_deep_loaders,
        deep_import_outcomes=resolved_deep_import_outcomes,
        deep_import_calls=resolved_deep_import_calls,
        speculative_replay=resolved_speculative_replay,
        issues=tuple(issues),
    )


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
