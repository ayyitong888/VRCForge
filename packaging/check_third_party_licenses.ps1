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

function Assert-LicenseFile {
    param(
        [string]$ComponentName,
        [string]$ComponentPath,
        [string]$LicenseFile,
        [object[]]$RequiredText
    )

    $licensePath = Join-Path $ComponentPath $LicenseFile
    if (-not (Test-Path -LiteralPath $licensePath)) {
        throw "Third-party component $ComponentName is missing license file: $licensePath"
    }

    $licenseText = Get-Content -LiteralPath $licensePath -Raw
    foreach ($requiredText in $RequiredText) {
        if ($licenseText -notmatch [regex]::Escape($requiredText)) {
            throw "Third-party component $ComponentName license does not contain required text: $requiredText"
        }
    }
}

foreach ($component in $manifest.components) {
    $componentPath = Join-Path $repoRoot $component.path
    if (-not (Test-Path -LiteralPath $componentPath)) {
        throw "Third-party component missing: $($component.name) at $componentPath"
    }

    $requiredLicenseFilesProperty = $component.PSObject.Properties["requiredLicenseFiles"]
    if ($requiredLicenseFilesProperty -and $requiredLicenseFilesProperty.Value) {
        foreach ($license in $requiredLicenseFilesProperty.Value) {
            Assert-LicenseFile `
                -ComponentName $component.name `
                -ComponentPath $componentPath `
                -LicenseFile $license.file `
                -RequiredText $license.requiredText
        }
    } else {
        Assert-LicenseFile `
            -ComponentName $component.name `
            -ComponentPath $componentPath `
            -LicenseFile $component.requiredLicenseFile `
            -RequiredText $component.requiredLicenseText
    }

    if ($component.requiredDistributionNotes) {
        $notesPath = Join-Path $componentPath $component.requiredDistributionNotes
        if (-not (Test-Path -LiteralPath $notesPath)) {
            throw "Third-party component $($component.name) is missing distribution notes: $notesPath"
        }
    }

    Write-Host "Third-party license gate passed: $($component.name) ($($component.license))"
}
