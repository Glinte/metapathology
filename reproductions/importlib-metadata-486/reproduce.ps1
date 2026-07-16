$ErrorActionPreference = "Continue"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct importlib_metadata lookup (historical failure) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same lookup under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
