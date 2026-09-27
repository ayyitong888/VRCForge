"""Exercise the dependency build with real ZIP bytes and no network/compiler."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("tampered", [False, True])
def test_dependency_archives_extract_only_after_digest_check(tmp_path, shell, tampered):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} unavailable")
    packaging = tmp_path / "packaging"
    packaging.mkdir()
    helper = tmp_path / "tools" / "alcom_litedb_reader"
    helper.mkdir(parents=True)
    source = (ROOT / "packaging/build_alcom_litedb_reader.ps1").read_text()
    packages = [
        ("LiteDB", "lib/net45/LiteDB.dll", "938a4f5f9d6a28383de8ccfcbdb8897e78432399215232cf38dbf147e03ec167"),
        ("System.Buffers", "lib/net461/System.Buffers.dll", "c30b3dd2c7e2f4cee4b823d692fd42118309b42ab1f5007f923d329a5b0d6b12"),
    ]
    for name, member, pinned in packages:
        archive = tmp_path / f"{name}.fixture"
        with zipfile.ZipFile(archive, "w") as target:
            target.writestr(member, name.encode())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        source = source.replace(pinned, digest)
    if tampered:
        with (tmp_path / "LiteDB.fixture").open("ab") as target:
            target.write(b"changed after pinning")
    (packaging / "build_alcom_litedb_reader.ps1").write_text(source)
    (helper / "build.ps1").write_text('''
param($LiteDbDll, $SystemBuffersDll, $OutputDirectory)
if ([IO.File]::ReadAllText($LiteDbDll) -cne 'LiteDB') { throw 'Bad LiteDB bytes' }
if ([IO.File]::ReadAllText($SystemBuffersDll) -cne 'System.Buffers') { throw 'Bad buffers bytes' }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
[IO.File]::WriteAllText((Join-Path $OutputDirectory 'compiled.txt'), 'verified inputs')
$global:LASTEXITCODE = 0
''')
    # Intercept downloads only; production hashing, extraction and cleanup run unchanged.
    runner = tmp_path / "run.ps1"
    runner.write_text('''
$ErrorActionPreference = 'Stop'
function Invoke-WebRequest {
    param($Uri, $OutFile)
    $name = if ($Uri -like '*/LiteDB/*') { 'LiteDB' } else { 'System.Buffers' }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot ($name + '.fixture')) -Destination $OutFile
}
& (Join-Path $PSScriptRoot 'packaging/build_alcom_litedb_reader.ps1') -OutputDirectory (Join-Path $PSScriptRoot 'result')
''')
    environment = os.environ.copy()
    # A PowerShell 7 parent must not inject its module directory into Windows PowerShell.
    for key in list(environment):
        if key.casefold() == "psmodulepath":
            del environment[key]
    if shell == "powershell.exe":
        environment["PSModulePath"] = str(Path(executable).parent / "Modules")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
        env=environment,
    )
    if tampered:
        assert result.returncode != 0
        assert "LiteDB package hash mismatch" in result.stderr
        assert not (tmp_path / "result").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert (tmp_path / "result/compiled.txt").read_text() == "verified inputs"
