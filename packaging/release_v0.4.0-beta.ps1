# VRCForge v0.4.0-beta one-shot release script (ASCII only for PS 5.1 compatibility)
# Usage (repo root):
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging\release_v0.4.0-beta.ps1
# Steps: validate (pytest/tsc/build) -> commit batch -> reword 3 old commits -> push + tag -> build release
# Any failure aborts immediately; validation failures commit nothing.

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent
Set-Location $repo

$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") { throw "Current branch is '$branch'. Switch to main first." }

Write-Host ""
Write-Host "== [1/7] Validate: pytest ==" -ForegroundColor Cyan
python -m pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "pytest FAILED. Aborted, nothing committed." }

Write-Host ""
Write-Host "== [2/7] Validate: tsc ==" -ForegroundColor Cyan
npx tsc --noEmit
if ($LASTEXITCODE -ne 0) { throw "tsc FAILED. Aborted, nothing committed." }

Write-Host ""
Write-Host "== [3/7] Validate: frontend build ==" -ForegroundColor Cyan
npm run build
if ($LASTEXITCODE -ne 0) { throw "build FAILED. Aborted, nothing committed." }

Write-Host ""
Write-Host "== [4/7] Commit this batch ==" -ForegroundColor Cyan
git add -A
git commit -m "feat: stepper onboarding, temp-chat/new-project sidebar with custom project prefs, chat-bubble agent replies with real LLM reply text and provider-model badge, steering queue, collapsible run rows with per-turn timing, project collapse/hide, selection toolbar, temp-chat section collapse"
if ($LASTEXITCODE -ne 0) { throw "commit FAILED." }

Write-Host ""
Write-Host "== [5/7] Reword 3 old commit messages (non-interactive rebase) ==" -ForegroundColor Cyan
$todoEditor = Join-Path $env:TEMP "vrcforge-rewrite-todo.ps1"
@'
param([string]$TodoPath)
$map = [ordered]@{
  "2e8d0c8" = "feat: new home/settings, fetched model dropdown, multi-chat sidebar, UI sweep fixes"
  "ddbc1c0" = "feat: new home/settings, fetched model dropdown, multi-chat sidebar with local transcript persistence, UI sweep fixes"
  "b0301e6" = "feat: new home/settings, model dropdown, multi-chat persistence, history replay, /compact, slash-command skill invocation"
}
$out = @()
foreach ($line in Get-Content $TodoPath) {
  $out += $line
  foreach ($hash in $map.Keys) {
    if ($line -match "^pick\s+$hash") {
      $out += ('exec git commit --amend -m "' + $map[$hash] + '"')
    }
  }
}
Set-Content -Path $TodoPath -Value $out -Encoding ASCII
'@ | Set-Content -Path $todoEditor -Encoding ASCII

$env:GIT_SEQUENCE_EDITOR = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$todoEditor`""
git rebase -i "2e8d0c8^"
$rebaseExit = $LASTEXITCODE
Remove-Item Env:GIT_SEQUENCE_EDITOR -ErrorAction SilentlyContinue
Remove-Item $todoEditor -ErrorAction SilentlyContinue
if ($rebaseExit -ne 0) {
  git rebase --abort 2>$null
  throw "rebase FAILED. Aborted back to pre-rebase state (your batch commit is intact)."
}
Write-Host "Recent commits after reword:" -ForegroundColor Yellow
git log --oneline -8

Write-Host ""
Write-Host "== [6/7] Push + tag ==" -ForegroundColor Cyan
git push --force-with-lease origin main
if ($LASTEXITCODE -ne 0) { throw "push FAILED (local commit/rebase done; fix network/auth then rerun: git push --force-with-lease origin main)." }
$tagExists = git tag -l "v0.4.0-beta"
if (-not $tagExists) { git tag v0.4.0-beta }
git push origin v0.4.0-beta
if ($LASTEXITCODE -ne 0) { throw "tag push FAILED (rerun: git push origin v0.4.0-beta)." }

Write-Host ""
Write-Host "== [7/7] Build release packages ==" -ForegroundColor Cyan
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "packaging\build_release.ps1")
if ($LASTEXITCODE -ne 0) { throw "build_release FAILED (main and tag already pushed; fix then rerun packaging\build_release.ps1 alone)." }

Write-Host ""
Write-Host "ALL DONE." -ForegroundColor Green
Write-Host "Last manual step: create the GitHub Release for tag v0.4.0-beta, upload both installers + payload zip, and set up Issues templates (bug / feature request)."
