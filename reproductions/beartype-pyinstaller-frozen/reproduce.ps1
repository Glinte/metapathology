$ErrorActionPreference = "Stop"

Push-Location $PSScriptRoot
try {
    uv sync

    $collect = "--collect-submodules", "key_value", "--collect-submodules", "beartype", "--collect-submodules", "diskcache"
    $meta = "--copy-metadata", "py-key-value-aio", "--copy-metadata", "beartype"
    $build = @("run", "--no-sync", "pyinstaller", "--onefile", "--noconfirm", "--log-level", "ERROR") + $collect + $meta

    Write-Host "`n=== Control: run unfrozen (real .py files back every module) ==="
    uv run --no-sync python app.py

    Write-Host "`n=== Frozen without the fix: the bug ==="
    uv @build --name app_nofix app.py
    & .\dist\app_nofix.exe
    if ($LASTEXITCODE -ne 0) { Write-Host "(exited non-zero, as expected)" }

    Write-Host "`n=== Frozen under metapathology: the diagnosis ==="
    uv @build --name app_metapathology --collect-submodules metapathology app_metapathology.py
    & .\dist\app_metapathology.exe
    if ($LASTEXITCODE -ne 0) { Write-Host "(exited non-zero, as expected)" }
    Write-Host "`n--- metapathology report ---"
    Get-Content .\dist\mp_report.txt

    Write-Host "`n=== Frozen with the runtime-hook fix: control ==="
    uv @build --name app_fixed --runtime-hook rth_beartype_frozen.py app.py
    & .\dist\app_fixed.exe
} finally {
    Pop-Location
}
