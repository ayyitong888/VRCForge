import ctypes
import os
from pathlib import Path

import pytest
from project_memory_index import scan_project_memory


def baseline(tmp_path):
    root = tmp_path / "files"
    assets = root / "Assets"
    assets.mkdir(parents=True)
    meta = assets / "asset.meta"
    meta.write_text("guid: 0123456789abcdef0123456789abcdef\n", encoding="utf-8")
    storage = tmp_path / "index"
    first = scan_project_memory(root, storage)
    index = Path(first["indexPath"])
    return root, storage, meta, index, index.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows sharing violation")
def test_locked_meta_preserves_existing_index_bytes(tmp_path):
    root, storage, meta, index, original = baseline(tmp_path)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    # Test owns a read-only, exclusive handle to this exact temporary file;
    # no permissions changed, no external interface, always closed below.
    handle = kernel.CreateFileW(str(meta), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        with pytest.raises(OSError):
            scan_project_memory(root, storage)
        assert index.read_bytes() == original
    finally:
        assert kernel.CloseHandle(handle)
    assert scan_project_memory(root, storage)["summary"]["changed"] is False


def test_stat_failure_does_not_report_file_deleted(tmp_path, monkeypatch):
    root, storage, meta, index, original = baseline(tmp_path)
    stat = Path.stat
    def fail(path, *args, **kwargs):
        if path == meta:
            raise PermissionError("metadata access denied")
        return stat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", fail)
        with pytest.raises(OSError):
            scan_project_memory(root, storage)
    assert index.read_bytes() == original


def test_directory_enumeration_error_preserves_index(tmp_path, monkeypatch):
    root, storage, meta, index, original = baseline(tmp_path)
    def walk(path, **kwargs):
        callback = kwargs.get("onerror")
        if callback:
            callback(PermissionError("directory enumeration denied"))
        return iter(())
    monkeypatch.setattr(os, "walk", walk)
    with pytest.raises(OSError):
        scan_project_memory(root, storage)
    assert index.read_bytes() == original
