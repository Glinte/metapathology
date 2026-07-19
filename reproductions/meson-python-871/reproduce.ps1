$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct run (bug: invalid call exits successfully) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same import under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
