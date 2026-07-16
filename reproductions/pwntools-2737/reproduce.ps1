$ErrorActionPreference = "Continue"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct pwntools import (historical failure) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same import under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
