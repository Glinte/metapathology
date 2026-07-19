$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    $Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv" }

    $VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    $env:PATH = "$(Join-Path $PSScriptRoot '.venv\Scripts');$env:PATH"

    function Invoke-VenvPython {
        & $VenvPython @args
        if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
    }

    Invoke-VenvPython -m pip install --upgrade pip
    Invoke-VenvPython -m pip install `
        "beartype==0.22.9" `
        "meson==1.11.2" `
        "meson-python==0.20.0" `
        "metapathology==0.4.3" `
        "ninja==1.13.0"
    Invoke-VenvPython -m pip install --no-build-isolation --editable .

    Write-Host "`n=== Direct run (bug: invalid call exits successfully) ==="
    Invoke-VenvPython invoke.py

    Write-Host "`n=== Same import under metapathology ==="
    Invoke-VenvPython -m metapathology invoke.py
} finally {
    Pop-Location
}
