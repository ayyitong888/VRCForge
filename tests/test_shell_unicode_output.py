"""Real bounded PowerShell output tests; no App, provider, or Unity process."""
import os
import subprocess
import zipfile

import pytest

from agent_shell_service import AgentShellService, default_shell_process_ports
from test_agent_shell_service import service


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell encoding boundary")
@pytest.mark.parametrize("runner", ["direct", "session_argv"])
def test_powershell_retains_unicode_and_declared_archive_encodings(tmp_path, runner):
    expected = "ARCHIVE 面捕3件 中文 日本語"
    utf8_text = "使用说明：面捕設定を確認してください。"
    legacy_text = "日本語の説明"
    archive = tmp_path / "fixture.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Readme.txt", utf8_text.encode("utf-8"))
        bundle.writestr("Legacy.txt", legacy_text.encode("cp932"))
    command = f"""Write-Output '{expected}'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead('{archive.as_posix()}')
try {{
  foreach ($item in @(@('Readme.txt', 65001), @('Legacy.txt', 932))) {{
    $reader = [IO.StreamReader]::new($archive.GetEntry($item[0]).Open(), [Text.Encoding]::GetEncoding([int]$item[1]), $false)
    try {{ Write-Output $reader.ReadToEnd() }} finally {{ $reader.Dispose() }}
  }}
  [Console]::Error.WriteLine('错误 診断')
}} finally {{ $archive.Dispose() }}
"""
    if runner == "session_argv":
        result = subprocess.run(AgentShellService._command_argv(command), cwd=tmp_path,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    else:
        shell, *_ = service(tmp_path)
        shell._process = default_shell_process_ports()
        try:
            with shell._execution_admission() as admission:
                result = shell._run_command(command, tmp_path, admission_id=admission, timeout_seconds=15)
            stdout, stderr, code = result["stdout"], result["stderr"], result["exitCode"]
            assert shell.active_process_count == 0
        finally:
            shell.shutdown()
    assert code == 0
    assert stdout.splitlines() == [expected, utf8_text, legacy_text]
    assert stderr.strip() == "错误 診断"
    assert "\ufffd" not in stdout + stderr
