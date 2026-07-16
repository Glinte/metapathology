$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync
    $env:PYTHONPATH = Join-Path $PSScriptRoot "src"

    Write-Host "`n=== Control: package coverage succeeds ==="
    uv run --no-sync pytest -c pyproject.toml --cov bt_repro tests/test_foo.py

    Write-Host "`n=== Historical failure under metapathology ==="
    uv run --no-sync metapathology --report t7-report.json --report-format json -m pytest -c pyproject.toml --cov bt_repro.foo tests/test_foo.py
    $Report = Get-ChildItem -Path "t7-report.*.json" | Sort-Object LastWriteTime | Select-Object -Last 1
    uv run --no-sync python validate_report.py $Report.FullName
} finally {
    Pop-Location
}
