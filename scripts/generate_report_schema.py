"""Generate the bundled JSON Schema from the report ``TypedDict`` graph."""

from __future__ import annotations

import argparse
import json
import types
from pathlib import Path
from typing import Literal, Union, cast, get_args, get_origin, get_type_hints, is_typeddict

from metapathology._report_schema import ReportJSON

_ROOT = Path(__file__).resolve().parents[1]
_DESTINATION = _ROOT / "src" / "metapathology" / "report.schema.json"


def build_schema() -> dict[str, object]:
    """Build the complete draft 2020-12 report schema."""
    definitions: dict[str, object] = {}
    _typed_dict_schema(ReportJSON, definitions)
    root = definitions.pop("ReportJSON")
    assert isinstance(root, dict)
    properties = root["properties"]
    assert isinstance(properties, dict)
    schema_field = properties["schema"]
    assert isinstance(schema_field, dict)
    schema_ref = schema_field["$ref"]
    assert isinstance(schema_ref, str)
    schema_definition = definitions[schema_ref.rsplit("/", 1)[-1]]
    assert isinstance(schema_definition, dict)
    schema_properties = schema_definition["properties"]
    assert isinstance(schema_properties, dict)
    schema_properties["major"] = {"const": 2}
    schema_properties["minor"] = {"const": 0}
    schema_properties["name"] = {"const": "metapathology.report"}
    tool_definition = definitions["ToolInfo"]
    assert isinstance(tool_definition, dict)
    tool_properties = tool_definition["properties"]
    assert isinstance(tool_properties, dict)
    tool_properties["name"] = {"const": "metapathology"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://glinte.github.io/metapathology/schema/report-2.0.json",
        "title": "metapathology report",
        "description": "Stable machine-readable import-hook diagnostic report.",
        **root,
        "$defs": definitions,
    }


def _typed_dict_schema(type_: type[object], definitions: dict[str, object]) -> dict[str, object]:
    """Return a reference while recursively registering one ``TypedDict``."""
    name = type_.__name__
    reference: dict[str, object] = {"$ref": f"#/$defs/{name}"}
    if name in definitions:
        return reference
    definitions[name] = {}
    hints = get_type_hints(type_, include_extras=True)
    required_keys = type_.__required_keys__  # type: ignore[attr-defined]
    definitions[name] = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(required_keys),
        "properties": {key: _type_schema(value, definitions) for key, value in sorted(hints.items())},
    }
    return reference


def _type_schema(type_: object, definitions: dict[str, object]) -> dict[str, object]:
    """Translate one supported annotation into JSON Schema."""
    if is_typeddict(type_):
        return _typed_dict_schema(cast(type[object], type_), definitions)
    origin = get_origin(type_)
    arguments = get_args(type_)
    if origin is list:
        return {"type": "array", "items": _type_schema(arguments[0], definitions)}
    if origin is Literal:
        return {"enum": list(arguments)}
    if origin in (Union, types.UnionType):
        return {"anyOf": [_type_schema(argument, definitions) for argument in arguments]}
    if type_ is str:
        return {"type": "string"}
    if type_ is int:
        return {"type": "integer"}
    if type_ is bool:
        return {"type": "boolean"}
    if type_ is type(None):
        return {"type": "null"}
    raise TypeError(f"unsupported report annotation: {type_!r}")


def main() -> None:
    """Write or check the generated schema."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if _DESTINATION.read_text(encoding="utf-8") != rendered:
            raise SystemExit("report.schema.json is out of date; run scripts/generate_report_schema.py")
        return
    _DESTINATION.write_text(rendered, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
