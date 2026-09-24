# Meet AGI - Step 1 installer. Run in an ADMIN PowerShell.
$ErrorActionPreference = "Continue"
$log = Join-Path $PSScriptRoot "..\setup_versions.txt"
$ids = @("Git.Git","Python.Python.3.12","OpenJS.NodeJS.LTS","GitHub.cli","Ngrok.Ngrok")
foreach ($id in $ids) {
  Write-Host "=== Installing $id ===" -ForegroundColor Cyan
  winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements --scope machine
  if ($LASTEXITCODE -ne 0) { winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements }
}
Write-Host "=== Installing Claude Code (native installer) ===" -ForegroundColor Cyan
try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression } catch { Write-Host "Claude Code install error: $_" }
# refresh PATH in this window
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") + ";$env:USERPROFILE\.local\bin"
git config --system core.longpaths true
$out = @()
function V($name, $cmd) { try { $v = (Invoke-Expression $cmd 2>&1 | Select-Object -First 1) -join ""; "$name | $v" } catch { "$name | NOT FOUND" } }
$out += V "git" "git --version"
$out += V "python(py -3.12)" "py -3.12 --version"
$out += V "python(on PATH)" "python --version"
$out += V "python path" "(Get-Command python).Source"
$out += V "node" "node --version"
$out += V "npm" "npm.cmd --version"
$out += V "gh" "gh --version"
$out += V "claude" "claude --version"
$out += V "claude path" "(Get-Command claude).Source"
$out += V "ngrok" "ngrok version"
$out += V "core.longpaths" "git config --system core.longpaths"
$out += V "winget" "winget --version"
$out | Set-Content -Encoding utf8 $log
$out | ForEach-Object { Write-Host $_ }
Write-Host "`nDONE - tell Claude 'step 1 done'" -ForegroundColor Green
