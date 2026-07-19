#!/bin/sh
set -eu

cd "$(dirname "$0")"
uv sync

printf '\n=== Package coverage works ===\n'
uv run --no-sync pytest -q --cov=eager_source tests/test_normalization.py

printf '\n=== Dotted-module coverage may fail after loading NumPy twice ===\n'
uv run --no-sync pytest -q --cov=eager_source.normalization tests/test_normalization.py || true

printf '\n=== Same command with deep metapathology evidence ===\n'
uv run --no-sync python -m metapathology --deep --report report.json --report-format json -m pytest -q --cov=eager_source.normalization tests/test_normalization.py || true
