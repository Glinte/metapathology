$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false

Push-Location $PSScriptRoot
try {
    uv sync

    Write-Host "`n=== Direct collision ==="
    uv run --no-sync python reproduce.py
    if ($LASTEXITCODE -eq 0) {
        throw "The direct collision unexpectedly succeeded."
    }

    Write-Host "`n=== Deep path-entry attribution ==="
    uv run --no-sync python -m metapathology --deep reproduce.py
    if ($LASTEXITCODE -eq 0) {
        throw "The monitored collision unexpectedly succeeded."
    }
} finally {
    Pop-Location
}
