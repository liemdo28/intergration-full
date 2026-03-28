$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

Write-Host "[1/4] Installing build dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

Write-Host "[2/4] Ensuring runtime dependencies are present..."
python -m pip install -r requirements.txt

Write-Host "[3/4] Ensuring Playwright Chromium is installed..."
python -m playwright install chromium

Write-Host "[4/4] Building ToastPOSManager..."
pyinstaller ToastPOSManager.spec --noconfirm

Write-Host ""
Write-Host "Build complete:"
Write-Host "  $PSScriptRoot\dist\ToastPOSManager\ToastPOSManager.exe"
