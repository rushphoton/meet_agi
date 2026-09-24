# WHY: creates the public GitHub repo "meet_agi" from this folder and pushes the
# first commit, using the GitHub CLI you already logged into. Guards against the
# one failure that matters here: committing the secrets file (.env).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .git)) { git init -b main | Out-Null }
if (-not (git config user.email)) {
  $u = gh api user | ConvertFrom-Json
  git config user.name $(if ($u.name) { $u.name } else { $u.login })
  git config user.email "$($u.id)+$($u.login)@users.noreply.github.com"
}
git add -A
if (git diff --cached --name-only | Select-String -Pattern '(^|/)\.env$') { git reset | Out-Null; throw "STOPPED: .env was about to be committed." }
git commit -m "A1: rules, agents, hooks" | Out-Null
gh repo create meet_agi --public --source . --remote origin --push
$url = gh repo view meet_agi --json url -q .url
"$url" | Set-Content -Encoding utf8 publish_result.txt
Write-Host "`nDONE: $url - tell Claude 'pushed'" -ForegroundColor Green
