#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

uv sync

printf '\n=== Direct run (bug: invalid call exits successfully) ===\n'
uv run --no-sync python invoke.py

printf '\n=== Same import under metapathology ===\n'
uv run --no-sync metapathology invoke.py
