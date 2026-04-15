$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Get-GitCommit {
    try {
        return (git -C $PSScriptRoot rev-parse --short HEAD).Trim()
    } catch {
        return "nogit"
    }
}

function Get-InnoCompiler {
    $candidates = @(
        (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }
    return $null
}

Write-Host "[1/5] Installing build dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements-build.txt

Write-Host "[2/5] Ensuring runtime dependencies are present..."
python -m pip install -r requirements.txt

Write-Host "[3/5] Ensuring Playwright Chromium is installed..."
python -m playwright install chromium

Write-Host "[4/5] Building ToastPOSManager..."
pyinstaller ToastPOSManager.spec --noconfirm

Write-Host "[5/5] Creating release bundle..."
$commit = Get-GitCommit
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$releaseRoot = Join-Path $PSScriptRoot "release"
$releaseName = "ToastPOSManager-$timestamp-$commit"
$releaseDir = Join-Path $releaseRoot $releaseName
$bundleDir = Join-Path $PSScriptRoot "dist\ToastPOSManager"
$zipPath = Join-Path $releaseRoot "$releaseName.zip"

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
if (Test-Path $releaseDir) {
    Remove-Item -Recurse -Force $releaseDir
}
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

Copy-Item -Recurse -Force $bundleDir (Join-Path $releaseDir "ToastPOSManager")
Copy-Item -Force "$PSScriptRoot\README.md" $releaseDir
Copy-Item -Force "$PSScriptRoot\.env.qb.example" $releaseDir
Copy-Item -Force "$PSScriptRoot\local-config.example.json" $releaseDir

if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}
Compress-Archive -Path "$releaseDir\*" -DestinationPath $zipPath

$iscc = Get-InnoCompiler
if ($iscc -and (Test-Path "$PSScriptRoot\installer\ToastPOSManager.iss")) {
    Write-Host "Building installer with Inno Setup..."
    & $iscc `
        "/DMyAppVersion=$timestamp-$commit" `
        "/DMySourceDir=$bundleDir" `
        "/DMyOutputDir=$releaseRoot" `
        "$PSScriptRoot\installer\ToastPOSManager.iss"
} else {
    Write-Host "Inno Setup compiler not found. Skipping installer build."
}

Write-Host ""
Write-Host "Build complete:"
Write-Host "  $PSScriptRoot\dist\ToastPOSManager\ToastPOSManager.exe"
Write-Host "Release zip:"
Write-Host "  $zipPath"
