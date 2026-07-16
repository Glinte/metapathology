$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct extension load (two class objects) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same extension load under metapathology ==="
    uv run --no-sync metapathology --deep-path-hooks --deep-path-entry-finders --deep-loaders invoke.py
} finally {
    Pop-Location
}
