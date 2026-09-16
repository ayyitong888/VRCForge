param(
    [string]$OutputDirectory = "tools\alcom_litedb_reader"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$output = if ([IO.Path]::IsPathRooted($OutputDirectory)) { $OutputDirectory } else { Join-Path $repoRoot $OutputDirectory }
$work = Join-Path ([IO.Path]::GetTempPath()) ("vrcforge-litedb-" + [guid]::NewGuid().ToString("N"))
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
$liteUrl = "https://www.nuget.org/api/v2/package/LiteDB/5.0.21"
$liteHash = "938a4f5f9d6a28383de8ccfcbdb8897e78432399215232cf38dbf147e03ec167"
$buffersUrl = "https://www.nuget.org/api/v2/package/System.Buffers/4.5.1"
$buffersHash = "c30b3dd2c7e2f4cee4b823d692fd42118309b42ab1f5007f923d329a5b0d6b12"
try {
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    $litePackage = Join-Path $work "LiteDB.nupkg"
    $buffersPackage = Join-Path $work "System.Buffers.nupkg"
    Invoke-WebRequest -Uri $liteUrl -OutFile $litePackage
    Invoke-WebRequest -Uri $buffersUrl -OutFile $buffersPackage
    if ((Get-FileHash $litePackage -Algorithm SHA256).Hash.ToLowerInvariant() -cne $liteHash) { throw "LiteDB package hash mismatch." }
    if ((Get-FileHash $buffersPackage -Algorithm SHA256).Hash.ToLowerInvariant() -cne $buffersHash) { throw "System.Buffers package hash mismatch." }
    $liteExtract = Join-Path $work "LiteDB"
    $buffersExtract = Join-Path $work "System.Buffers"
    Expand-Archive $litePackage -DestinationPath $liteExtract
    Expand-Archive $buffersPackage -DestinationPath $buffersExtract
    & (Join-Path $repoRoot "tools\alcom_litedb_reader\build.ps1") `
        -LiteDbDll (Join-Path $liteExtract "lib\net45\LiteDB.dll") `
        -SystemBuffersDll (Join-Path $buffersExtract "lib\net461\System.Buffers.dll") `
        -OutputDirectory $output
    if ($LASTEXITCODE -ne 0) { throw "ALCOM LiteDB reader build failed." }
} finally {
    $workFull = [IO.Path]::GetFullPath($work)
    if ($workFull.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $workFull)) {
        Remove-Item -LiteralPath $workFull -Recurse -Force -ErrorAction SilentlyContinue
    }
}
