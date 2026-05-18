param(
    [string]$PackagePath = "third_party\com.coplaydev.unity-mcp"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedPackagePath = if ([System.IO.Path]::IsPathRooted($PackagePath)) {
    $PackagePath
} else {
    Join-Path $repoRoot $PackagePath
}

if (-not (Test-Path -LiteralPath $resolvedPackagePath)) {
    throw "Pinned CoplayDev Unity MCP package was not found: $resolvedPackagePath"
}

$licenseCandidates = @(@("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING") |
    ForEach-Object { Join-Path $resolvedPackagePath $_ } |
    Where-Object { Test-Path -LiteralPath $_ })

if (-not $licenseCandidates) {
    throw "CoplayDev Unity MCP package is missing a LICENSE file. Refuse to build installers."
}

$licenseText = ($licenseCandidates | ForEach-Object { Get-Content -LiteralPath $_ -Raw }) -join "`n"
$isCoplayDevMit = $licenseText -match "MIT License" -and
    $licenseText -match "Copyright \(c\) 2025 CoplayDev" -and
    $licenseText -match "The above copyright notice and this permission notice shall be included in all"

if (-not $isCoplayDevMit) {
    throw "CoplayDev Unity MCP must include the expected CoplayDev MIT LICENSE text before packaging."
}

$noticeCandidates = @(@("NOTICE", "NOTICE.md", "NOTICE.txt") |
    ForEach-Object { Join-Path $resolvedPackagePath $_ } |
    Where-Object { Test-Path -LiteralPath $_ })

$distributionNotes = Join-Path $resolvedPackagePath "VRCFORGE_DISTRIBUTION_NOTES.txt"
if (-not (Test-Path -LiteralPath $distributionNotes)) {
    throw "CoplayDev Unity MCP vendored package is missing VRCFORGE_DISTRIBUTION_NOTES.txt. Refuse to build installers because local distribution changes must be documented."
}

Write-Host "CoplayDev Unity MCP license gate passed."
Write-Host "License file: $($licenseCandidates[0])"
Write-Host "Distribution notes: $distributionNotes"
if ($noticeCandidates) {
    Write-Host "Notice file: $($noticeCandidates[0])"
} else {
    Write-Warning "No NOTICE file found. Continuing because the license file passed; generated licenses/ will still include the LICENSE."
}
