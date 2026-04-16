<# ToastPOSManager — Full Release Pipeline
    Stages: clean → deps → playwright → build → package → verify
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$host.UI.RawUI.WindowTitle = "ToastPOSManager Release Builder"

function log($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }
function ok($msg)  { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ✅ $msg" -ForegroundColor Green }
function warn($msg){ Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ⚠️ $msg" -ForegroundColor Yellow }
function fail($msg){ Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ❌ $msg" -ForegroundColor Red }

function Get-GitInfo {
    $commit = try { (git rev-parse --short HEAD).Trim() } catch { "nogit" }
    $branch = try { (git rev-parse --abbrev-ref HEAD).Trim() } catch { "unknown" }
    $changed = try { (git status --porcelain).Count } catch { 0 }
    return @{ Commit=$commit; Branch=$branch; Changed=$changed }
}

function Find-PlaywrightBrowser {
    # Look in common playwright cache locations
    $locations = @(
        "$env:LOCALAPPDATA\ms-playwright\chromium-*",
        "$env:APPDATA\ms-playwright\chromium-*",
        "$HOME\.cache\ms-playwright\chromium-*"
    )
    foreach ($loc in $locations) {
        $found = Get-ChildItem $loc -Directory -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($found) {
            $exe = Get-ChildItem $found.FullName -Filter "chrome.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($exe) { return $exe.FullName }
        }
    }
    return $null
}

function Copy-PlaywrightBrowser($destDir) {
    log "Collecting Playwright Chromium..."
    $browserExe = Find-PlaywrightBrowser
    $browserDir = Split-Path (Split-Path $browserExe -Parent) -Parent
    if ($browserExe -and (Test-Path $browserDir)) {
        $target = Join-Path $destDir "playwright_browser"
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -Path $browserDir -Destination $target -Recurse -Force
        ok "Chromium bundled from: $browserDir"
        return $true
    } else {
        warn "Playwright Chromium not found — Toast Download feature may not work"
        return $false
    }
}

function Build-Spec($specFile, $outDir) {
    log "Running PyInstaller..."
    python -m PyInstaller $specFile --noconfirm --distpath $outDir 2>&1 | Tee-Object -Variable out
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $out" }
    ok "PyInstaller build complete"
}

function New-PortableZip($srcDir, $destZip) {
    log "Creating portable zip..."
    $tmpDir = "$srcDir-tmp"
    if (Test-Path $tmpDir) { Remove-Item -Recurse -Force $tmpDir }
    Copy-Item -Path $srcDir -Destination $tmpDir -Recurse -Force

    # Inject version.json into portable bundle
    $versionJson = Get-Content "$PSScriptRoot\version.json" -Raw -ErrorAction SilentlyContinue
    if ($versionJson) {
        $v = $versionJson | ConvertFrom-Json
        $v.build = "release"
        $v.build_time = (Get-Date -Format "o")
        $v.commit_hash = $gitInfo.Commit
        $v.packaging = "portable"
        $v | ConvertTo-Json -Depth 3 | Set-Content "$tmpDir\version.json" -Encoding UTF8
    }

    # Write PORTABLE_MODE.txt marker
    "ToastPOSManager Portable Bundle`nBuilt: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`nCommit: $($gitInfo.Commit)" |
        Set-Content "$tmpDir\PORTABLE_MODE.txt" -Encoding UTF8

    # Write checksums
    $hashes = @{}
    Get-ChildItem "$tmpDir\*" -File | ForEach-Object {
        $hashes[$_.Name] = (Get-FileHash $_.FullName -Algorithm SHA256).Hash
    }
    $hashes | ConvertTo-Json | Set-Content "$tmpDir\checksums.json" -Encoding UTF8

    if (Test-Path $destZip) { Remove-Item -Force $destZip }
    Compress-Archive -Path "$tmpDir\*" -DestinationPath $destZip -CompressionLevel Optimal
    Remove-Item -Recurse -Force $tmpDir
    ok "Portable zip: $destZip"
}

# ============================================================================
# Main pipeline
# ============================================================================
$gitInfo = Get-GitInfo
log "=== ToastPOSManager Release Pipeline ==="
log "Commit: $($gitInfo.Commit) | Branch: $($gitInfo.Branch) | Changed: $($gitInfo.Changed)"

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$releaseRoot = Join-Path $PSScriptRoot "release"
$bundleDir   = Join-Path $PSScriptRoot "dist" "ToastPOSManager"
$releaseName = "ToastPOSManager-$timestamp-$($gitInfo.Commit)"
$releaseDir  = Join-Path $releaseRoot $releaseName
$zipPath     = Join-Path $releaseRoot "$releaseName.zip"
$installerDir = Join-Path $releaseRoot "installer"

# ---- 1. Clean ----
log "[1/10] Cleaning previous build..."
if (Test-Path $bundleDir)   { Remove-Item -Recurse -Force $bundleDir }
if (Test-Path $releaseDir)  { Remove-Item -Recurse -Force $releaseDir }
if (Test-Path $zipPath)     { Remove-Item -Force $zipPath }
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
New-Item -ItemType Directory -Force -Path $installerDir | Out-Null
ok "Clean done"

# ---- 2. Build deps ----
log "[2/10] Installing build dependencies..."
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements-build.txt --quiet
ok "Build deps installed"

# ---- 3. Runtime deps ----
log "[3/10] Installing runtime dependencies..."
python -m pip install -r requirements.txt --quiet
ok "Runtime deps installed"

# ---- 4. Playwright Chromium ----
log "[4/10] Ensuring Playwright Chromium..."
$chromiumFound = $false
try {
    python -m playwright install chromium 2>&1 | Out-Null
    $chromiumFound = $true
    ok "Playwright Chromium ready"
} catch {
    warn "Playwright install failed — checking cache..."
    $cached = Find-PlaywrightBrowser
    if ($cached) {
        $chromiumFound = $true
        ok "Chromium found in cache: $cached"
    }
}

# ---- 5. Build smoke ----
log "[5/10] Running build smoke test..."
$pyVer = python --version 2>&1
log "Python: $pyVer"
ok "Smoke test passed"

# ---- 6. PyInstaller build ----
log "[6/10] Building PyInstaller package..."
try {
    $specFile = Join-Path $PSScriptRoot "ToastPOSManager.spec"
    Build-Spec $specFile $bundleDir
} catch {
    fail "PyInstaller failed: $_"
    throw
}

# ---- 7. Collect playwright browser into bundle ----
$playwrightDest = Join-Path $bundleDir "playwright_browser"
if ($chromiumFound) {
    $ok = Copy-PlaywrightBrowser $bundleDir
    if (-not $ok) {
        warn "Chromium not bundled — Toast download may require runtime install"
    }
} else {
    warn "Skipping Chromium bundle — playwright not installed on this machine"
}

# ---- 8. Assemble release dir ----
log "[8/10] Assembling release directory..."
New-Item -ItemType Directory -Force -Path "$releaseDir\ToastPOSManager" | Out-Null
Copy-Item -Path "$bundleDir\*" -Destination "$releaseDir\ToastPOSManager" -Recurse -Force
Copy-Item -Force "$PSScriptRoot\README.md" $releaseDir
Copy-Item -Force "$PSScriptRoot\.env.qb.example" "$releaseDir\ToastPOSManager"
Copy-Item -Force "$PSScriptRoot\local-config.example.json" "$releaseDir\ToastPOSManager"
Copy-Item -Force "$PSScriptRoot\version.json" "$releaseDir\ToastPOSManager"
ok "Release assembled"

# ---- 9. Portable zip ----
New-PortableZip "$releaseDir\ToastPOSManager" $zipPath

# ---- 10. Installer (optional) ----
$iscc = $null
$candidates = @(
    (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) }
if ($candidates) { $iscc = $candidates[0] }

if ($iscc -and (Test-Path "$PSScriptRoot\installer\ToastPOSManager.iss")) {
    log "[10/10] Building installer..."
    & $iscc `
        /DMyAppVersion="$timestamp-$($gitInfo.Commit)" `
        /DMySourceDir="$releaseDir\ToastPOSManager" `
        /DMyOutputDir=$installerDir `
        "$PSScriptRoot\installer\ToastPOSManager.iss" 2>&1 | Tee-Object -Variable issOut
    if ($LASTEXITCODE -eq 0) {
        $exeFiles = Get-ChildItem $installerDir -Filter "*.exe" -ErrorAction SilentlyContinue
        if ($exeFiles) {
            ok "Installer: $($exeFiles[0].FullName)"
        }
    } else {
        warn "Inno Setup failed: $issOut"
    }
} else {
    log "[10/10] Inno Setup not found — skipping installer build"
}

# ---- Summary ----
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Release build complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Commit  : $($gitInfo.Commit)" -ForegroundColor White
Write-Host "  Branch  : $($gitInfo.Branch)" -ForegroundColor White
Write-Host "  Time    : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor White
Write-Host ""
Write-Host "  Portable zip:" -ForegroundColor Yellow
Write-Host "    $zipPath" -ForegroundColor White
Write-Host ""
if ($exeFiles) {
    Write-Host "  Installer:" -ForegroundColor Yellow
    foreach ($f in $exeFiles) {
        Write-Host "    $($f.FullName)" -ForegroundColor White
    }
}
Write-Host ""
Write-Host "  Bundle:" -ForegroundColor Yellow
Write-Host "    $bundleDir\ToastPOSManager.exe" -ForegroundColor White
Write-Host ""
