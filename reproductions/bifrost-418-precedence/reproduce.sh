#!/bin/sh
set -eu

cd "$(dirname "$0")"
uv sync

printf '\n=== Current Bifrost insertion policy ===\n'
uv run --no-sync python reproduce.py

printf '\n=== Insert immediately before PathFinder ===\n'
uv run --no-sync python control.py

printf '\n=== Current policy under metapathology ===\n'
uv run --no-sync python -m metapathology --report reproduce.py
