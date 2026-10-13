# Start Exora on Windows (web app + data engine in one process). Run scripts\setup.ps1 once first.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\start.ps1 -BindHost 0.0.0.0 -Port 8090   # LAN access
param([string]$BindHost = "127.0.0.1", [int]$Port = 8090)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exe = Join-Path $root ".venv\Scripts\prospector.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Exora is not installed yet. Run: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1" -ForegroundColor Red
    exit 1
}
& $exe serve --host $BindHost --port $Port
exit $LASTEXITCODE
