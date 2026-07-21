#!/bin/sh
set -eu

cd "$(dirname "$0")"
uv sync

COLLECT="--collect-submodules key_value --collect-submodules beartype --collect-submodules diskcache"
META="--copy-metadata py-key-value-aio --copy-metadata beartype"
BUILD="uv run --no-sync pyinstaller --onefile --noconfirm --log-level ERROR $COLLECT $META"

printf '\n=== Control: run unfrozen (real .py files back every module) ===\n'
uv run --no-sync python app.py

printf '\n=== Frozen without the fix: the bug ===\n'
$BUILD --name app_nofix app.py
./dist/app_nofix || printf '(exited non-zero, as expected)\n'

printf '\n=== Frozen under metapathology: the diagnosis ===\n'
$BUILD --name app_metapathology --collect-submodules metapathology app_metapathology.py
./dist/app_metapathology || printf '(exited non-zero, as expected)\n'
printf '\n--- metapathology report ---\n'
cat ./dist/mp_report.txt

printf '\n=== Frozen with the runtime-hook fix: control ===\n'
$BUILD --name app_fixed --runtime-hook rth_beartype_frozen.py app.py
./dist/app_fixed
