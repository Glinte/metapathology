$ErrorActionPreference = "Continue"

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct pytest collection (historical failure) ==="
    uv run --no-sync pytest -c pyproject.toml --import-mode=importlib tests/repro_pkg/test_value.py

    Write-Host "`n=== Same collection under metapathology ==="
    uv run --no-sync python invoke.py
} finally {
    Pop-Location
}
