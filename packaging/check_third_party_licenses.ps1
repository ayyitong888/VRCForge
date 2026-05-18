param(
    [string]$ManifestPath = "packaging\THIRD_PARTY_LICENSES.json"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedManifestPath = if ([System.IO.Path]::IsPathRooted($ManifestPath)) {
    $ManifestPath
} else {
    Join-Path $repoRoot $ManifestPath
}

if (-not (Test-Path -LiteralPath $resolvedManifestPath)) {
    throw "Third-party license manifest is missing: $resolvedManifestPath"
}

$manifest = Get-Content -LiteralPath $resolvedManifestPath -Raw | ConvertFrom-Json
if (-not $manifest.components -or $manifest.components.Count -eq 0) {
    throw "Third-party license manifest has no components."
}

foreach ($component in $manifest.components) {
    $componentPath = Join-Path $repoRoot $component.path
    if (-not (Test-Path -LiteralPath $componentPath)) {
        throw "Third-party component missing: $($component.name) at $componentPath"
    }

    $licensePath = Join-Path $componentPath $component.requiredLicenseFile
    if (-not (Test-Path -LiteralPath $licensePath)) {
        throw "Third-party component $($component.name) is missing license file: $licensePath"
    }

    $licenseText = Get-Content -LiteralPath $licensePath -Raw
    foreach ($requiredText in $component.requiredLicenseText) {
        if ($licenseText -notmatch [regex]::Escape($requiredText)) {
            throw "Third-party component $($component.name) license does not contain required text: $requiredText"
        }
    }

    if ($component.requiredDistributionNotes) {
        $notesPath = Join-Path $componentPath $component.requiredDistributionNotes
        if (-not (Test-Path -LiteralPath $notesPath)) {
            throw "Third-party component $($component.name) is missing distribution notes: $notesPath"
        }
    }

    Write-Host "Third-party license gate passed: $($component.name) ($($component.license))"
}
