$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct import (cwd namespace wins) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same import under metapathology ==="
    uv run --no-sync metapathology --deep-import-outcomes invoke.py
} finally {
    Pop-Location
}
