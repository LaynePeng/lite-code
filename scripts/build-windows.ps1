# lite-code Windows packaging script (PowerShell): frontend build -> PyInstaller backend -> NSIS installer
# Usage: .\scripts\build-windows.ps1 [-Insecure]
#   -Insecure  Skip TLS certificate validation (only for intranet/self-signed cert environments)
#
# Prerequisites:
#   - Python 3.11+ (available as `python` on PATH)
#   - Node.js 18+
#   - First run needs network to download the Electron binary (npmmirror mirror is used by default in CN)
param([switch]$Insecure)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Restricted network: default to CN mirrors (overridable via environment variables)
if (-not $env:ELECTRON_MIRROR) { $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/" }
if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) { $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/" }

if ($Insecure) {
    Write-Host "==> Non-strict TLS validation enabled (intranet/self-signed only)"
    $env:NODE_TLS_REJECT_UNAUTHORIZED = "0"
    $env:npm_config_strict_ssl = "false"
}

function Invoke-Step([string]$Name, [scriptblock]$Block) {
    Write-Host "==> $Name"
    $global:LASTEXITCODE = 0
    # PS 5.1 下原生命令的 stderr 在 ErrorActionPreference=Stop 时会变成致命错误
    # （例如 pip 的升级提示），这里仅在步骤内临时降级，成功与否只看退出码
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $Block
    } finally {
        $ErrorActionPreference = $prevEap
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! $Name failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ------------------------------------------------------------ Python environment

$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"

Invoke-Step "Prepare Python virtual environment" {
    if (-not (Test-Path $venvPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Failed to create venv. Please install Python 3.11+" }
    }
}

Invoke-Step "Install Python dependencies (.venv\Scripts\pip install -e .[dev,package])" {
    & $venvPython -m pip install -e ".[dev,package]"
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# ------------------------------------------------------------ Frontend dependencies

if (-not (Test-Path node_modules)) {
    Invoke-Step "Install root dependencies (npm install)" {
        npm install
    }
}

if (-not (Test-Path web\node_modules)) {
    Invoke-Step "Install frontend dependencies (npm --prefix web install)" {
        npm --prefix web install
    }
}

Invoke-Step "Build frontend (npm run build:web)" {
    npm run build:web
}

# ------------------------------------------------------------ Backend binary

Invoke-Step "Package Python backend (PyInstaller)" {
    node scripts/package-backend.mjs
}

# ------------------------------------------------------------ electron-builder NSIS

Invoke-Step "Package Windows installer (electron-builder --win nsis)" {
    npx electron-builder --win nsis --publish never
}

Write-Host "==> Done:"
Get-ChildItem release -Filter *.exe | ForEach-Object {
    Write-Host ("    " + $_.Name + " (" + [math]::Round($_.Length / 1MB, 1) + " MB)")
}