"""Safe reduction of live module metadata to plain diagnostic data."""

import types

from metapathology._records import ImportObjectRef, SpecSummary, type_name
from metapathology._spec import summarize_spec

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import cast as _cast
else:

    def _cast(_type: object, value: object) -> object:
        return value


class ModuleMetadata:
    """One safely inspected ``sys.modules`` value."""

    __slots__ = (
        "inspection",
        "loader_agreement",
        "loader_source",
        "module",
        "module_loader",
        "module_loader_available",
        "name",
        "reason",
        "spec_is_none",
        "spec_present",
        "spec_summary",
    )

    def __init__(
        self,
        *,
        name: str,
        module: ImportObjectRef,
        inspection: str,
        reason: str | None,
        spec_present: bool,
        spec_is_none: bool,
        spec_summary: SpecSummary | None,
        module_loader_available: bool,
        module_loader: ImportObjectRef | None,
        loader_source: str,
        loader_agreement: bool | None,
    ) -> None:
        self.name = name
        self.module = module
        self.inspection = inspection
        self.reason = reason
        self.spec_present = spec_present
        self.spec_is_none = spec_is_none
        self.spec_summary = spec_summary
        self.module_loader_available = module_loader_available
        self.module_loader = module_loader
        self.loader_source = loader_source
        self.loader_agreement = loader_agreement

    @property
    def loader(self) -> ImportObjectRef | None:
        """Effective loader used to group this module."""
        spec_loader = None if self.spec_summary is None else self.spec_summary.loader
        return spec_loader if spec_loader is not None else self.module_loader


def object_ref(value: object) -> ImportObjectRef:
    """Return import-safe identity metadata for an arbitrary object."""
    return ImportObjectRef(id(value), type_name(value))


def module_namespace(module: object) -> tuple[dict[str, object] | None, str | None]:
    """Read a real module's dictionary without invoking subclass dispatch."""
    if not issubclass(type(module), types.ModuleType):
        return None, "not_module"
    try:
        namespace = types.ModuleType.__getattribute__(_cast("types.ModuleType", module), "__dict__")
    except BaseException as exc:
        return None, f"namespace:{type_name(exc)}"
    if type(namespace) is not dict:
        return None, "namespace:unavailable"
    return namespace, None


def spec_namespace(spec: object) -> dict[str, object] | None:
    """Read an object's real dictionary without invoking foreign dispatch."""
    try:
        namespace = object.__getattribute__(spec, "__dict__")
    except BaseException:
        return None
    return namespace if type(namespace) is dict else None


def safe_spec_loader(spec: object) -> object | None:
    """Return a spec's loader when stored plainly, otherwise None."""
    namespace = spec_namespace(spec)
    return None if namespace is None else namespace.get("loader")


def safe_spec_name(spec: object) -> str | None:
    """Return a spec's exact string name without foreign dispatch."""
    namespace = spec_namespace(spec)
    if namespace is None:
        return None
    name = namespace.get("name")
    return name if type(name) is str else None


def safe_module_name(module: object) -> str | None:
    """Return a module call's current exact string name without materializing it."""
    namespace, _reason = module_namespace(module)
    if namespace is None:
        return None
    spec = namespace.get("__spec__")
    if spec is not None:
        name = safe_spec_name(spec)
        if name is not None:
            return name
    name = namespace.get("__name__")
    return name if type(name) is str else None


def inspect_module(name: str, module: object) -> ModuleMetadata:
    """Capture loader metadata without ordinary module attribute access."""
    reference = object_ref(module)
    namespace, reason = module_namespace(module)
    if namespace is None:
        return ModuleMetadata(
            name=name,
            module=reference,
            inspection="unavailable",
            reason=reason,
            spec_present=False,
            spec_is_none=False,
            spec_summary=None,
            module_loader_available=False,
            module_loader=None,
            loader_source="none",
            loader_agreement=None,
        )

    spec_present = "__spec__" in namespace
    spec = namespace.get("__spec__")
    summary: SpecSummary | None = None
    raw_spec_loader: object | None = None
    spec_loader_available = False
    if spec is not None:
        try:
            summary, raw_spec_loader = summarize_spec(spec, iterate_foreign_locations=False)
            spec_loader_available = "loader:missing" not in summary.unavailable_fields
        except BaseException:
            summary = None

    module_loader_available = "__loader__" in namespace
    raw_module_loader = namespace.get("__loader__")
    module_loader = None if raw_module_loader is None else object_ref(raw_module_loader)
    spec_loader = None if summary is None else summary.loader
    loader_source = "spec" if spec_loader is not None else "module" if module_loader is not None else "none"
    agreement = (raw_spec_loader is raw_module_loader) if spec_loader_available and module_loader_available else None
    return ModuleMetadata(
        name=name,
        module=reference,
        inspection="available",
        reason=None,
        spec_present=spec_present,
        spec_is_none=spec is None,
        spec_summary=summary,
        module_loader_available=module_loader_available,
        module_loader=module_loader,
        loader_source=loader_source,
        loader_agreement=agreement,
    )
