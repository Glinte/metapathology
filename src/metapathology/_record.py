"""Shared machinery for small immutable internal records.

Record classes declare their fields once as annotations. ``_RecordMeta``
derives slots, field order, and a read-only initializer while keeping runtime
imports free of ``typing`` and ``typing_extensions``.
"""

TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import ClassVar

    # ``dataclass_transform`` lives in ``typing`` only from 3.11; this import
    # is checker-only so Python 3.10 still has no runtime dependency.
    from typing_extensions import dataclass_transform

    # ``annotationlib`` (3.14+) is unavailable at the 3.10 check target.
    _annotationlib = None
else:

    def dataclass_transform(**_kwargs: object):
        """Runtime no-op standing in for ``typing.dataclass_transform``."""

        def decorate(cls: object) -> object:
            return cls

        return decorate

    try:
        # Python 3.14+ (PEP 649) exposes lazy class annotations.
        import annotationlib as _annotationlib
    except ImportError:  # Python < 3.14
        _annotationlib = None


def _field_names(namespace: "dict[str, object]") -> "tuple[str, ...]":
    """Return public annotated field names in declaration order."""
    annotations = namespace.get("__annotations__")
    if not isinstance(annotations, dict) and _annotationlib is not None:
        annotate = namespace.get("__annotate_func__")
        if annotate is not None:
            annotations = _annotationlib.call_annotate_function(annotate, _annotationlib.Format.FORWARDREF)
    if not isinstance(annotations, dict):
        return ()
    return tuple(name for name in annotations if isinstance(name, str) and not name.startswith("_"))


def _make_init(cls_name: str, fields: "tuple[str, ...]", defaults: "dict[str, object]"):
    """Build a read-only initializer accepting positional or keyword fields."""

    def __init__(self: object, *args: object, **kwargs: object) -> None:
        if len(args) > len(fields):
            raise TypeError(f"{cls_name}() takes at most {len(fields)} positional arguments")
        values: dict[str, object] = dict(zip(fields, args))
        for key, value in kwargs.items():
            if key not in fields:
                raise TypeError(f"{cls_name}() got an unexpected keyword argument {key!r}")
            if key in values:
                raise TypeError(f"{cls_name}() got multiple values for argument {key!r}")
            values[key] = value
        for name in fields:
            if name not in values:
                if name in defaults:
                    values[name] = defaults[name]
                else:
                    raise TypeError(f"{cls_name}() missing required argument {name!r}")
            object.__setattr__(self, name, values[name])

    return __init__


@dataclass_transform(frozen_default=True, eq_default=False)
class _RecordMeta(type):
    """Derive slots, field order, and a frozen initializer from annotations."""

    def __new__(mcs, name: str, bases: "tuple[type, ...]", namespace: "dict[str, object]") -> type:
        fields = _field_names(namespace)
        if fields:
            defaults = {field: namespace.pop(field) for field in fields if field in namespace}
            namespace["__slots__"] = fields
            namespace["_fields"] = fields
            namespace["__init__"] = _make_init(name, fields, defaults)
        return super().__new__(mcs, name, bases, namespace)


class _Record(metaclass=_RecordMeta):
    """Frozen, slotted base with identity equality and a shared repr."""

    __slots__ = ()
    _fields: "ClassVar[tuple[str, ...]]" = ()

    if not TYPE_CHECKING:
        # The checker already treats these records as frozen through
        # ``dataclass_transform``.
        def __setattr__(self, name: str, value: object) -> None:
            raise AttributeError(f"{type(self).__name__!r} attribute {name!r} is read-only")

        def __delattr__(self, name: str) -> None:
            raise AttributeError(f"{type(self).__name__!r} attribute {name!r} is read-only")

    def __repr__(self) -> str:
        fields = ", ".join(f"{name}={getattr(self, name)!r}" for name in self._fields)
        return f"{type(self).__name__}({fields})"
