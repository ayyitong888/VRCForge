"""Real archive service and Windows file failures; no Unity or App ledger writes."""
import ctypes
import os
from contextlib import contextmanager

import pytest

from agent_gateway import AgentGateway


@contextmanager
def deny_replace(path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0, None)
    assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        yield
    finally:
        assert kernel.CloseHandle(handle)


@pytest.mark.parametrize("failure", ["directory", "locked", "none"])
def test_archive_restore_preserves_all_current_files_on_failure(tmp_path, failure):
    if failure == "locked" and os.name != "nt":
        pytest.skip("Requires actual Windows delete-sharing denial")
    service = AgentGateway(tmp_path / "config.json", tmp_path / "audit").checkpoint_recovery
    project = tmp_path / "files"
    assets = project / "Assets"
    assets.mkdir(parents=True)
    first, second, extra = (assets / name for name in ("A.txt", "B.txt", "0-extra.txt"))
    first.write_bytes(b"snapshot-A")
    second.write_bytes(b"snapshot-B")
    checkpoint = service._create_archive_checkpoint(project, {
        "id": "ckpt_atomic", "projectRoot": str(project), "status": "unavailable"})
    assert checkpoint["ok"]
    first.write_bytes(b"current-A")
    second.write_bytes(b"current-B")
    extra.write_bytes(b"current-extra")
    if failure == "directory":
        second.unlink()
        second.mkdir()
    assert service._checkpoint_available(checkpoint)["ok"]
    if failure == "locked":
        with deny_replace(second):
            result = service._restore_archive_checkpoint(checkpoint)
    else:
        result = service._restore_archive_checkpoint(checkpoint)
    assert result["ok"] is (failure == "none"), result
    if failure == "none":
        assert first.read_bytes() == b"snapshot-A"
        assert second.read_bytes() == b"snapshot-B"
        assert not extra.exists()
    else:
        assert first.read_bytes() == b"current-A"
        assert extra.read_bytes() == b"current-extra"
        assert second.is_dir() if failure == "directory" else second.read_bytes() == b"current-B"
    assert not list(assets.glob("*.tmp"))
