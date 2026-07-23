"""Conservative reduction of module specs to plain diagnostic data."""

from importlib.machinery import ModuleSpec

from metapathology._records import ModuleSpecSnapshot, ObjectIdentity, type_name

TYPE_CHECKING = False

if TYPE_CHECKING:
    from metapathology._records import LocationsState


def _safe_value(value: object) -> str | ObjectIdentity | None:
    if value is None or type(value) is str:
        return value
    return ObjectIdentity.of(value)


def _safe_location(value: object) -> str | ObjectIdentity:
    if type(value) is str:
        return value
    return ObjectIdentity.of(value)


def _spec_namespace(spec: object, unavailable: list[str]) -> dict[str, object]:
    try:
        namespace = object.__getattribute__(spec, "__dict__")
    except Exception as exc:
        unavailable.append(f"__dict__:{type_name(exc)}")
        unavailable.append("__dict__:unavailable")
        return {}
    if type(namespace) is dict:
        return namespace
    unavailable.append("__dict__:unavailable")
    return {}


def _cached_path(spec: object, origin: object, cached: object, missing: object, unavailable: list[str]) -> object:
    if cached is not missing and cached is not None:
        return cached
    if type(spec) is ModuleSpec and type(origin) is str:
        try:
            return spec.cached
        except Exception as exc:
            unavailable.append(f"cached:{type_name(exc)}")
            return None
    if cached is missing:
        unavailable.append("cached:missing")
    return None


def _package_locations(
    locations: object,
    missing: object,
    iterate_foreign_locations: bool,
    unavailable: list[str],
) -> "tuple[bool | None, tuple[str | ObjectIdentity, ...] | None, LocationsState]":
    if locations is missing:
        unavailable.append("submodule_search_locations:missing")
        return None, None, "failed"
    if locations is None:
        return False, None, "not_applicable"
    if type(locations) not in (list, tuple) and not iterate_foreign_locations:
        return True, None, "deferred"
    try:
        copied = tuple(_safe_location(item) for item in locations)  # type: ignore[union-attr]
    except Exception as exc:
        unavailable.append(f"submodule_search_locations:{type_name(exc)}")
        return True, None, "failed"
    state = "captured" if type(locations) in (list, tuple) else "current_state"
    return True, copied, state


def summarize_spec(
    spec: object,
    *,
    iterate_foreign_locations: bool,
) -> tuple[ModuleSpecSnapshot, object | None]:
    """Copy safe spec semantics without invoking arbitrary attribute access.

    Args:
        spec: Finder return value to inspect.
        iterate_foreign_locations: Whether report-time analysis may make one
            guarded iteration attempt for a non-list/tuple location sequence.

    Returns:
        The plain summary and the raw loader, when it was safely available.
    """
    unavailable: list[str] = []
    namespace = _spec_namespace(spec, unavailable)

    missing = object()
    loader = namespace.get("loader", missing)
    origin = namespace.get("origin", missing)
    locations = namespace.get("submodule_search_locations", missing)
    cached = namespace.get("_cached", missing)

    if loader is missing:
        unavailable.append("loader:missing")
        loader = None
    if origin is missing:
        unavailable.append("origin:missing")
        origin = None
    cached = _cached_path(spec, origin, cached, missing, unavailable)
    is_package, copied_locations, locations_state = _package_locations(
        locations, missing, iterate_foreign_locations, unavailable
    )

    safe_origin = _safe_value(origin)
    is_namespace = is_package and origin is None if is_package is not None else None
    summary = ModuleSpecSnapshot(
        spec=ObjectIdentity.of(spec),
        loader=None if loader is None else ObjectIdentity.of(loader),
        origin=safe_origin,
        cached=_safe_value(cached),
        is_package=is_package,
        is_namespace=is_namespace,
        submodule_search_locations=copied_locations,
        locations_state=locations_state,
        unavailable_fields=tuple(unavailable),
    )
    return summary, loader
