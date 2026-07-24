"""Contracts for shared test support."""

import subprocess

import pytest
from support import PythonRunner, assert_mapping_contains, json_object_stdout, one_matching


def test_python_runner_decodes_one_json_object(python_runner: PythonRunner) -> None:
    result = python_runner.run_code_json("import json; print(json.dumps({'status': 'ready', 'count': 2}))")

    assert result == {"status": "ready", "count": 2}


def test_json_object_decoder_rejects_other_json_shapes() -> None:
    process = subprocess.CompletedProcess[str]([], 0, "[]", "")

    with pytest.raises(AssertionError, match="expected child JSON object, got list"):
        json_object_stdout(process)


def test_semantic_mapping_helpers_require_one_complete_match() -> None:
    items = [{"kind": "first", "value": 1}, {"kind": "second", "value": 2}]

    selected = one_matching(items, kind="second")
    assert_mapping_contains(selected, kind="second", value=2)

    with pytest.raises(AssertionError, match="found 0"):
        one_matching(items, kind="missing")
    with pytest.raises(KeyError):
        assert_mapping_contains(selected, absent=None)
