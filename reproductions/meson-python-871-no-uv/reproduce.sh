#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"

if [ -n "${PYTHON:-}" ]; then
    python_bin=$PYTHON
elif command -v python3 >/dev/null 2>&1; then
    python_bin=python3
else
    python_bin=python
fi

"$python_bin" -m venv .venv
venv_python=.venv/bin/python
PATH="$script_dir/.venv/bin:$PATH"
export PATH

"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install \
    'beartype==0.22.9' \
    'meson==1.11.2' \
    'meson-python==0.20.0' \
    'metapathology==0.4.3' \
    'ninja==1.13.0'
"$venv_python" -m pip install --no-build-isolation --editable .

printf '\n=== Direct run (bug: invalid call exits successfully) ===\n'
"$venv_python" invoke.py

printf '\n=== Same import under metapathology ===\n'
"$venv_python" -m metapathology invoke.py
