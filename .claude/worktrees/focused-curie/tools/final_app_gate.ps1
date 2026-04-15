param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

function Write-CheckResult {
    param(
        [string]$Name,
        [ValidateSet("OK", "WARN", "FAIL")]
        [string]$Status,
        [string]$Message
    )

    Write-Host ("[{0}] {1}: {2}" -f $Status, $Name, $Message)
}

function Test-FileExists {
    param([string]$Path)
    return Test-Path -LiteralPath (Join-Path $RepoRoot $Path)
}

function Test-WorkflowContains {
    param(
        [string]$Path,
        [string]$Pattern
    )

    $fullPath = Join-Path $RepoRoot $Path
    if (-not (Test-Path -LiteralPath $fullPath)) {
        return $false
    }

    return Select-String -Path $fullPath -Pattern $Pattern -Quiet
}

$failCount = 0
$warnCount = 0

function Add-Result {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$PassMessage,
        [string]$FailMessage,
        [switch]$WarnOnly
    )

    if ($Passed) {
        Write-CheckResult -Name $Name -Status "OK" -Message $PassMessage
        return
    }

    if ($WarnOnly) {
        $script:warnCount++
        Write-CheckResult -Name $Name -Status "WARN" -Message $FailMessage
    }
    else {
        $script:failCount++
        Write-CheckResult -Name $Name -Status "FAIL" -Message $FailMessage
    }
}

Add-Result -Name "Policy" `
    -Passed (Test-FileExists "POLICY.md") `
    -PassMessage "Engineering policy exists" `
    -FailMessage "Missing POLICY.md"

Add-Result -Name "Current Review" `
    -Passed (Test-FileExists "docs/CURRENT_STATE_REVIEW.md") `
    -PassMessage "Current-state review exists" `
    -FailMessage "Missing docs/CURRENT_STATE_REVIEW.md"

Add-Result -Name "Operator Guide" `
    -Passed (Test-FileExists "docs/OPERATOR_GUIDE.md") `
    -PassMessage "Operator guide exists" `
    -FailMessage "Missing docs/OPERATOR_GUIDE.md"

Add-Result -Name "Final App Requirements" `
    -Passed (Test-FileExists "docs/FINAL_APP_REQUIREMENTS.md") `
    -PassMessage "Final app requirements exist" `
    -FailMessage "Missing docs/FINAL_APP_REQUIREMENTS.md"

Add-Result -Name "Secret Remediation Plan" `
    -Passed (Test-FileExists "docs/SECRET_REMEDIATION.md") `
    -PassMessage "Secret remediation doc exists" `
    -FailMessage "Missing docs/SECRET_REMEDIATION.md"

Add-Result -Name "Release Build Script" `
    -Passed (Test-FileExists "desktop-app/build_release.ps1") `
    -PassMessage "Release build script exists" `
    -FailMessage "Missing desktop-app/build_release.ps1"

Add-Result -Name "Installer Script" `
    -Passed (Test-FileExists "desktop-app/installer/ToastPOSManager.iss") `
    -PassMessage "Installer script exists" `
    -FailMessage "Missing Inno Setup installer script"

Add-Result -Name "Windows CI" `
    -Passed (Test-FileExists ".github/workflows/windows-ci.yml") `
    -PassMessage "Windows CI workflow exists" `
    -FailMessage "Missing Windows CI workflow"

Add-Result -Name "CI Installer Lane" `
    -Passed (Test-WorkflowContains ".github/workflows/windows-ci.yml" "innosetup|ISCC|Install Inno Setup") `
    -PassMessage "CI includes installer prerequisites" `
    -FailMessage "CI does not appear to provision installer tooling"

Add-Result -Name "CI Artifact Upload" `
    -Passed (Test-WorkflowContains ".github/workflows/windows-ci.yml" "upload-artifact") `
    -PassMessage "CI uploads release artifacts" `
    -FailMessage "CI artifact upload step not found"

Add-Result -Name "Validation UX" `
    -Passed (Test-WorkflowContains "desktop-app/app.py" "Validation Issues") `
    -PassMessage "Validation issue panel exists in the app UI" `
    -FailMessage "Validation issue panel not detected in app.py"

Add-Result -Name "Typed Validation Model" `
    -Passed (Test-WorkflowContains "desktop-app/qb_sync.py" "class ValidationIssue") `
    -PassMessage "Typed validation model exists" `
    -FailMessage "ValidationIssue model not found in qb_sync.py"

Add-Result -Name "Secret Scanning in CI" `
    -Passed (Test-WorkflowContains ".github/workflows/windows-ci.yml" "gitleaks|trufflehog|secret") `
    -PassMessage "CI appears to include secret scanning" `
    -FailMessage "No secret scanning step detected in CI yet" `
    -WarnOnly

Add-Result -Name "Signed Artifact Flow" `
    -Passed (Test-WorkflowContains ".github/workflows/windows-ci.yml" "signtool|codesign|sign") `
    -PassMessage "Signing flow detected" `
    -FailMessage "No artifact signing flow detected yet" `
    -WarnOnly

Write-Host ""
Write-Host ("Summary: {0} fail, {1} warn" -f $failCount, $warnCount)

if ($failCount -gt 0) {
    exit 1
}
