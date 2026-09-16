param(
    [Parameter(Mandatory = $true)][string]$LiteDbDll,
    [Parameter(Mandatory = $true)][string]$SystemBuffersDll,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) { throw "NET Framework 4.x csc.exe not found: $csc" }
if (-not (Test-Path -LiteralPath $LiteDbDll -PathType Leaf)) { throw "LiteDB.dll not found: $LiteDbDll" }
if (-not (Test-Path -LiteralPath $SystemBuffersDll -PathType Leaf)) { throw "System.Buffers.dll not found: $SystemBuffersDll" }
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$output = Join-Path $OutputDirectory "vrcforge_alcom_litedb_reader.exe"
& $csc /nologo /target:exe /optimize+ /platform:x64 /out:$output /reference:$LiteDbDll (Join-Path $PSScriptRoot "AlcomLiteDbReader.cs")
if ($LASTEXITCODE -ne 0) { throw "ALCOM LiteDB reader compilation failed." }
Copy-Item -LiteralPath $LiteDbDll -Destination (Join-Path $OutputDirectory "LiteDB.dll") -Force
Copy-Item -LiteralPath $SystemBuffersDll -Destination (Join-Path $OutputDirectory "System.Buffers.dll") -Force
