param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-ExistingFile {
    param(
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) {
            continue
        }

        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

function Get-UnityMcpCommand {
    $command = Get-Command "unity-mcp.exe" -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return @{
            FilePath = $command.Source
            Prefix = @()
        }
    }

    $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
    $pythonScriptsDir = if ($pythonCommand -and $pythonCommand.Source) {
        Join-Path (Split-Path -Parent $pythonCommand.Source) "Scripts"
    } else {
        $null
    }
    $virtualEnvUnityMcp = if ($env:VIRTUAL_ENV) {
        Join-Path $env:VIRTUAL_ENV "Scripts\unity-mcp.exe"
    } else {
        $null
    }
    $pythonUnityMcp = if ($pythonScriptsDir) {
        Join-Path $pythonScriptsDir "unity-mcp.exe"
    } else {
        $null
    }

    $unityMcpExe = Get-ExistingFile @(
        $virtualEnvUnityMcp,
        $pythonUnityMcp,
        "$env:APPDATA\Python\Python314\Scripts\unity-mcp.exe",
        "$env:APPDATA\Python\Scripts\unity-mcp.exe",
        "$env:LOCALAPPDATA\Microsoft\WinGet\Links\unity-mcp.exe"
    )
    if ($unityMcpExe) {
        return @{
            FilePath = $unityMcpExe
            Prefix = @()
        }
    }

    $virtualEnvUvx = if ($env:VIRTUAL_ENV) {
        Join-Path $env:VIRTUAL_ENV "Scripts\uvx.exe"
    } else {
        $null
    }
    $pythonUvx = if ($pythonScriptsDir) {
        Join-Path $pythonScriptsDir "uvx.exe"
    } else {
        $null
    }

    $uvxCommand = Get-Command "uvx.exe" -ErrorAction SilentlyContinue
    $uvxPath = if ($uvxCommand -and $uvxCommand.Source) {
        $uvxCommand.Source
    } else {
        Get-ExistingFile @(
            $virtualEnvUvx,
            $pythonUvx,
            "$env:APPDATA\Python\Python314\Scripts\uvx.exe",
            "$env:APPDATA\Python\Scripts\uvx.exe",
            "$env:LOCALAPPDATA\Microsoft\WinGet\Links\uvx.exe"
        )
    }

    if ($uvxPath) {
        return @{
            FilePath = $uvxPath
            Prefix = @("--from", "mcpforunityserver", "unity-mcp")
        }
    }

    throw @"
Could not find a usable unity-mcp CLI.
Expected one of:
- unity-mcp.exe on PATH
- unity-mcp.exe under %APPDATA%\Python\Python314\Scripts
- uvx.exe so the wrapper can run: uvx --from mcpforunityserver unity-mcp

Install either:
1. python -m pip install --user mcpforunityserver
2. python -m pip install --user uv
"@
}

$resolved = Get-UnityMcpCommand
$arguments = @()
$arguments += $resolved.Prefix
$arguments += $CliArgs

& $resolved.FilePath @arguments
exit $LASTEXITCODE
