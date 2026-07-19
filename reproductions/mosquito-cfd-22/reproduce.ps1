$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Package coverage works ==="
    uv run --no-sync pytest -q --cov=eager_source tests/test_normalization.py

    Write-Host "`n=== Dotted-module coverage fails after loading NumPy twice ==="
    uv run --no-sync pytest -q --cov=eager_source.normalization tests/test_normalization.py
    if ($LASTEXITCODE -eq 0) {
        throw "The dotted-module command unexpectedly succeeded on this platform."
    }

    Write-Host "`n=== Same failure with deep metapathology evidence ==="
    uv run --no-sync python -m metapathology --deep --report report.json --report-format json -m pytest -q --cov=eager_source.normalization tests/test_normalization.py
    if ($LASTEXITCODE -eq 0) {
        throw "The monitored dotted-module command unexpectedly succeeded."
    }
} finally {
    Pop-Location
}
