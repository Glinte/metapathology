"""Small semantic selectors for structured test evidence."""

from collections.abc import Iterable, Mapping
from typing import TypeVar

_ItemT = TypeVar("_ItemT")


def one_matching(items: Iterable[_ItemT], /, **attributes: object) -> _ItemT:
    """Return the only item whose mapping keys or attributes match."""
    candidates = list(items)
    matches = [
        item for item in candidates if all(_value(item, name) == expected for name, expected in attributes.items())
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one item matching {attributes!r}, found {len(matches)} in {candidates!r}"
        )
    return matches[0]


def assert_mapping_contains(actual: Mapping[str, object], /, **expected: object) -> None:
    """Assert selected mapping values while preserving a compact semantic diff."""
    projection = {name: actual[name] for name in expected}
    assert projection == expected


def _value(item: object, name: str) -> object:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)
