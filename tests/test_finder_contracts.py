"""Conservative meta-path finder protocol inspection."""

from metapathology._monitor import _inspect_finder_protocol


class ModernFinder:
    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        return None


class LegacyFinder:
    __slots__ = ()

    def find_module(self, fullname: str, path: object = None) -> None:
        return None


class DescriptorFinder:
    @property
    def find_spec(self) -> object:
        raise AssertionError("protocol inspection bound a descriptor")


class DynamicFinder:
    def __getattr__(self, name: str) -> object:
        raise AssertionError("protocol inspection performed dynamic lookup")


class HostileMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name in {"__dict__", "__mro__", "__name__"}:
            raise AssertionError("protocol inspection dispatched through the metaclass")
        return super().__getattribute__(name)


class HostileFinder(metaclass=HostileMeta):
    @staticmethod
    def find_spec(fullname: str, path: object = None, target: object = None) -> None:
        return None


def test_protocol_inspection_classifies_raw_dictionary_evidence() -> None:
    modern = _inspect_finder_protocol(ModernFinder(), "find_spec")
    legacy = _inspect_finder_protocol(LegacyFinder(), "find_module")
    absent = _inspect_finder_protocol(LegacyFinder(), "find_spec")

    assert (modern.availability, modern.evidence, modern.defined_by) == (
        "callable",
        "class_dict",
        "ModernFinder",
    )
    assert legacy.availability == "callable"
    assert absent.availability == "absent"


def test_protocol_inspection_never_binds_dynamic_attributes_or_descriptors() -> None:
    descriptor = _inspect_finder_protocol(DescriptorFinder(), "find_spec")
    dynamic = _inspect_finder_protocol(DynamicFinder(), "find_spec")
    hostile = _inspect_finder_protocol(HostileFinder, "find_spec")

    assert descriptor.availability == "indeterminate"
    assert dynamic.availability == "absent"
    assert hostile.availability == "callable"


def test_protocol_inspection_preserves_instance_dictionary_evidence() -> None:
    finder = ModernFinder()
    finder.find_spec = lambda fullname, path=None, target=None: None  # type: ignore[method-assign]

    protocol = _inspect_finder_protocol(finder, "find_spec")

    assert protocol.availability == "callable"
    assert protocol.evidence == "instance_dict"
