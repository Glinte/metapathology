$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Current Bifrost insertion policy ==="
    uv run --no-sync python reproduce.py

    Write-Host "`n=== Insert immediately before PathFinder ==="
    uv run --no-sync python control.py

    Write-Host "`n=== Current policy under metapathology ==="
    uv run --no-sync python -m metapathology --report report.txt --report-format text reproduce.py
} finally {
    Pop-Location
}
