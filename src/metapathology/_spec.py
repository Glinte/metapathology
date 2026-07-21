"""Conservative reduction of module specs to plain diagnostic data."""

from importlib.machinery import ModuleSpec

from metapathology._records import ObjectRef, SpecSummary, type_name


def _object_ref(value: object) -> ObjectRef:
    return ObjectRef(id(value), type_name(value))


def _safe_value(value: object) -> str | ObjectRef | None:
    if value is None or type(value) is str:
        return value
    return _object_ref(value)


def _safe_location(value: object) -> str | ObjectRef:
    if type(value) is str:
        return value
    return _object_ref(value)


def summarize_spec(
    spec: object,
    *,
    iterate_foreign_locations: bool,
) -> tuple[SpecSummary, object | None]:
    """Copy safe spec semantics without invoking arbitrary attribute access.

    Args:
        spec: Finder return value to inspect.
        iterate_foreign_locations: Whether report-time analysis may make one
            guarded iteration attempt for a non-list/tuple location sequence.

    Returns:
        The plain summary and the raw loader, when it was safely available.
    """
    unavailable: list[str] = []
    try:
        namespace = object.__getattribute__(spec, "__dict__")
    except Exception as exc:
        namespace = None
        unavailable.append(f"__dict__:{type_name(exc)}")
    if type(namespace) is not dict:
        namespace = {}
        if "__dict__" not in unavailable:
            unavailable.append("__dict__:unavailable")

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
    if cached is missing or cached is None:
        if type(spec) is ModuleSpec and type(origin) is str:
            try:
                cached = spec.cached
            except Exception as exc:
                cached = None
                unavailable.append(f"cached:{type_name(exc)}")
        elif cached is missing:
            cached = None
            unavailable.append("cached:missing")

    copied_locations: tuple[str | ObjectRef, ...] | None = None
    locations_state = "not_applicable"
    is_package: bool | None
    if locations is missing:
        unavailable.append("submodule_search_locations:missing")
        is_package = None
        locations_state = "failed"
    elif locations is None:
        is_package = False
    else:
        is_package = True
        if type(locations) in (list, tuple) or iterate_foreign_locations:
            try:
                copied_locations = tuple(_safe_location(item) for item in locations)
                locations_state = "captured" if type(locations) in (list, tuple) else "post_hoc"
            except Exception as exc:
                locations_state = "failed"
                unavailable.append(f"submodule_search_locations:{type_name(exc)}")
        else:
            locations_state = "deferred"

    safe_origin = _safe_value(origin)
    is_namespace = is_package and origin is None if is_package is not None else None
    summary = SpecSummary(
        spec=_object_ref(spec),
        loader=None if loader is None else _object_ref(loader),
        origin=safe_origin,
        cached=_safe_value(cached),
        is_package=is_package,
        is_namespace=is_namespace,
        submodule_search_locations=copied_locations,
        locations_state=locations_state,
        unavailable_fields=tuple(unavailable),
    )
    return summary, loader
