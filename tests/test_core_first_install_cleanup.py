"""Production install/rollback phase and real Windows copy failure.

Only ordinary temporary files are created, not a Unity project. The scheduling
seam locks a source file after its real identity hash has been read.
"""
import ast
import ctypes
import os
from pathlib import Path

import pytest
import dashboard_server


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows sharing violation")
@pytest.mark.parametrize("previous_core", [False, True])
def test_first_install_copy_failure_removes_its_partial_tree(tmp_path, monkeypatch, previous_core):
    source = tmp_path / "source"
    source.mkdir()
    (source / "first.txt").write_bytes(b"first")
    locked = source / "second.txt"
    locked.write_bytes(b"second")
    Path(str(source) + ".meta").write_bytes(b"root-meta")
    destination = tmp_path / "destination"
    if previous_core:
        destination.mkdir()
        (destination / "old.txt").write_bytes(b"previous-Core")
        Path(str(destination) + ".meta").write_bytes(b"previous-meta")
    original_identity = dashboard_server._unity_core_tree_identity
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = None

    def hash_then_lock(path):
        nonlocal handle
        identity = original_identity(path)
        if Path(path) == source and handle is None:
            handle = kernel.CreateFileW(str(locked), 0x80000000, 0, None, 3, 0, None)
            assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
        return identity

    monkeypatch.setattr(dashboard_server, "_unity_core_tree_identity", hash_then_lock)
    text = Path(dashboard_server.__file__).read_text(encoding="utf-8")
    function = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)
                    and n.name == "install_vrcforge_into_unity_project")
    block = next(n for n in function.body if isinstance(n, ast.Try) and any(
        isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        and call.func.id == "_copy_tree_clean_with_meta" for call in ast.walk(n)))
    initial_names = {"backups", "installed_vrcforge", "install_started", "refresh_signal", "unmanaged_preservation", "legacy_backup", "vrcforge_backup"}
    initial = [n for n in function.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and
               any(isinstance(target, ast.Name) and target.id in initial_names
                   for target in (n.targets if isinstance(n, ast.Assign) else [n.target]))]
    phase = ast.parse("def phase(source_assets, target_vrcforge, backup_root, legacy_target, migrate_legacy=False): pass").body[0]
    phase.body = initial + [block]
    scope = dict(vars(dashboard_server))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[phase], type_ignores=[])),
                 "production-install-phase", "exec"), scope)
    try:
        with pytest.raises(Exception, match="second.txt"):
            scope["phase"](source, destination, tmp_path / "backups", tmp_path / "legacy")
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)
    if previous_core:
        assert sorted(p.name for p in destination.iterdir()) == ["old.txt"]
        assert (destination / "old.txt").read_bytes() == b"previous-Core"
        assert Path(str(destination) + ".meta").read_bytes() == b"previous-meta"
    else:
        assert not destination.exists(), "failed first install retained partial Core"
        assert not Path(str(destination) + ".meta").exists()
    assert (source / "first.txt").read_bytes() == b"first"
    assert locked.read_bytes() == b"second"
