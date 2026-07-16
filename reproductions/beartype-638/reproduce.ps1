$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync
    $env:PYTHONPATH = Join-Path $PSScriptRoot "src"

    Write-Host "`n=== Control: package coverage succeeds ==="
    uv run --no-sync pytest -c pyproject.toml --cov bt_repro tests/test_foo.py

    Write-Host "`n=== Historical failure under metapathology ==="
    uv run --no-sync metapathology -m pytest -c pyproject.toml --cov bt_repro.foo tests/test_foo.py
} finally {
    Pop-Location
}
