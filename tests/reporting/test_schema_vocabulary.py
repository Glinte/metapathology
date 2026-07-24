"""Guard the JSON schema vocabulary against drifting from the event registry.

Schema 2.0 duplicates the timeline ``kind`` vocabulary in two places: the
``EVENT_KIND`` dispatch registry (the runtime source of truth) and the
``EventKind`` ``Literal`` the schema generator reads. These must stay identical,
or the bundled schema would advertise a ``kind`` enum that real reports violate.

The 2.0 vocabulary tightening (mechanism ``name``/``completeness``, finder
contract ``category``/``observation``, module ``inspection``/``loader_source``,
event ``outcome``, and the nested snapshot/finding ``data`` unions) closed many
previously-open ``str`` fields. ``test_real_report_conforms_to_bundled_schema``
guards those enums against real output, so a producer emitting a value outside
its closed vocabulary fails loudly instead of silently violating the schema.
"""

import importlib.resources
import json
from typing import get_args

from support import PythonRunner

from metapathology._report_events import EVENT_KIND
from metapathology._report_json import _EVENT_JSON_BUILDERS
from metapathology._report_schema import EventKind
from metapathology._report_text import _TIMELINE_LINE_BUILDERS


def test_event_kind_literal_matches_registry() -> None:
    assert set(get_args(EventKind)) == set(EVENT_KIND.values())


def test_every_event_type_has_a_payload_builder() -> None:
    # Each dispatched event type must have both a stable ``kind`` and a JSON
    # payload builder, so ``_json_event`` can always build a full envelope.
    assert set(_EVENT_JSON_BUILDERS) == set(EVENT_KIND)


def test_every_event_type_has_a_text_timeline_builder() -> None:
    assert set(_TIMELINE_LINE_BUILDERS) == set(EVENT_KIND)


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _resolve(schema: dict[str, object], root: dict[str, object]) -> dict[str, object]:
    """Follow ``$ref`` chains into the schema's ``$defs``."""
    while "$ref" in schema:
        name = str(schema["$ref"]).rsplit("/", 1)[-1]
        defs = root["$defs"]
        assert isinstance(defs, dict)
        target = defs[name]
        assert isinstance(target, dict)
        schema = target
    return schema


def _assert_json_type(value: object, declared: str, path: str) -> None:
    """Check a scalar's JSON type, keeping ``boolean`` distinct from ``integer``."""
    if declared == "integer":
        # bool is an int subclass; the two vocabularies must stay distinct.
        assert isinstance(value, int), f"{path}: {value!r} is not an integer"
        assert not isinstance(value, bool), f"{path}: {value!r} is not an integer"
        return
    assert isinstance(value, _JSON_TYPES[declared]), f"{path}: {value!r} is not {declared}"


def _assert_any_of(value: object, branches: list[object], root: dict[str, object], path: str) -> None:
    """Pass when the value conforms to at least one union branch."""
    errors: list[str] = []
    for branch in branches:
        assert isinstance(branch, dict)
        try:
            _assert_conforms(value, branch, root, path)
            return
        except AssertionError as error:
            errors.append(str(error))
    raise AssertionError(f"{path}: {value!r} matched no anyOf branch ({errors})")


def _assert_children(value: object, schema: dict[str, object], root: dict[str, object], path: str) -> None:
    """Recurse into a declared object's properties or an array's items."""
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        assert isinstance(properties, dict)
        for key, item in value.items():
            declared_property = properties.get(key)
            if isinstance(declared_property, dict):
                _assert_conforms(item, declared_property, root, f"{path}.{key}")
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _assert_conforms(item, items, root, f"{path}[{index}]")


def _assert_conforms(value: object, schema: dict[str, object], root: dict[str, object], path: str) -> None:
    """Validate one value against the subset of JSON Schema the generator emits.

    Handles ``$ref``, ``const``, ``enum``, ``anyOf``, typed scalars, arrays, and
    objects (recursing only into declared properties). Enough to prove that real
    report output respects every closed-vocabulary ``enum`` in the bundled schema.
    """
    schema = _resolve(schema, root)
    if "const" in schema:
        assert value == schema["const"], f"{path}: {value!r} != const {schema['const']!r}"
        return
    if "enum" in schema:
        options = schema["enum"]
        assert isinstance(options, list)
        assert value in options, f"{path}: {value!r} not in enum {options}"
        return
    if "anyOf" in schema:
        branches = schema["anyOf"]
        assert isinstance(branches, list)
        _assert_any_of(value, branches, root, path)
        return
    declared = schema.get("type")
    if isinstance(declared, str):
        _assert_json_type(value, declared, path)
    _assert_children(value, schema, root, path)


# Exercises the tightened vocabularies: detailed diagnostics (detailed ``outcome``s and a
# ``module_executed_again`` detailed-call finding), a custom finder (contract
# ``observation``/``category``), meta_path/path_hooks/importer-cache mutations,
# a spec-less module (``module_without_spec`` finding), and ordinary imports (loader
# inventory ``inspection``/``loader_source``).
_CONFORMANCE_REPORT = r"""
import importlib
import json
import sys
import types

import metapathology


class CheckFinder:
    def find_spec(self, fullname, path=None, target=None):
        return None


metapathology.install(report_at_exit=False, capture=metapathology.CaptureConfig(detailed=metapathology.DetailedCaptureConfig(enabled=True)))
sys.path_importer_cache["conformance-cache-check"] = None
sys.meta_path = list(sys.meta_path)
sys.path_hooks = list(sys.path_hooks)


def check_hook(path):
    return None


sys.path_hooks.append(check_hook)
sys.meta_path.insert(0, CheckFinder())
importlib.import_module("colorsys")
importlib.import_module("fractions")
sys.modules["ghost_mod"] = types.ModuleType("ghost_mod")

print(json.dumps(metapathology.get_report()))
"""


def test_real_report_conforms_to_bundled_schema(python_runner: PythonRunner) -> None:
    schema = json.loads(
        importlib.resources.files("metapathology").joinpath("report.schema.json").read_text(encoding="utf-8")
    )
    proc = python_runner.run_code_ok(_CONFORMANCE_REPORT)
    document = json.loads(proc.stdout)
    _assert_conforms(document, schema, schema, "report")
    # Sanity: the scenario must actually populate the closed-vocabulary fields
    # so the conformance walk has something to check.
    assert document["capture"]["mechanisms"], "no mechanisms emitted"
    assert any(finding["kind"] == "module_without_spec" for finding in document["findings"]), "no findings emitted"
