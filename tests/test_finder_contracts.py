"""Conservative meta-path finder protocol inspection."""

import subprocess
from collections.abc import Callable

from metapathology._finder_attribution import _FinderAttribution

RunPython = Callable[..., "subprocess.CompletedProcess[str]"]


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
    modern = _FinderAttribution._inspect_protocol(ModernFinder(), "find_spec")
    legacy = _FinderAttribution._inspect_protocol(LegacyFinder(), "find_module")
    absent = _FinderAttribution._inspect_protocol(LegacyFinder(), "find_spec")

    assert (modern.availability, modern.evidence, modern.defined_by) == (
        "callable",
        "class_dict",
        "ModernFinder",
    )
    assert legacy.availability == "callable"
    assert absent.availability == "absent"


def test_protocol_inspection_never_binds_dynamic_attributes_or_descriptors() -> None:
    descriptor = _FinderAttribution._inspect_protocol(DescriptorFinder(), "find_spec")
    dynamic = _FinderAttribution._inspect_protocol(DynamicFinder(), "find_spec")
    hostile = _FinderAttribution._inspect_protocol(HostileFinder, "find_spec")

    assert descriptor.availability == "indeterminate"
    assert dynamic.availability == "absent"
    assert hostile.availability == "callable"


def test_protocol_inspection_preserves_instance_dictionary_evidence() -> None:
    finder = ModernFinder()
    finder.find_spec = lambda fullname, path=None, target=None: None  # type: ignore[method-assign]

    protocol = _FinderAttribution._inspect_protocol(finder, "find_spec")

    assert protocol.availability == "callable"
    assert protocol.evidence == "instance_dict"


CONTRACT_REPORT = r"""
import json
import sys

import metapathology

class LegacyFinder:
    def find_module(self, fullname, path=None):
        return None

finder = LegacyFinder()
before = dict(finder.__dict__)
metapathology.install(report_at_exit=False)
sys.meta_path.append(finder)
replacement_finder = LegacyFinder()
sys.meta_path = [replacement_finder, *sys.meta_path]
import fractions
document = json.loads(metapathology.render_report(format="json"))
legacy_contracts = [item for item in document["finder_contracts"] if item["finder_type_name"] == "LegacyFinder"]
contract = next(item for item in legacy_contracts if item["finder_id"] == hex(id(finder)))
assert contract["category"] == "legacy_only", contract
assert contract["find_spec"] == {
    "availability": "absent",
    "defined_by": None,
    "evidence": "class_mro",
}
assert contract["find_module"]["availability"] == "callable"
assert contract["observation"] == "mutation"
assert contract["observation_event_ref"].startswith("event:")
event_id = contract["observation_event_ref"]
mutation = next(event for event in document["timeline"] if event["id"] == event_id)
assert mutation["kind"] == "meta_path_mutation"
assert mutation["data"]["added"] == ["LegacyFinder"]
replacement_contract = next(item for item in legacy_contracts if item["finder_id"] == hex(id(replacement_finder)))
assert replacement_contract["observation"] == "reassignment"
assert replacement_contract["observation_event_ref"] is None
finding = next(item for item in document["findings"] if item["kind"] == "legacy_finder_contract")
assert finding["subject"] == {"kind": "finder", "value": "LegacyFinder"}
assert finding["data"]["finder_contract_ref"] == f"finder-contract:{hex(id(finder))}"
assert any(
    contract["id"] == finding["data"]["finder_contract_ref"] and contract["finder_id"] == hex(id(finder))
    for contract in document["finder_contracts"]
)
assert finding["evidence"]["level"] == "captured"
assert finding["evidence"]["event_refs"] == [event_id]
text = metapathology.render_report()
assert "[legacy-only] LegacyFinder" in text, text
assert "[legacy-finder-contract] LegacyFinder" in text, text
assert "CPython 3.12+" in text, text
metapathology.uninstall()
assert finder.__dict__ == before
print("OK")
"""


def test_contract_report_links_mutation_and_restores_finder(run_python: RunPython) -> None:
    proc = run_python(CONTRACT_REPORT)

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"
