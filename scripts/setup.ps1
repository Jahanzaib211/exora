# Exora one-time setup for Windows: dependencies, web build, .env with a random secret, first admin.
# Safe to re-run (it skips what is already done).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -Dev       # also install test/lint tools
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoAdmin   # skip the first-admin prompt
param([switch]$Dev, [switch]$NoAdmin)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# 1. uv: installs Python 3.12 and every Python dependency from uv.lock (no system Python needed).
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    $candidate = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $candidate) { $uv = $candidate }
}
if (-not $uv) {
    Info "Installing uv (Python package manager, https://docs.astral.sh/uv/)"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (-not (Test-Path $uv)) { Fail "uv did not install. Install it from https://docs.astral.sh/uv/ and re-run." }
}

# 2. Node.js builds the web app once; it is not needed to run Exora afterwards.
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fail "Node.js 24 is needed to build the web app. Install it from https://nodejs.org (or: winget install OpenJS.NodeJS.LTS), open a new terminal and re-run."
}
$nodeVersion = (& node --version).Trim()
$nodeMajor = [int]($nodeVersion.TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 24) { Fail "Node.js 24 or newer is needed (found $nodeVersion)." }
if ($nodeMajor -ne 24) { Write-Host "Note: Exora is tested on Node.js 24; found $nodeVersion." -ForegroundColor Yellow }

# 3. Python environment (.venv) from the lockfile.
Info "Installing Python dependencies"
$syncArgs = @("sync", "--frozen")
if ($Dev) { $syncArgs += @("--extra", "dev") }
& $uv @syncArgs
if ($LASTEXITCODE -ne 0) { Fail "uv sync failed." }

# 4. Web app -> web/dist (served by the Python app). npm.cmd, because execution policy often blocks npm.ps1.
Info "Building the web app"
Push-Location web
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Fail "npm ci failed." }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Fail "Web build failed." }
} finally { Pop-Location }

# 5. .env, database and the first admin.
Info "Configuring Exora"
$initArgs = @("run", "--frozen", "prospector", "init")
if ($NoAdmin) { $initArgs += "--no-admin" }
& $uv @initArgs
if ($LASTEXITCODE -ne 0) { Fail "prospector init failed." }

Write-Host ""
Write-Host "Exora is ready. Start it with:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File scripts\start.ps1"
Write-Host "then open http://127.0.0.1:8090"
