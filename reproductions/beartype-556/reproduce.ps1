$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct console command (bug: exits successfully) ==="
    uv run --no-sync myproject

    Write-Host "`n=== Same import under the metapathology command ==="
    uv run --no-sync metapathology --report t7-report.json --report-format json invoke.py
    $Report = Get-ChildItem -Path "t7-report.*.json" | Sort-Object LastWriteTime | Select-Object -Last 1
    uv run --no-sync python validate_report.py $Report.FullName
} finally {
    Pop-Location
}
