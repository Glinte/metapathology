$ErrorActionPreference = "Continue"

Push-Location $PSScriptRoot
try {
    uv sync
    uv pip install --python .venv\Scripts\python.exe --target old-setuptools "setuptools<45"

    $env:PYTHONPATH = (Join-Path $PSScriptRoot "old-setuptools")
    try {
        Write-Host "`n=== Direct import (historical AttributeError) ==="
        uv run --no-sync python invoke.py

        Write-Host "`n=== Same import under metapathology ==="
        uv run --no-sync metapathology invoke.py
    } finally {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
} finally {
    Pop-Location
}
