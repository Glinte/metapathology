#!/bin/sh
set -eu

cd "$(dirname "$0")"
uv sync

printf '\n=== Direct collision ===\n'
uv run --no-sync python reproduce.py || true

printf '\n=== Deep path-entry attribution ===\n'
uv run --no-sync python -m metapathology --deep reproduce.py || true
