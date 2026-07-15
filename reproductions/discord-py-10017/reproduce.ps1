$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct extension load (two class objects) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same extension load under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
