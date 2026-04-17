#Requires -Version 5.1
<#
.SYNOPSIS
    ToastPOSManager — Built-EXE Smoke Test
.DESCRIPTION
    Runs against dist/ToastPOSManager/ToastPOSManager.exe (the PyInstaller build artifact).
    Stages: verify artifacts → launch → check startup → check logs → teardown.
    Returns exit code 0 on all-pass, 1 on any FAIL.
.NOTES
    Script root is resolved via $PSScriptRoot so it is safe to run from any cwd.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ─────────────────────────────────────────────────────────────────────────────
# Helper: coloured pass / fail markers
# ─────────────────────────────────────────────────────────────────────────────
function Write-Pass {
    param([string]$Message)
    Write-Host "  [PASS] $Message" -ForegroundColor Green
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
}

function Write-Info {
    param([string]$Message)
    Write-Host "  [INFO] $Message" -ForegroundColor Cyan
}

# ─────────────────────────────────────────────────────────────────────────────
# 1/5  Get-SmokeConfig  — return a hashtable of resolved paths
# ─────────────────────────────────────────────────────────────────────────────
function Get-SmokeConfig {
    $scriptRoot = $PSScriptRoot
    $bundleDir  = Join-Path $scriptRoot 'dist' 'ToastPOSManager'

    return @{
        ExePath     = Join-Path $bundleDir 'ToastPOSManager.exe'
        BundleDir   = $bundleDir
        LogDir      = Join-Path $bundleDir 'logs'
        ReportPath  = Join-Path $bundleDir 'bootstrap_report.json'
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2/5  Test-BundleArtifacts  — check every expected file is present in the
#     unpacked bundle (dist/ToastPOSManager/)
# ─────────────────────────────────────────────────────────────────────────────
function Test-BundleArtifacts {
    param([string]$BundleDir, [string]$ExePath)

    $allPassed = $true
    Write-Host "  Checking bundle: $BundleDir" -ForegroundColor Gray

    # Must-exist checks
    foreach ($item in @($ExePath, (Join-Path $BundleDir 'version.json'))) {
        if (Test-Path $item -PathType Leaf) {
            Write-Pass "Exists: $(Split-Path $item -Leaf)"
        } else {
            Write-Fail "Missing: $(Split-Path $item -Leaf)"
            $allPassed = $false
        }
    }

    # Py-compiled Python files (launcher + app)
    $launcherPyc = Get-ChildItem -Path $BundleDir -Filter 'launcher*.pyc' -File -ErrorAction SilentlyContinue
    if ($launcherPyc) {
        Write-Pass "Found launcher: $($launcherPyc.Name)"
    } else {
        Write-Fail "Missing: launcher*.pyc in bundle"
        $allPassed = $false
    }

    $appPyc = Get-ChildItem -Path $BundleDir -Filter 'app.pyc' -File -ErrorAction SilentlyContinue
    if ($appPyc) {
        Write-Pass "Found app.pyc"
    } else {
        Write-Fail "Missing: app.pyc in bundle"
        $allPassed = $false
    }

    # PORTABLE_MODE.txt must NOT be in the bundle (it belongs in the ZIP only)
    $portableMarker = Join-Path $BundleDir 'PORTABLE_MODE.txt'
    if (Test-Path $portableMarker -PathType Leaf) {
        Write-Warn "PORTABLE_MODE.txt found in bundle (should only be in the ZIP)"
        $allPassed = $false   # treat as failure since spec is clear
    } else {
        Write-Pass "PORTABLE_MODE.txt correctly absent from bundle"
    }

    return $allPassed
}

# ─────────────────────────────────────────────────────────────────────────────
# 3/5  Invoke-AppSmokeTest  — launch the EXE with --safe, observe behaviour
# ─────────────────────────────────────────────────────────────────────────────
function Invoke-AppSmokeTest {
    param(
        [string]$ExePath,
        [string]$BundleDir,
        [int]$TimeoutSec = 10
    )

    # Temporary files created in the system temp folder so they are cleaned
    # up regardless of where the script is invoked from.
    $tmpDir   = [System.IO.Path]::GetTempPath()
    $errLog   = Join-Path $tmpDir "toast_bootstrap_err_$PID.log"
    $outLog   = Join-Path $tmpDir "toast_bootstrap_out_$PID.log"

    Write-Host "  Launching: $ExePath --safe" -ForegroundColor Gray
    Write-Host "  Timeout: ${TimeoutSec}s  stderr → $errLog" -ForegroundColor Gray

    try {
        $proc = Start-Process `
            -FilePath         $ExePath `
            -ArgumentList     '--safe' `
            -PassThru `
            -RedirectStandardError $errLog `
            -RedirectStandardOutput $outLog `
            -WindowStyle Hidden

        Write-Info "Process started with PID $($proc.Id)"

        $timedOut = $false
        try {
            $didExit = $proc.WaitForExit(([int]$TimeoutSec * 1000))
            if (-not $didExit) {
                Write-Info "Process still running after ${TimeoutSec}s — stable"
                $timedOut = $false
            }
        } catch {
            Write-Warn "WaitForExit threw: $_"
        }

        # Read exit state
        $exitCode  = $proc.ExitCode
        $hasExited = $proc.HasExited

        Write-Info "HasExited=$hasExited  ExitCode=$exitCode"

        # Examine stderr for [BOOTSTRAP] markers
        $errLines  = @()
        $hasBootstrap = $false
        if (Test-Path $errLog -PathType Leaf) {
            $errLines = Get-Content $errLog -Raw -ErrorAction SilentlyContinue -Encoding UTF8
            if ($errLines -match '\[BOOTSTRAP\]') {
                $hasBootstrap = $true
                Write-Info "[BOOTSTRAP] marker found in stderr"
            }
        }

        # Assess health
        if ($hasExited -and $exitCode -eq 0) {
            Write-Pass "Process exited cleanly (code 0)"
        } elseif ($hasExited -and $exitCode -eq 1) {
            Write-Warn "Process exited with code 1 — bootstrap blocked (acceptable)"
        } elseif (-not $hasExited) {
            Write-Pass "Process still running — stable"
        } else {
            Write-Warn "Process exited with unexpected code ${exitCode}"
        }

        # Dump first few stderr lines for diagnostics
        if ($errLines.Count -gt 0) {
            Write-Host "  --- stderr excerpt ---" -ForegroundColor Gray
            $errLines -split "`n" | Select-Object -First 10 | ForEach-Object {
                Write-Host "    $_" -ForegroundColor DarkGray
            }
            Write-Host "  --- end stderr ---" -ForegroundColor Gray
        }

        $healthy = ($exitCode -le 1)   # 0 or 1 are both acceptable
        return $healthy

    } finally {
        # Always clean up: stop the process and remove temp files
        if ($proc -and -not $proc.HasExited) {
            Write-Info "Stopping process $($proc.Id)"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }

        Remove-Item $errLog -Force -ErrorAction SilentlyContinue
        Remove-Item $outLog -Force -ErrorAction SilentlyContinue
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 4/5  Test-BootstrapReport  — parse bootstrap_report.json if present
# ─────────────────────────────────────────────────────────────────────────────
function Test-BootstrapReport {
    param([string]$ReportPath)

    if (-not (Test-Path $ReportPath -PathType Leaf)) {
        Write-Fail "bootstrap_report.json not found at: $ReportPath"
        return $false
    }

    Write-Info "Parsing: $ReportPath"

    try {
        $json = Get-Content $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Fail "Failed to parse JSON: $_"
        return $false
    }

    $canRun        = $json.can_run        -eq $true
    $isFirstRun    = $json.is_first_run   -eq $true
    $portableMode  = $json.portable_mode  -eq $true
    $blockerCount  = 0
    if ($json.blockers -is [array]) { $blockerCount = $json.blockers.Count }

    Write-Info "can_run       : $canRun"
    Write-Info "is_first_run   : $isFirstRun"
    Write-Info "portable_mode  : $portableMode"
    Write-Info "blocker_count  : $blockerCount"

    if ($json.blockers -is [array] -and $blockerCount -gt 0) {
        Write-Host "  Blockers:" -ForegroundColor Yellow
        $json.blockers | ForEach-Object {
            Write-Host "    - $_" -ForegroundColor Yellow
        }
    }

    # Pass if can_run is true, OR if there are blockers but the process didn't crash
    if ($canRun) {
        Write-Pass "can_run = true"
        return $true
    } elseif ($blockerCount -gt 0) {
        Write-Warn "can_run = false but blockers present (bootstrap correctly rejected)"
        return $true
    } else {
        Write-Fail "can_run = false with no blockers — unexpected state"
        return $false
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 5a/5  Test-RuntimeFolders  — verify auto-created directories appear
# ─────────────────────────────────────────────────────────────────────────────
function Test-RuntimeFolders {
    param([string]$BundleDir)

    $expectedFolders = @(
        'logs',
        'audit-logs',
        'toast-reports',
        'recovery-backups',
        'crash-reports'
    )

    $allPassed = $true
    foreach ($folder in $expectedFolders) {
        $path = Join-Path $BundleDir $folder
        if (Test-Path $path -PathType Container) {
            Write-Pass "$folder/"
        } else {
            Write-Fail "$folder/ missing (not auto-created)"
            $allPassed = $false
        }
    }

    return $allPassed
}

# ─────────────────────────────────────────────────────────────────────────────
# 5b/5  Test-BootstrapLog  — find and scan the most recent bootstrap_*.log
# ─────────────────────────────────────────────────────────────────────────────
function Test-BootstrapLog {
    param([string]$LogDir)

    if (-not (Test-Path $LogDir -PathType Container)) {
        Write-Warn "logs/ directory not yet created"
        return $false
    }

    $logFiles = Get-ChildItem -Path $LogDir -Filter 'bootstrap_*.log' -File -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending

    if ($logFiles.Count -eq 0) {
        Write-Warn "No bootstrap_*.log found in logs/"
        return $false
    }

    $latestLog = $logFiles[0]
    Write-Info "Scanning: $($latestLog.Name)"

    $content = Get-Content $latestLog.FullName -Raw -Encoding UTF8 -ErrorAction SilentlyContinue

    $hasBootstrapMarker = $content -match '\[BOOTSTRAP\]'
    $hasCanRun          = $content -match 'can_run\s*='
    $hasBlocker         = $content -match 'blocker'

    if ($hasBootstrapMarker) { Write-Pass "[BOOTSTRAP] marker present" } else { Write-Warn "[BOOTSTRAP] marker not found" }
    if ($hasCanRun)          { Write-Pass "can_run= line present"       } else { Write-Warn "can_run= line not found" }
    if ($hasBlocker)         { Write-Info "blocker text present"        } else { Write-Info "no blocker text" }

    # Print the first 20 lines for quick inspection
    Write-Host "  --- log excerpt (first 20 lines) ---" -ForegroundColor Gray
    Get-Content $latestLog.FullName -ErrorAction SilentlyContinue |
        Select-Object -First 20 |
        ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    Write-Host "  --- end log ---" -ForegroundColor Gray

    return ($hasBootstrapMarker -and ($hasCanRun -or $hasBlocker))
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN  —  orchestrate all five stages
# ─────────────────────────────────────────────────────────────────────────────
function Start-SmokeTest {
    $config = Get-SmokeConfig

    $passed  = 0
    $failed  = 0
    $warning = 0

    # ── Stage 1 ──────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " [1/5] Verify bundle artifacts" -ForegroundColor Magenta
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta

    if (Test-BundleArtifacts -BundleDir $config.BundleDir -ExePath $config.ExePath) {
        $passed++
    } else {
        $failed++
    }

    # ── Stage 2 ──────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " [2/5] Launch smoke test (--safe flag)" -ForegroundColor Magenta
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta

    $launchOk = Invoke-AppSmokeTest -ExePath $config.ExePath -BundleDir $config.BundleDir -TimeoutSec 10
    if ($launchOk) { $passed++ } else { $warning++; $failed++ }

    # ── Stage 3 ──────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " [3/5] Check bootstrap report" -ForegroundColor Magenta
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta

    if (Test-BootstrapReport -ReportPath $config.ReportPath) {
        $passed++
    } else {
        $failed++
    }

    # ── Stage 4 ──────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " [4/5] Check runtime folders" -ForegroundColor Magenta
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta

    if (Test-RuntimeFolders -BundleDir $config.BundleDir) {
        $passed++
    } else {
        $failed++
    }

    # ── Stage 5 ──────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " [5/5] Check bootstrap log" -ForegroundColor Magenta
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta

    if (Test-BootstrapLog -LogDir $config.LogDir) {
        $passed++
    } else {
        $warning++; $failed++
    }

    # ── Summary ──────────────────────────────────────────────────────────────
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host " SUMMARY" -ForegroundColor Magenta
    Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host "  Passed  : $passed" -ForegroundColor Green
    Write-Host "  Warnings: $warning" -ForegroundColor Yellow
    Write-Host "  Failed  : $failed" -ForegroundColor Red
    Write-Host ""

    if ($failed -gt 0) {
        Write-Host "RESULT: SMOKE TEST FAILED — investigate above failures." -ForegroundColor Red
        exit 1
    } else {
        Write-Host "RESULT: SMOKE TEST PASSED" -ForegroundColor Green
        exit 0
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# Entry point — resolve working directory and run
# ─────────────────────────────────────────────────────────────────────────────
$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path $MyInvocation.MyCommand.Path -Parent
}
Set-Location $scriptRoot
Write-Host "Script root: $scriptRoot" -ForegroundColor DarkGray

Start-SmokeTest
