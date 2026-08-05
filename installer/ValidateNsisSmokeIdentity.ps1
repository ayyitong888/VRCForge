Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$smokeId = [Environment]::GetEnvironmentVariable("VRCFORGE_NSIS_SMOKE_ID", "Process")
if ($smokeId -cnotmatch "^[a-f0-9]{32}$") {
    throw "VRCFORGE_NSIS_SMOKE_ID must be exactly 32 lowercase hexadecimal characters."
}
