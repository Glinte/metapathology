$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct console command (bug: exits successfully) ==="
    uv run --no-sync myproject

    Write-Host "`n=== Same import under the metapathology command ==="
    uv run --no-sync metapathology invoke.py
} finally {
    Pop-Location
}
