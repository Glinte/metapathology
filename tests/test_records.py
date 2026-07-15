"""Public event-record behavior."""

import pytest

from metapathology import ImportObjectRef, InternalError


def test_record_fields_are_read_only_and_slotted() -> None:
    record = InternalError(seq=1, where="test", exception_type_name="ValueError")

    assert record.seq == 1
    assert not hasattr(record, "__dict__")
    with pytest.raises(AttributeError, match="read-only"):
        setattr(record, "seq", 2)


def test_records_keep_repr_without_generated_equality_or_pattern_matching() -> None:
    first = InternalError(seq=1, where="test", exception_type_name="ValueError")
    second = InternalError(seq=1, where="test", exception_type_name="ValueError")

    assert repr(first) == ("InternalError(seq=1, where='test', exception_type_name='ValueError', message=None)")
    assert type(first).__eq__ is object.__eq__
    assert first != second
    assert not hasattr(type(first), "__match_args__")


def test_import_object_reference_is_plain_read_only_identity_data() -> None:
    reference = ImportObjectRef(object_id=42, type_name="function", name="path_hook_for_FileFinder")

    assert repr(reference) == ("ImportObjectRef(object_id=42, type_name='function', name='path_hook_for_FileFinder')")
    assert not hasattr(reference, "__dict__")
    with pytest.raises(AttributeError, match="read-only"):
        setattr(reference, "name", "changed")
