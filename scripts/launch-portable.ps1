param(
    [Parameter(Mandatory)]
    [string] $Executable,
    # Reuse the same explicit path across builds when testing with an isolated profile.
    [string] $UserDataDir
)

$desktop = Get-Item -LiteralPath $Executable -ErrorAction Stop
if ($desktop -isnot [System.IO.FileInfo] -or $desktop.Name -ine "VRCForge.exe") {
    throw "Specify the VRCForge.exe in the extracted portable build."
}

$startInfo = [System.Diagnostics.ProcessStartInfo]::new($desktop.FullName)
$startInfo.WorkingDirectory = $desktop.DirectoryName
$startInfo.UseShellExecute = $false
$profileVariable = "VRCFORGE_USER_DATA_DIR"
if ($UserDataDir) {
    $startInfo.EnvironmentVariables[$profileVariable] = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($UserDataDir)
} else {
    # Let the app use its normal persistent profile, even if an older launch set an override.
    $startInfo.EnvironmentVariables.Remove($profileVariable)
}

[System.Diagnostics.Process]::Start($startInfo)
