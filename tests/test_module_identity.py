"""Safe target-module identity observation."""

from collections.abc import Iterator

import pytest

from metapathology._module_metadata import module_cache_state


class HostileDict(dict[str, object]):
    """Dictionary subclass whose ordinary mapping API must remain untouched."""

    touched = False

    def __getitem__(self, key: str) -> object:
        type(self).touched = True
        raise AssertionError(key)

    def __contains__(self, key: object) -> bool:
        type(self).touched = True
        raise AssertionError(key)

    def __iter__(self) -> Iterator[str]:
        type(self).touched = True
        raise AssertionError

    def __missing__(self, key: str) -> object:
        type(self).touched = True
        raise AssertionError(key)

    def get(self, key: str, default: object = None) -> object:
        type(self).touched = True
        raise AssertionError(key)


class ForeignMapping:
    """Non-dictionary cache replacement that must not be probed."""

    touched = False

    def get(self, key: str, default: object = None) -> object:
        type(self).touched = True
        raise AssertionError(key)


def test_module_cache_state_distinguishes_all_available_states() -> None:
    module = object()
    cache: dict[str, object | None] = {"none": None, "present": module}

    assert module_cache_state(cache, "missing").state == "missing"
    assert module_cache_state(cache, "none").state == "none"
    present = module_cache_state(cache, "present")
    assert present.state == "object"
    assert present.object_id == id(module)
    assert present.type_name == "object"


def test_module_cache_state_bypasses_dict_subclass_mapping_methods() -> None:
    HostileDict.touched = False
    cache = HostileDict()
    dict.__setitem__(cache, "target", object())

    state = module_cache_state(cache, "target")

    assert state.state == "object"
    assert not HostileDict.touched


def test_module_cache_state_declines_foreign_mapping() -> None:
    ForeignMapping.touched = False

    state = module_cache_state(ForeignMapping(), "target")

    assert state.state == "unavailable"
    assert state.object_id is None
    assert state.type_name is None
    assert not ForeignMapping.touched


def test_module_cache_state_is_read_only() -> None:
    state = module_cache_state({}, "target")

    with pytest.raises(AttributeError):
        state.state = "object"  # type: ignore[misc]
