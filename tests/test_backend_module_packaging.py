"""Exercise publication checks without compiling or executing a backend."""
import os
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows packaging script')


def run_build(tmp_path: Path, *, omit_module: bool = False):
    repo = tmp_path / 'repo'
    (repo / 'packaging').mkdir(parents=True)
    shutil.copyfile(ROOT / 'packaging/build_backend.ps1', repo / 'packaging/build_backend.ps1')
    shutil.copytree(
        ROOT / 'examples/skill-packages/vrcforge-first-run-guide',
        repo / 'examples/skill-packages/vrcforge-first-run-guide',
    )
    # Windows PowerShell 5.1 reads Get-Content using the active code page;
    # keep this inert fixture ASCII so the packaging contract is tested
    # independently of the machine locale.
    manifest = repo / 'examples/skill-packages/vrcforge-first-run-guide/manifest.json'
    manifest.write_text(json.dumps(json.loads(manifest.read_text(encoding='utf-8'))), encoding='ascii')
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    # This command only creates inert fixture files under the test's build dir.
    (binaries / 'pyinstaller.ps1').write_text(r'''
$out = Join-Path $args[([Array]::IndexOf($args, '--distpath') + 1)] 'vrcforge_backend'
New-Item -ItemType Directory -Force -Path (Join-Path $out '_internal\winpty') | Out-Null
Set-Content -LiteralPath (Join-Path $out 'vrcforge_backend.exe') -Value 'inert-test-fixture'
$guideOut = Join-Path $out '_internal\examples\skill-packages\vrcforge-first-run-guide'
Copy-Item -LiteralPath $env:VRCFORGE_TEST_GUIDE_SOURCE -Destination $guideOut -Recurse -Force
foreach ($name in @('OpenConsole.exe','winpty-agent.exe')) {
    Set-Content -LiteralPath (Join-Path $out ('_internal\winpty\' + $name)) -Value 'inert-test-fixture'
}
if ($args -contains 'noarchive') {
    foreach ($name in @('agent_approval_transactions','agent_checkpoint_recovery','unity_read_input_schemas','unity_shared_input_schemas','unity_write_input_schemas','runtime_observation')) {
        if ($env:VRCFORGE_TEST_OMIT_MODULE -eq '1' -and $name -eq 'unity_shared_input_schemas') { continue }
        Set-Content -LiteralPath (Join-Path $out ('_internal\' + $name + '.pyc')) -Value 'inert-module-fixture'
    }
}
exit 0
''', encoding='utf-8')
    output = repo / 'dist/backend'
    output.mkdir(parents=True)
    (output / 'previous-build.txt').write_text('preserve until candidate is verified')
    env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'])
    env['VRCFORGE_TEST_OMIT_MODULE'] = '1' if omit_module else '0'
    env['VRCFORGE_TEST_GUIDE_SOURCE'] = str(repo / 'examples/skill-packages/vrcforge-first-run-guide')
    env['PSModulePath'] = os.pathsep.join(
        filter(None, [
            env.get('PSModulePath', ''),
            r'C:\Windows\System32\WindowsPowerShell\v1.0\Modules',
        ])
    )
    result = subprocess.run(
        ['powershell', '-NoProfile', '-File', str(repo / 'packaging/build_backend.ps1')],
        cwd=repo, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result, output


def test_backend_publishes_external_modules_with_existing_onedir_layout(tmp_path):
    result, output = run_build(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (output / '_internal/unity_shared_input_schemas.pyc').is_file()
    assert (output / '_internal/winpty/OpenConsole.exe').is_file()
    assert not (output / 'previous-build.txt').exists()


def test_backend_missing_required_module_preserves_previous_build(tmp_path):
    result, output = run_build(tmp_path, omit_module=True)
    assert result.returncode != 0, 'An incomplete backend must not replace the working output'
    assert 'unity_shared_input_schemas.pyc' in result.stderr
    assert (output / 'previous-build.txt').read_text() == 'preserve until candidate is verified'
