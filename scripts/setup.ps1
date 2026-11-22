# One-time setup on Windows: Python environment, packages, frontend build, .env
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# python, pip and npm report failure only through their exit code, which
# Windows PowerShell does not turn into an error, so every call is checked.
function Assert-Success([string]$step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$step failed (exit code $LASTEXITCODE). The reason is in the output above."
    }
}

# Vite and its bundler need Node.js 20.19+ or 22.12+.
function Assert-NodeVersion {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        throw "Node.js was not found. Install the LTS version from https://nodejs.org, open a new PowerShell window and run this script again."
    }
    $raw = (& node --version | Out-String).Trim()
    $v = [version]($raw.TrimStart("v").Split("-")[0])
    $supported = ($v.Major -eq 20 -and $v.Minor -ge 19) -or ($v.Major -eq 22 -and $v.Minor -ge 12) -or ($v.Major -ge 23)
    if (-not $supported) {
        throw "Node.js $raw is too old for the frontend, which needs 22.12 or newer. Install the LTS version from https://nodejs.org, open a new PowerShell window and run this script again."
    }
    Write-Host "Node.js $raw"
}

$startLocation = Get-Location
$failed = $false
try {
    Assert-NodeVersion

    Write-Host "== Backend ==" -ForegroundColor Cyan
    Set-Location "$root\backend"
    if (-not (Test-Path ".venv")) {
        python -m venv .venv
        Assert-Success "Creating the Python environment"
    }
    & .\.venv\Scripts\python.exe -m pip install --upgrade pip
    Assert-Success "Upgrading pip"
    & .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    Assert-Success "Installing the Python packages"
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "Created backend\.env: set COCKPIT_TOKEN and, for the AI voice, OPENAI_API_KEY" -ForegroundColor Yellow
    }

    Write-Host "== Frontend ==" -ForegroundColor Cyan
    Set-Location "$root\frontend"
    npm install
    Assert-Success "npm install"
    npm run build
    Assert-Success "Building the frontend"

    Write-Host "Setup done. Start the cockpit with .\scripts\run.ps1" -ForegroundColor Green
}
catch {
    $failed = $true
    Write-Host ""
    Write-Host "Setup stopped: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    Set-Location $startLocation
}
if ($failed) { exit 1 }
