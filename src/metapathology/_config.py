"""Pure installation-configuration resolution.

This module turns API values, environment values, and active report settings
into one immutable request before the monitor mutates process-global import
state. It deliberately does not import the monitor or reporting pipeline.
"""

import os

from metapathology._records import _Record

TYPE_CHECKING = False

if TYPE_CHECKING:
    from os import PathLike
    from typing import Literal

    _ColorMode = Literal["auto", "always", "never"]
    _ReportFormat = Literal["text", "json"]


REPORT_DESTINATION_ENV = "METAPATHOLOGY_REPORT"
REPORT_FORMAT_ENV = "METAPATHOLOGY_REPORT_FORMAT"
REPORT_COLOR_ENV = "METAPATHOLOGY_COLOR"
MONITOR_PATH_HOOKS_ENV = "METAPATHOLOGY_MONITOR_PATH_HOOKS"
MONITOR_IMPORTER_CACHE_ENV = "METAPATHOLOGY_MONITOR_IMPORTER_CACHE"
MONITOR_SYS_PATH_ENV = "METAPATHOLOGY_MONITOR_SYS_PATH"
DEEP_ENV = "METAPATHOLOGY_DEEP"
DEEP_PATH_HOOKS_ENV = "METAPATHOLOGY_DEEP_PATH_HOOKS"
DEEP_PATH_ENTRY_FINDERS_ENV = "METAPATHOLOGY_DEEP_PATH_ENTRY_FINDERS"
DEEP_LOADERS_ENV = "METAPATHOLOGY_DEEP_LOADERS"
DEEP_IMPORT_OUTCOMES_ENV = "METAPATHOLOGY_DEEP_IMPORT_OUTCOMES"

_TRUE_ENV_VALUES = frozenset(("1", "true", "yes", "on"))
_FALSE_ENV_VALUES = frozenset(("0", "false", "no", "off"))
_REPORT_FORMATS = frozenset(("json", "text"))
_COLOR_MODES = frozenset(("always", "auto", "never"))


class InstallRequest(_Record):
    """Fully resolved configuration consumed by one monitor installation."""

    report_at_exit: bool
    report_destination: str | None
    report_format: "_ReportFormat"
    report_color: "_ColorMode"
    monitor_path_hooks: bool
    monitor_importer_cache: bool
    monitor_sys_path: bool
    deep_path_hooks: bool
    deep_path_entry_finders: bool
    deep_loaders: bool
    deep_import_outcomes: bool
    issues: tuple[str, ...]


def normalize_report_destination(destination: "str | PathLike[str] | None") -> str | None:
    """Reduce an explicit destination before any lifecycle lock is acquired."""
    if destination is None:
        return None
    path = os.fspath(destination)
    if not isinstance(path, str):
        raise TypeError("report destinations must resolve to str, not bytes")
    return path


def resolve_install_request(
    *,
    report_at_exit: bool,
    report_destination: str | None,
    report_destination_explicit: bool,
    report_format: "_ReportFormat | str | None",
    report_color: "_ColorMode | str | None",
    monitor_path_hooks: bool | None,
    monitor_importer_cache: bool | None,
    monitor_sys_path: bool | None,
    deep: bool | None,
    deep_path_hooks: bool | None,
    deep_path_entry_finders: bool | None,
    deep_loaders: bool | None,
    deep_import_outcomes: bool | None,
    use_environment: bool,
    configure_report: bool,
    current_report_destination: str | None,
    current_report_format: "_ReportFormat",
    current_report_color: "_ColorMode",
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
    destination, format, color = _resolve_report_options(
        destination=report_destination,
        destination_explicit=report_destination_explicit,
        format=report_format,
        color=report_color,
        use_environment=use_environment,
        configure=configure_report,
        current_destination=current_report_destination,
        current_format=current_report_format,
        current_color=current_report_color,
        issues=issues,
    )
    return InstallRequest(
        report_at_exit=report_at_exit,
        report_destination=destination,
        report_format=format,
        report_color=color,
        monitor_path_hooks=resolved_path_hooks,
        monitor_importer_cache=resolved_importer_cache,
        monitor_sys_path=resolved_sys_path,
        deep_path_hooks=resolved_deep_path_hooks,
        deep_path_entry_finders=resolved_deep_path_entry_finders,
        deep_loaders=resolved_deep_loaders,
        deep_import_outcomes=resolved_deep_import_outcomes,
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


def _resolve_report_options(
    *,
    destination: str | None,
    destination_explicit: bool,
    format: "_ReportFormat | str | None",
    color: "_ColorMode | str | None",
    use_environment: bool,
    configure: bool,
    current_destination: str | None,
    current_format: "_ReportFormat",
    current_color: "_ColorMode",
    issues: list[str],
) -> "tuple[str | None, _ReportFormat, _ColorMode]":
    """Resolve automatic report settings while preserving repeat-install semantics."""
    if not configure:
        return current_destination, current_format, current_color
    if destination_explicit:
        resolved_destination = destination
    elif use_environment:
        resolved_destination = os.environ.get(REPORT_DESTINATION_ENV) or None
    else:
        resolved_destination = current_destination

    resolved_format: str | None = format
    format_from_environment = False
    if resolved_format is None and use_environment:
        resolved_format = os.environ.get(REPORT_FORMAT_ENV) or None
        format_from_environment = resolved_format is not None
    if resolved_format is None:
        resolved_format = "json" if resolved_destination is not None else "text"
    try:
        validated_format = validate_report_format(resolved_format)
    except ValueError:
        if not format_from_environment:
            raise
        validated_format = "json" if resolved_destination is not None else "text"
        issues.append("report_configuration")

    resolved_color: str | None = color
    color_from_environment = False
    if resolved_color is None and use_environment:
        resolved_color = os.environ.get(REPORT_COLOR_ENV) or None
        color_from_environment = resolved_color is not None
    if resolved_color is None:
        resolved_color = "auto" if use_environment else current_color
    try:
        validated_color = validate_report_color(resolved_color)
    except ValueError:
        if not color_from_environment:
            raise
        validated_color = "auto"
        issues.append(f"environment_configuration.{REPORT_COLOR_ENV}")
    return resolved_destination, validated_format, validated_color
