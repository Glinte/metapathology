$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct import (mqt.core is hidden) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same imports under metapathology ==="
    uv run --no-sync metapathology --deep-import-outcomes invoke.py
} finally {
    Pop-Location
}
