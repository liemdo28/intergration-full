param(
    [switch]$Execute
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pathsToPurge = @(
    "intergration Toast/config.json",
    "intergration Toast/config.example.json",
    "Codex/config112.json",
    "Codex/config.example.json",
    "Claude/config_cl.json",
    "Claude/config(withIDAPP).json"
)

Write-Host "Secret history cleanup helper"
Write-Host "Repository: $repoRoot"
Write-Host ""
Write-Host "Candidate paths to purge:"
$pathsToPurge | ForEach-Object { Write-Host " - $_" }
Write-Host ""

if (-not $Execute) {
    Write-Host "Dry run only. No history rewrite was executed."
    Write-Host ""
    Write-Host "Recommended flow:"
    Write-Host "  1. Rotate/revoke exposed secrets first."
    Write-Host "  2. Freeze pushes and make a backup clone."
    Write-Host "  3. Install git-filter-repo if needed."
    Write-Host "  4. Re-run this script with -Execute."
    Write-Host "  5. After validation, force-push rewritten refs to all remotes."
    return
}

if (git status --short) {
    throw "Working tree is not clean. Commit or stash changes before rewriting history."
}

$filterRepo = Get-Command git-filter-repo -ErrorAction SilentlyContinue
if (-not $filterRepo) {
    throw "git-filter-repo is not installed or not on PATH."
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupTag = "before-secret-rewrite-$timestamp"
git tag $backupTag
Write-Host "Created safety tag: $backupTag"

$args = @()
foreach ($path in $pathsToPurge) {
    $args += "--path"
    $args += $path
}
$args += "--invert-paths"
$args += "--force"

Write-Host "Running git-filter-repo..."
& git-filter-repo @args

Write-Host ""
Write-Host "History rewrite complete."
Write-Host "Next steps:"
Write-Host "  1. Validate log/history and rerun secret audit."
Write-Host "  2. Force-push: git push --force --all full && git push --force --tags full"
Write-Host "  3. Repeat force-push for origin if needed."
