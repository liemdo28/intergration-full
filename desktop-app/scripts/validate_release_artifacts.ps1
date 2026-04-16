<# ToastPOSManager — Release Artifact Validator
    Validates all build outputs before declaring a release ready.
    Returns exit code 0 on all pass, 1 on any FAIL.
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)

function log($msg)    { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }
function ok($msg)      { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ✅ $msg" -ForegroundColor Green }
function warn($msg)   { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ⚠️ $msg" -ForegroundColor Yellow }
function fail($msg)   { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ❌ $msg" -ForegroundColor Red }

$results = @{ Passed = 0; Failed = 0; Warnings = 0 }

# ── Resolve paths ─────────────────────────────────────────────────────────────
$scriptRoot = $PWD
$distDir    = Join-Path $scriptRoot "dist"
$releaseDir = Join-Path $scriptRoot "release"
$specFile   = Join-Path $scriptRoot "ToastPOSManager.spec"
$bundleDir  = Join-Path $distDir "ToastPOSManager"

$zipFiles = Get-ChildItem $releaseDir -Filter "ToastPOSManager-*.zip" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
$installerFiles = Get-ChildItem (Join-Path $releaseDir "installer") -Filter "*.exe" -ErrorAction SilentlyContinue

# ─────────────────────────────────────────────────────────────────────────────
# Helper: assert / warn
# ─────────────────────────────────────────────────────────────────────────────
function assert($condition, $label) {
    if ($condition) { ok $label; $script:results.Passed++; return $true }
    else            { fail $label; $script:results.Failed++; return $false }
}

# ─────────────────────────────────────────────────────────────────────────────
# 1. Portable ZIP exists
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [1/7] Portable ZIP" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

assert ($zipFiles)                "Portable ZIP exists"
assert ($zipFiles.Exists)        "Portable ZIP is readable"
log "  ZIP: $($zipFiles.FullName)"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Validate ZIP contents
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [2/7] ZIP Contents" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

$tmpExtract = "$env:TEMP\toast_zip_validate_$PID"
New-Item -ItemType Directory -Force -Path $tmpExtract | Out-Null

try {
    Microsoft.PowerShell.Archive\Expand-Archive -Path $zipFiles.FullName -DestinationPath $tmpExtract -Force
    $extractDir = Get-ChildItem $tmpExtract -Directory | Select-Object -First 1

    assert (Test-Path (Join-Path $extractDir "ToastPOSManager.exe"))             "EXE in ZIP"
    assert (Test-Path (Join-Path $extractDir "version.json"))                   "version.json in ZIP"
    assert (Test-Path (Join-Path $extractDir "bootstrap_runtime.pyc"))          "bootstrap_runtime bundled"
    assert (Test-Path (Join-Path $extractDir "app.pyc"))                       "app bundled"
    assert (Test-Path (Join-Path $extractDir ".env.qb.example"))                ".env.qb.example in ZIP"
    assert (Test-Path (Join-Path $extractDir "local-config.example.json"))      "local-config.example.json in ZIP"
    assert (Test-Path (Join-Path $extractDir "PORTABLE_MODE.txt"))             "PORTABLE_MODE.txt in ZIP"
    assert (Test-Path (Join-Path $extractDir "checksums.json"))                 "checksums.json in ZIP"

    $pwBrowser = Test-Path (Join-Path $extractDir "playwright_browser")
    if ($pwBrowser) {
        ok "playwright_browser/ in ZIP (Chromium bundled)"
        $results.Passed++
    } else {
        warn "playwright_browser/ NOT in ZIP — Download Reports may not work without runtime install"
        $results.Warnings++
    }
} finally {
    if (Test-Path $tmpExtract) { Remove-Item -Recurse -Force $tmpExtract -ErrorAction SilentlyContinue }
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Version metadata
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [3/7] Version Metadata" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

$vjPath = Join-Path $bundleDir "version.json"
if (Test-Path $vjPath) {
    try {
        $vj = Get-Content $vjPath -Raw | ConvertFrom-Json
        ok "version.json parsed"
        $results.Passed++

        assert ($vj.app_version -is [string] -and $vj.app_version.Length -gt 0)  "app_version is non-empty string"
        assert ($vj.build -match "^(release|dev)$")                              "build is 'release' or 'dev'"
        assert ($null -ne $vj.chromium_bundled)                                   "chromium_bundled field present"
        assert ($vj.build_time -match "^\d{4}-\d{2}-\d{2}")                     "build_time is ISO 8601"

        log "  app_version    : $($vj.app_version)"
        log "  build          : $($vj.build)"
        log "  chromium_bundled: $($vj.chromium_bundled)"
        log "  build_time     : $($vj.build_time)"
    } catch {
        fail "version.json parse failed: $_"
        $results.Failed++
    }
} else {
    fail "version.json not found in bundle: $vjPath"
    $results.Failed++
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. Checksums valid
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [4/7] Checksums" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

$tmpExtract2 = "$env:TEMP\toast_checksums_$PID"
New-Item -ItemType Directory -Force -Path $tmpExtract2 | Out-Null

try {
    Microsoft.PowerShell.Archive\Expand-Archive -Path $zipFiles.FullName -DestinationPath $tmpExtract2 -Force
    $extractDir2 = Get-ChildItem $tmpExtract2 -Directory | Select-Object -First 1
    $checksumFile = Join-Path $extractDir2 "checksums.json"

    if (Test-Path $checksumFile) {
        $checksums = Get-Content $checksumFile -Raw | ConvertFrom-Json
        $csErrors = 0
        foreach ($relPath in $checksums.PSObject.Properties.Name) {
            $filePath = Join-Path $extractDir2 $relPath
            if (Test-Path $filePath) {
                $actual = (Get-FileHash $filePath -Algorithm SHA256).Hash.ToLower()
                if ($actual -ne $checksums.$relPath.ToLower()) {
                    fail "Checksum mismatch: $relPath"
                    $csErrors++
                }
            }
        }
        if ($csErrors -eq 0) {
            ok "All checksums match ($($checksums.PSObject.Properties.Count) files)"
            $results.Passed++
        } else {
            fail "$csErrors checksum mismatch(es)"
            $results.Failed++
        }
    } else {
        warn "checksums.json not in extracted ZIP — cannot verify"
        $results.Warnings++
    }
} finally {
    if (Test-Path $tmpExtract2) { Remove-Item -Recurse -Force $tmpExtract2 -ErrorAction SilentlyContinue }
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. Spec file
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [5/7] PyInstaller Spec File" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

if (Test-Path $specFile) {
    $specContent = Get-Content $specFile -Raw
    assert ($specContent -match 'launcher\.py')                 "launcher.py is the entry point in spec"
    assert ($specContent -match 'playwright')                   "playwright hidden import present"
    assert ($specContent -match 'customtkinter')                 "customtkinter hidden import present"
    assert ($specContent -match 'tkcalendar')                   "tkcalendar hidden import present"
    assert ($specContent -match 'version\.json')                "version.json in spec datas"
} else {
    warn "ToastPOSManager.spec not found — may be outside desktop-app root"
    $results.Warnings++
}

# ─────────────────────────────────────────────────────────────────────────────
# 6. Bundle directory
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [6/7] Bundle Directory" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

assert (Test-Path $bundleDir)                           "dist/ToastPOSManager/ bundle dir exists"
assert (Test-Path (Join-Path $bundleDir "ToastPOSManager.exe")) "ToastPOSManager.exe in bundle"

$pycFiles = @(Get-ChildItem $bundleDir -Filter "*.pyc" -Recurse | Where-Object { $_.Name -notmatch "^bootstrap_runtime\.pyc" })
if ($pycFiles.Count -gt 0) {
    ok "PYC files bundled ($($pycFiles.Count) files)"
    $results.Passed++
} else {
    warn "No .pyc files found in bundle — may indicate incomplete build"
    $results.Warnings++
}

# ─────────────────────────────────────────────────────────────────────────────
# 7. Installer (optional)
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  [7/7] Installer EXE (optional)" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta

if ($installerFiles -and $installerFiles.Count -gt 0) {
    foreach ($exe in $installerFiles) {
        ok "Installer found: $($exe.Name)"
        $results.Passed++
    }
} else {
    warn "No installer EXE in release/installer/ — Inno Setup not found or build skipped"
    $results.Warnings++
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Artifact Validation Summary" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Passed   : $($results.Passed)" -ForegroundColor Green
Write-Host "  Warnings : $($results.Warnings)" -ForegroundColor Yellow
Write-Host "  Failed   : $($results.Failed)" -ForegroundColor Red
Write-Host ""

if ($results.Failed -gt 0) {
    Write-Host "RESULT: ARTIFACT VALIDATION FAILED" -ForegroundColor Red
    exit 1
} elseif ($results.Warnings -gt 0) {
    Write-Host "RESULT: VALIDATION PASSED WITH WARNINGS" -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "RESULT: ALL ARTIFACTS VALIDATED" -ForegroundColor Green
    exit 0
}
