from __future__ import annotations

import os
from pathlib import Path


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = (
            ("creation_time", ctypes.c_int64),
            ("last_access_time", ctypes.c_int64),
            ("last_write_time", ctypes.c_int64),
            ("change_time", ctypes.c_int64),
            ("file_attributes", wintypes.DWORD),
        )

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _open_file = _kernel32.CreateFileW
    _open_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    _open_file.restype = wintypes.HANDLE
    _query_file = _kernel32.GetFileInformationByHandleEx
    _query_file.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _query_file.restype = wintypes.BOOL
    _close_file = _kernel32.CloseHandle
    _close_file.argtypes = (wintypes.HANDLE,)
    _close_file.restype = wintypes.BOOL
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    _READ_ATTRIBUTES = 0x80
    _SHARE_ALL = 7
    _OPEN_EXISTING = 3
    _OPEN_REPARSE_POINT = 0x00200000
    _FILE_BASIC_INFO = 0
    _WINDOWS_EPOCH_TICKS = 116_444_736_000_000_000


def file_change_time_ns(path: Path, metadata: os.stat_result) -> int | None:
    """Return a reliable content/metadata change stamp when the filesystem exposes one."""
    if os.name != "nt":
        return metadata.st_ctime_ns

    # Python's st_ctime on Windows is creation time. Open for metadata only so
    # this probe does not require file-content access or affect sharing.
    handle = _open_file(str(path), _READ_ATTRIBUTES, _SHARE_ALL, None, _OPEN_EXISTING, _OPEN_REPARSE_POINT, None)
    if not handle or handle == _INVALID_HANDLE:
        return None
    try:
        info = _FileBasicInfo()
        if not _query_file(handle, _FILE_BASIC_INFO, ctypes.byref(info), ctypes.sizeof(info)) or info.change_time <= 0:
            return None
        return (info.change_time - _WINDOWS_EPOCH_TICKS) * 100
    finally:
        _close_file(handle)
