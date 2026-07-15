$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct backend import (editable finder wins) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same backend import under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
