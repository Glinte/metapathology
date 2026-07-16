$ErrorActionPreference = "Continue"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct wrapt post-import hook run (historical failure) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same run under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
