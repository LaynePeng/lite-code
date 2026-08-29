# lite-code Windows 打包脚本 (PowerShell)：前端构建 → PyInstaller 后端 → NSIS 安装包
# 用法: .\scripts\build-windows.ps1 [-Insecure]
#   -Insecure  跳过 TLS 证书校验（仅建议内网/自签证书环境使用）
#
# 前置要求：
#   - Python 3.11+（已加入 PATH 或可用 `python` 命令）
#   - Node.js 18+
#   - 首次运行需联网下载 Electron 二进制（国内建议用默认 npmmirror 镜像）
param([switch]$Insecure)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# 网络受限环境：默认使用国内镜像（可用环境变量覆盖）
if (-not $env:ELECTRON_MIRROR) { $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/" }
if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) { $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/" }

if ($Insecure) {
    Write-Host "==> 已启用非严格 TLS 校验（仅建议内网/自签证书环境使用）"
    $env:NODE_TLS_REJECT_UNAUTHORIZED = "0"
    $env:npm_config_strict_ssl = "false"
}

function Invoke-Step([string]$Name, [scriptblock]$Block) {
    Write-Host "==> $Name"
    & $Block
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! $Name 失败 (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ------------------------------------------------------------ Python 环境

$venvPython = Join-Path $PWD ".venv\Scripts\python.exe"

Invoke-Step "准备 Python 虚拟环境" {
    if (-not (Test-Path $venvPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败，请确认已安装 Python 3.11+" }
    }
}

Invoke-Step "安装 Python 依赖 (.venv\Scripts\pip install -e .[dev,package])" {
    & $venvPython -m pip install -e ".[dev,package]"
    if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }
}

# ------------------------------------------------------------ 前端依赖

if (-not (Test-Path node_modules)) {
    Invoke-Step "安装根依赖 (npm install)" {
        npm install
    }
}

if (-not (Test-Path web\node_modules)) {
    Invoke-Step "安装前端依赖 (npm --prefix web install)" {
        npm --prefix web install
    }
}

Invoke-Step "构建前端 (npm run build:web)" {
    npm run build:web
}

# ------------------------------------------------------------ 后端二进制

Invoke-Step "打包 Python 后端 (PyInstaller)" {
    node scripts/package-backend.mjs
}

# ------------------------------------------------------------ electron-builder NSIS

Invoke-Step "打包 Windows 安装包 (electron-builder --win nsis)" {
    npx electron-builder --win nsis --publish never
}

Write-Host "==> 完成："
Get-ChildItem release -Filter *.exe | ForEach-Object {
    Write-Host ("    " + $_.Name + " (" + [math]::Round($_.Length / 1MB, 1) + " MB)")
}