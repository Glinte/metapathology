"""Public event-record behavior."""

import types

import pytest

from metapathology import ImportAuditStart, ImporterCacheEntry, ImporterCacheReplacement, InternalError, ObjectRef


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


def test_object_reference_is_plain_read_only_identity_data() -> None:
    reference = ObjectRef(object_id=42, type_name="function", name="path_hook_for_FileFinder")

    assert repr(reference) == ("ObjectRef(object_id=42, type_name='function', name='path_hook_for_FileFinder')")
    assert not hasattr(reference, "__dict__")
    with pytest.raises(AttributeError, match="read-only"):
        setattr(reference, "name", "changed")


def test_object_ref_of_captures_function_name_but_not_plain_object_name() -> None:
    def path_hook_for_FileFinder(path: str) -> None: ...

    hook_ref = ObjectRef.of(path_hook_for_FileFinder)
    assert hook_ref.object_id == id(path_hook_for_FileFinder)
    assert hook_ref.type_name == "function"
    assert hook_ref.name == "path_hook_for_FileFinder"

    plain = object()
    plain_ref = ObjectRef.of(plain)
    assert plain_ref.object_id == id(plain)
    assert plain_ref.type_name == "object"
    assert plain_ref.name is None


def test_object_ref_of_does_not_dispatch_to_a_spoofed_class_attribute() -> None:
    class Hostile:
        touched = False

        @property
        def __class__(self) -> type:  # type: ignore[override]
            Hostile.touched = True
            return types.FunctionType

    hostile = Hostile()
    reference = ObjectRef.of(hostile)

    assert Hostile.touched is False
    assert reference.type_name == "Hostile"
    assert reference.name is None


def test_importer_cache_values_distinguish_negative_entries_and_replacements() -> None:
    finder = ObjectRef(object_id=42, type_name="FileFinder")
    entry = ImporterCacheEntry(path="/example", finder=None)
    replacement = ImporterCacheReplacement(path="/example", before=finder, after=None)

    assert repr(entry) == "ImporterCacheEntry(path='/example', finder=None)"
    assert replacement.before is finder
    assert replacement.after is None
    assert not hasattr(entry, "__dict__")
    with pytest.raises(AttributeError, match="read-only"):
        setattr(entry, "path", "/changed")


def test_import_audit_start_contains_only_plain_capture_data() -> None:
    record = ImportAuditStart(
        seq=7,
        attempt_id=3,
        fullname="example.module",
        meta_path_id=41,
        meta_path_type_names=("BuiltinImporter", "PathFinder"),
        path_hooks_id=42,
        importer_cache_id=43,
        importer_cache_size=5,
        thread_name="MainThread",
        thread_id=1,
    )

    assert repr(record) == (
        "ImportAuditStart(seq=7, attempt_id=3, fullname='example.module', meta_path_id=41, "
        "meta_path_type_names=('BuiltinImporter', 'PathFinder'), path_hooks_id=42, "
        "importer_cache_id=43, importer_cache_size=5, thread_name='MainThread', thread_id=1)"
    )
    assert not hasattr(record, "__dict__")
    with pytest.raises(AttributeError, match="read-only"):
        setattr(record, "fullname", "changed")
