$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Historical failure: __main__ configuration lookup ==="
    uv run --no-sync python -m beartypeproject.my_functions

    Write-Host "`n=== Same module execution under metapathology ==="
    uv run --no-sync metapathology -m beartypeproject.my_functions
} finally {
    Pop-Location
}
