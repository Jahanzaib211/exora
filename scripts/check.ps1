# Quality gate: run every check the way CI does. Exit code is non-zero if anything fails.
#   powershell -ExecutionPolicy Bypass -File scripts\check.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONDONTWRITEBYTECODE = "1"

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) { $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe" }
if (-not (Test-Path $uv)) { Write-Host "uv not found - run scripts\setup.ps1 first." -ForegroundColor Red; exit 1 }

$failed = @()
function Step($name, [scriptblock]$cmd) {
    Write-Host "== $name" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:failed += $name; Write-Host "FAILED: $name" -ForegroundColor Red }
}

Step "Python lint (ruff)"          { & $uv run --frozen --extra dev ruff check src tests scripts --no-cache }
Step "Python format (ruff)"        { & $uv run --frozen --extra dev ruff format --check src tests scripts --no-cache }
Step "Python tests (pytest)"       { & $uv run --frozen --extra dev pytest -p no:cacheprovider -q }
Step "Front end typecheck + build" { Push-Location web; & npm.cmd run build; $c = $LASTEXITCODE; Pop-Location; $global:LASTEXITCODE = $c }

if ($failed.Count) { Write-Host "`nFailed: $($failed -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "`nAll checks passed." -ForegroundColor Green
