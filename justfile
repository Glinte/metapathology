# Work in progress
default:
  just --help

AAA:
  Get-ChildItem .\reproductions -Directory | ForEach-Object {
      Write-Host "`n=== $($_.Name) ===" -ForegroundColor Cyan
      & (Join-Path $_.FullName 'reproduce.ps1')
      Write-Host "exit code: $LASTEXITCODE"
  }
