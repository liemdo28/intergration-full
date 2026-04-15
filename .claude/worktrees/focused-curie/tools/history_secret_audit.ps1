$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")

$patterns = @(
    "client_secret",
    "client_id",
    "restaurant_guid",
    "api_hostname"
)

Write-Host "Scanning git history for legacy secret markers..."
foreach ($pattern in $patterns) {
    Write-Host ""
    Write-Host "Pattern: $pattern"
    git log -G $pattern --oneline --all
}

Write-Host ""
Write-Host "Known high-risk legacy files:"
git log --all --stat -- "intergration Toast/config.json" "intergration Toast/config.example.json" "Codex/config112.json" "Codex/config.example.json" "Claude/config_cl.json" "Claude/config(withIDAPP).json"
