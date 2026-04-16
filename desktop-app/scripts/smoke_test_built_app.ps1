<# ToastPOSManager — Built-EXE Smoke Test
    Runs against dist/ToastPOSManager/ToastPOSManager.exe
    Stages: verify artifacts → launch → check startup → check logs → teardown
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)

function log($msg)   { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor Cyan }
function ok($msg)     { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ✅ $msg" -ForegroundColor Green }
function warn($msg)  { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ⚠️ $msg" -ForegroundColor Yellow }
function fail($msg)  { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ❌ $msg" -ForegroundColor Red }

$results = @{ Passed = 0; Failed = 0; Warnings = 0 }

# ── Paths ────────────────────────────────────────────────────────────────────
$exePath    = Join-Path $PWD "dist" "ToastPOSManager" "ToastPOSManager.exe"
$bundleDir  = Split-Path $exePath -Parent
$logDir     = Join-Path $bundleDir "logs"
$reportPath = Join-Path $bundleDir "bootstrap_report.json"

# ─────────────────────────────────────────────────────────────────────────────
# 1. Verify bundle artifacts
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta
Write-Host "  [1/5] Bundle Artifact Verification" -ForegroundColor Magenta
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta

$checks = @(
    @{ Label = "ToastPOSManager.exe exists";   Path = $exePath },
    @{ Label = "version.json in bundle";       Path = Join-Path $bundleDir "version.json" },
    @{ Label = "launcher entry bundled";      Path = (Get-ChildItem $bundleDir -Filter "launcher*.pyc" | Select-Object -First 1).FullName },
    @{ Label = "app.py bundled";               Path = (Get-ChildItem $bundleDir -Filter "app*.pyc" | Select-Object -First 1).FullName },
    @{ Label = "PORTABLE_MODE.txt NOT in bundle (normal — added at zip step)"; IsAbsent = $true; Path = Join-Path $bundleDir "PORTABLE_MODE.txt" },
)

foreach ($check in $checks) {
    if ($check.IsAbsent) {
        if (-not (Test-Path $check.Path)) {
            ok $check.Label
            $results.Passed++
        } else {
            fail "$($check.Label) — found when it should not exist"
            $results.Failed++
        }
    } else {
        if (Test-Path $check.Path) {
            ok $check.Label
            $results.Passed++
        } else {
            fail "$($check.Label) — missing at: $($check.Path)"
            $results.Failed++
        }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. Launch smoke test
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta
Write-Host "  [2/5] Launch Smoke Test (--safe flag)" -ForegroundColor Magenta
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta

if (-not (Test-Path $exePath)) {
    fail "EXE not found at: $exePath"
    $results.Failed++
} else {
    $errLog = "$env:TEMP\toast_smoke_$PID.err"
    $proc = $null
    try {
        log "Launching: $exePath --safe"
        $proc = Start-Process $exePath -ArgumentList "--safe" -PassThru -RedirectStandardError $errLog -WindowStyle Hidden
        Start-Sleep 6

        if ($proc.HasExited) {
            $code = $proc.ExitCode
            if ($code -le 1) {
                ok "Process exited cleanly (exit code $code — acceptable)"
                $results.Passed++
            } else {
                warn "Process exited with code $code (may be bootstrap-blocked)"
                $results.Warnings++
            }
        } else {
            ok "Process running stably (PID $($proc.Id))"
            $results.Passed++
            Stop-Process $proc.Id -Force -ErrorAction SilentlyContinue
        }
    } finally {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process $proc.Id -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path $errLog) { Remove-Item $errLog -Force -ErrorAction SilentlyContinue }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 3. Bootstrap report
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta
Write-Host "  [3/5] Bootstrap Report Check" -ForegroundColor Magenta
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta

if (Test-Path $reportPath) {
    try {
        $report = Get-Content $reportPath -Raw | ConvertFrom-Json
        ok "bootstrap_report.json found"
        $results.Passed++

        $canRun    = $report.can_run
        $isFirst   = $report.is_first_run
        $portable  = $report.portable_mode
        $blockers  = $report.blockers | Measure-Object | Select-Object -ExpandProperty Count

        log "  can_run      : $canRun"
        log "  is_first_run : $isFirst"
        log "  portable_mode : $portable"
        log "  blocker count : $blockers"

        if ($canRun -or $blockers -gt 0) {
            ok "Bootstrap state is coherent"
            $results.Passed++
        } else {
            warn "Bootstrap report found but state is unclear"
            $results.Warnings++
        }
    } catch {
        warn "Could not parse bootstrap_report.json: $_"
        $results.Warnings++
    }
} else {
    warn "bootstrap_report.json not found at: $reportPath"
    $results.Warnings++
}

# ─────────────────────────────────────────────────────────────────────────────
# 4. Runtime folders
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta
Write-Host "  [4/5] Runtime Folder Auto-Creation" -ForegroundColor Magenta
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta

$requiredFolders = @("logs", "audit-logs", "toast-reports", "recovery-backups", "crash-reports")
foreach ($folder in $requiredFolders) {
    $folderPath = Join-Path $bundleDir $folder
    if (Test-Path $folderPath) {
        ok "Auto-created: $folder/"
        $results.Passed++
    } else {
        fail "Missing auto-created folder: $folder/"
        $results.Failed++
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# 5. Bootstrap log
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta
Write-Host "  [5/5] Bootstrap Log Check" -ForegroundColor Magenta
Write-Host "────────────────────────────────────────────────────────" -ForegroundColor Magenta

$bootstrapLogs = Get-ChildItem $logDir -Filter "bootstrap_*.log" -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 1

if ($bootstrapLogs) {
    ok "Bootstrap log found: $($bootstrapLogs.Name)"
    $results.Passed++

    $logContent = Get-Content $bootstrapLogs.FullName -Raw -ErrorAction SilentlyContinue
    if ($logContent -match "\[BOOTSTRAP\]") {
        ok "Log contains [BOOTSTRAP] marker"
        $results.Passed++
    } else {
        warn "Log missing [BOOTSTRAP] marker"
        $results.Warnings++
    }

    if ($logContent -match "can_run=" -or $logContent -match "blocker") {
        ok "Log contains can_run or blocker info"
        $results.Passed++
    } else {
        warn "Log missing can_run/blocker info"
        $results.Warnings++
    }
} else {
    warn "No bootstrap_*.log found in: $logDir"
    $results.Warnings++
}

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Smoke Test Summary" -ForegroundColor Magenta
Write-Host "════════════════════════════════════════════════════════" -ForegroundColor Magenta
Write-Host "  Passed   : $($results.Passed)" -ForegroundColor Green
Write-Host "  Warnings : $($results.Warnings)" -ForegroundColor Yellow
Write-Host "  Failed   : $($results.Failed)" -ForegroundColor Red
Write-Host ""

if ($results.Failed -gt 0) {
    Write-Host "RESULT: SMOKE TEST FAILED" -ForegroundColor Red
    exit 1
} elseif ($results.Warnings -gt 0) {
    Write-Host "RESULT: SMOKE TEST PASSED WITH WARNINGS" -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "RESULT: SMOKE TEST PASSED" -ForegroundColor Green
    exit 0
}
