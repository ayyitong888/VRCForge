"""Read-only evidence for the exact installer helper in a disposable Windows VM."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main() -> None:
    helper = Path(sys.argv[1]).resolve(strict=True)
    output = Path(sys.argv[2]).resolve()
    native_root = Path(os.environ['WINDIR']) / 'System32'
    program_files = Path(os.environ.get('ProgramW6432', os.environ['ProgramFiles']))
    env = os.environ.copy()
    env['PSModulePath'] = str(native_root / 'WindowsPowerShell/v1.0/Modules')
    command = [str(native_root / 'WindowsPowerShell/v1.0/powershell.exe'),
               '-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
               '-File', str(helper), '-Action', 'ValidateDestination', '-Version', '1.8.0',
               '-ProgramFilesRoot', str(program_files),
               '-DestinationRoot', str(program_files / 'VRCForge'),
               '-ExpectedInstallLeaf', 'VRCForge', '-StateTag', 'VRCForge']
    # Owned synchronous child, current runner identity, no network/auth channel.
    # The helper's validation action is read-only; subprocess owns and closes pipes.
    result = subprocess.run(command, env=env, capture_output=True, text=True,
                            errors='replace', timeout=30, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    output.write_text(json.dumps({
        'helperSha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
        'action': 'ValidateDestination', 'exitCode': result.returncode,
        'stdout': result.stdout, 'stderr': result.stderr,
    }, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
