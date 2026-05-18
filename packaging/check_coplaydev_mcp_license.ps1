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
$allowed = $licenseText -match "MIT License" -or
    $licenseText -match "Apache License" -or
    $licenseText -match "BSD"

if (-not $allowed) {
    throw "CoplayDev Unity MCP license was not recognized as a redistributable license. Review manually before packaging."
}

$noticeCandidates = @(@("NOTICE", "NOTICE.md", "NOTICE.txt") |
    ForEach-Object { Join-Path $resolvedPackagePath $_ } |
    Where-Object { Test-Path -LiteralPath $_ })

Write-Host "CoplayDev Unity MCP license gate passed."
Write-Host "License file: $($licenseCandidates[0])"
if ($noticeCandidates) {
    Write-Host "Notice file: $($noticeCandidates[0])"
} else {
    Write-Warning "No NOTICE file found. Continuing because the license file passed; generated licenses/ will still include the LICENSE."
}
