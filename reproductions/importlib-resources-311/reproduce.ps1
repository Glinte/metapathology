$ErrorActionPreference = "Continue"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct importlib_resources.files() call (historical failure) ==="
    uv run --no-sync python invoke.py

    Write-Host "`n=== Same call under metapathology ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
