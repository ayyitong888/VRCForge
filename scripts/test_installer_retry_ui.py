"""Exercise the actual published payload and NSIS UI on a disposable Windows VM."""
from __future__ import annotations
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import winreg

u = c.WinDLL('user32', use_last_error=True)
k = c.WinDLL('kernel32', use_last_error=True)
CALLBACK = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
for name, args, ret in [
    ('EnumWindows', [CALLBACK, w.LPARAM], w.BOOL),
    ('EnumChildWindows', [w.HWND, CALLBACK, w.LPARAM], w.BOOL),
    ('GetWindowThreadProcessId', [w.HWND, c.POINTER(w.DWORD)], w.DWORD),
    ('GetWindowTextW', [w.HWND, w.LPWSTR, c.c_int], c.c_int),
    ('GetClassNameW', [w.HWND, w.LPWSTR, c.c_int], c.c_int),
    ('IsWindowVisible', [w.HWND], w.BOOL),
    ('IsWindowEnabled', [w.HWND], w.BOOL),
    ('GetDlgCtrlID', [w.HWND], c.c_int),
    ('PostMessageW', [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL),
]:
    fn = getattr(u, name); fn.argtypes = args; fn.restype = ret
for name, args, ret in [
    ('CreateFileW', [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
    ('CloseHandle', [w.HANDLE], w.BOOL),
    ('OpenProcess', [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
    ('TerminateProcess', [w.HANDLE, w.UINT], w.BOOL),
    ('CreateToolhelp32Snapshot', [w.DWORD, w.DWORD], w.HANDLE),
]:
    fn = getattr(k, name); fn.argtypes = args; fn.restype = ret
class ProcessEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD), ('heap', c.c_size_t),
                ('module', w.DWORD), ('threads', w.DWORD), ('parent', w.DWORD), ('priority', w.LONG),
                ('flags', w.DWORD), ('exe', w.WCHAR * 260)]
for name in ('Process32FirstW', 'Process32NextW'):
    getattr(k, name).argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
    getattr(k, name).restype = w.BOOL

def descendants(root: int) -> set[int]:
    handle = k.CreateToolhelp32Snapshot(2, 0)
    if handle == w.HANDLE(-1).value: raise c.WinError(c.get_last_error())
    pairs = []
    try:
        entry = ProcessEntry(); entry.size = c.sizeof(entry)
        ok = k.Process32FirstW(handle, c.byref(entry))
        while ok:
            pairs.append((entry.pid, entry.parent))
            ok = k.Process32NextW(handle, c.byref(entry))
    finally: k.CloseHandle(handle)
    result = {root}
    while True:
        more = {pid for pid, parent in pairs if parent in result} - result
        if not more: return result
        result.update(more)

def text(hwnd: int, class_name=False) -> str:
    buf = c.create_unicode_buffer(8192)
    (u.GetClassNameW if class_name else u.GetWindowTextW)(hwnd, buf, len(buf))
    return buf.value

def snapshot(pid: int) -> list[dict]:
    pids = descendants(pid); windows = []
    def top(hwnd, _):
        owner = w.DWORD(); u.GetWindowThreadProcessId(hwnd, c.byref(owner))
        if owner.value not in pids or not u.IsWindowVisible(hwnd): return True
        controls = []
        def child(ch, _):
            if u.IsWindowVisible(ch):
                controls.append(dict(hwnd=ch, id=u.GetDlgCtrlID(ch), text=text(ch),
                                     cls=text(ch, True), enabled=bool(u.IsWindowEnabled(ch))))
            return True
        u.EnumChildWindows(hwnd, CALLBACK(child), 0)
        windows.append(dict(hwnd=hwnd, pid=owner.value, title=text(hwnd), controls=controls))
        return True
    u.EnumWindows(CALLBACK(top), 0)
    return windows

def click(button: dict) -> None:
    if not u.PostMessageW(button['hwnd'], 0xF5, 0, 0): raise c.WinError(c.get_last_error())

def retry_dialog(windows: list[dict]):
    for window in windows:
        buttons = {x['id']: x for x in window['controls'] if x['cls'] == 'Button' and x['enabled']}
        if 4 in buttons and 2 in buttons:
            return window, buttons
    return None

def advance(windows: list[dict]) -> None:
    for window in reversed(windows):
        for control in window['controls']:
            if control['cls'] != 'Button' or not control['enabled']: continue
            label = control['text'].replace('&', '').strip().lower().strip('<> ')
            if label in {'next', 'i agree', 'install', 'finish', 'close', 'ok'}:
                click(control); return

def stop_owned(process: subprocess.Popen) -> None:
    # Only this harness's live child tree in the disposable hosted VM.
    if process.poll() is not None: return
    for pid in sorted(descendants(process.pid) - {process.pid}, reverse=True):
        handle = k.OpenProcess(1, False, pid)
        if handle:
            try: k.TerminateProcess(handle, 1)
            finally: k.CloseHandle(handle)
    process.terminate(); process.wait(timeout=15)

def lock(path: Path):
    directory = path.is_dir()
    # Directory handles allow reads but withhold FILE_SHARE_DELETE so an atomic
    # directory rename fails without involving Restart Manager's app resources.
    handle = k.CreateFileW(str(path), 0x80000000, 3 if directory else 0, None, 3, 0x02000000 if directory else 0, None)
    if handle == w.HANDLE(-1).value: raise c.WinError(c.get_last_error())
    return handle

def sha(path: Path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()

def silent(installer: Path, timeout: float):
    return subprocess.run([str(installer), '/S'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout).returncode

def interactive(installer: Path, dest: Path, case: str, evidence: dict, timeout: float):
    held = None; app = None; process = None
    try:
        if case == 'running-app':
            app = subprocess.Popen([str(dest / 'VRCForge.exe')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(5)
            if app.poll() is not None: raise RuntimeError('Actual installed App exited before running-app test')
            evidence['appPid'] = app.pid
        elif case != 'activation-retry': held = lock(dest / 'VRCForge.exe')
        process = subprocess.Popen([str(installer)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Acquire a real file lock after preflight, while the real payload is
            # extracting. This tests the activation-failure retry independently
            # of the early busy-app prompt (Web must download a fresh stage).
            if case == 'activation-retry' and held is None and any(dest.parent.glob('VRCForge-Stage-*')):
                held = lock(dest)
                evidence['lockedAfterPreflight'] = True
            windows = snapshot(process.pid); evidence['lastWindows'] = windows
            found = retry_dialog(windows)
            if found:
                window, buttons = found
                body = '\n'.join(x['text'] for x in window['controls'])
                expected_message = 'The running VRCForge could not close normally' if case == 'activation-retry' else 'VRCForge is still running'
                if expected_message not in body: raise RuntimeError('Unexpected retry dialog: ' + body)
                if case == 'activation-retry' and not held: raise RuntimeError('Activation fault was not injected')
                evidence['busyDialog'] = window
                if app and app.poll() is not None: raise RuntimeError('Installer closed App before asking user')
                if case != 'cancel':
                    if held: k.CloseHandle(held); held = None
                    if app: stop_owned(app)
                click(buttons[2 if case == 'cancel' else 4])
                break
            if process.poll() is not None: raise RuntimeError('Installer exited before Retry/Cancel prompt')
            advance(windows); time.sleep(.3)
        else: raise TimeoutError('No Retry/Cancel prompt')
        # NSIS Abort leaves an enabled Close button; success leaves Finish.
        deadline = time.monotonic() + timeout
        while process.poll() is None and time.monotonic() < deadline:
            windows = snapshot(process.pid); evidence['lastWindows'] = windows
            if retry_dialog(windows):
                # Allow the posted click to drain, but never auto-click a second retry.
                time.sleep(.3); continue
            if case == 'cancel':
                # NSIS Abort leaves an "Installation Aborted" page with Cancel
                # (not Close); close that page and its standard quit confirmation.
                for window in windows:
                    body = '\n'.join(x['text'] for x in window['controls'])
                    for control in window['controls']:
                        if control['cls'] != 'Button' or not control['enabled']: continue
                        if (control['id'] == 2 and 'Installation Aborted' in body) or (control['id'] == 6 and 'quit' in body.lower()):
                            click(control)
            advance(windows); time.sleep(.3)
        if process.poll() is None: raise TimeoutError('Installer did not finish after selected action')
        evidence['exitCode'] = process.returncode
        if (case == 'cancel') != (process.returncode != 0): raise RuntimeError('Unexpected installer exit code')
    finally:
        if held: k.CloseHandle(held)
        if process: stop_owned(process)
        if app: stop_owned(app)

def main():
    parser = argparse.ArgumentParser()
    for flag in ('old-installer', 'candidate-installer', 'evidence'): parser.add_argument('--' + flag, type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=180)
    args = parser.parse_args()
    if os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted': raise RuntimeError('Disposable hosted VM required')
    if not c.windll.shell32.IsUserAnAdmin(): raise RuntimeError('Elevated disposable runner required')
    dest = Path(os.environ.get('ProgramW6432', os.environ['ProgramFiles'])) / 'VRCForge'
    if dest.exists(): raise RuntimeError('Initial installation directory must be absent')
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence = dict(ok=False, candidateInstallerSha256=sha(args.candidate_installer), oldInstallerSha256=sha(args.old_installer), cases={})
    try:
        if silent(args.old_installer.resolve(), args.timeout) != 0: raise RuntimeError('Baseline install failed')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\VRCForge') as key:
            winreg.SetValueEx(key, 'InstallerLanguage', 0, winreg.REG_SZ, '1033')
        sentinel = Path(os.environ['LOCALAPPDATA']) / 'VRCForge/agentic-app/config/installer-retry-sentinel.txt'
        sentinel.write_text('preserve-user-data\n', encoding='utf-8')
        expected = {name: sha(dest/name) for name in ('VRCForge.exe', 'backend/vrcforge_backend.exe', 'VERSION', 'payload-integrity.json')}
        evidence['expectedInstalledHashes'] = expected
        baseline_uninstaller = sha(dest/'Uninstall.exe')
        for case in ('cancel', 'retry', 'running-app', 'activation-retry'):
            result = {}; evidence['cases'][case] = result
            interactive(args.candidate_installer.resolve(), dest, case, result, args.timeout)
            actual = {name: sha(dest/name) for name in expected}
            result['installedHashes'] = actual
            if actual != expected or sentinel.read_text(encoding='utf-8') != 'preserve-user-data\n': raise RuntimeError('Payload or user-data preservation mismatch')
            result['uninstallerSha256'] = sha(dest/'Uninstall.exe')
            if case == 'cancel' and result['uninstallerSha256'] != baseline_uninstaller: raise RuntimeError('Cancel replaced the uninstaller')
            if case == 'retry' and result['uninstallerSha256'] == baseline_uninstaller: raise RuntimeError('Retry did not install the hotfix uninstaller')
        held = lock(dest/'VRCForge.exe'); start = time.monotonic()
        try: code = silent(args.candidate_installer.resolve(), args.timeout)
        finally: k.CloseHandle(held)
        evidence['cases']['silent'] = dict(exitCode=code, elapsedSeconds=time.monotonic()-start)
        if code == 0: raise RuntimeError('Silent busy install reported success')
        if {name: sha(dest/name) for name in expected} != expected or sentinel.read_text(encoding='utf-8') != 'preserve-user-data\n': raise RuntimeError('Silent failure changed the installation or user data')
        evidence['ok'] = True
    except Exception as exc:
        evidence['error'] = repr(exc)
        raise
    finally:
        args.evidence.write_text(json.dumps(evidence, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    return 0

if __name__ == '__main__': raise SystemExit(main())
