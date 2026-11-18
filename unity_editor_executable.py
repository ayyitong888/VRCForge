from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path


def unity_hub_editor_paths(version: str) -> list[Path]:
    app_data = os.environ.get("APPDATA")
    if not app_data:
        return []
    try:
        catalogue = json.loads((Path(app_data) / "UnityHub" / "editors-v2.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    entries = catalogue.get("data") if isinstance(catalogue, dict) else None
    if not isinstance(entries, list):
        return []
    paths = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("version") != version:
            continue
        locations = entry.get("location")
        if isinstance(locations, list):
            for location in locations:
                if isinstance(location, str):
                    path = Path(location)
                    if path.is_absolute() and path.name.casefold() == "unity.exe":
                        paths.append(path)
    return paths


def read_unity_editor_version(path: Path) -> str | None:
    """Read the full Unity version from the Windows executable without starting it."""
    if sys.platform != "win32":
        return None

    version_api = ctypes.WinDLL("version.dll", winmode=0x00000800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
    size_of = version_api.GetFileVersionInfoSizeW
    size_of.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
    size_of.restype = wintypes.DWORD
    read = version_api.GetFileVersionInfoW
    read.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID)
    read.restype = wintypes.BOOL
    query = version_api.VerQueryValueW
    query.argtypes = (
        wintypes.LPCVOID, wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT),
    )
    query.restype = wintypes.BOOL

    size = size_of(str(path), None)
    if not size:
        return None
    buffer = ctypes.create_string_buffer(size)
    if not read(str(path), 0, size, buffer):
        return None

    value = ctypes.c_void_p()
    length = wintypes.UINT()
    if not query(buffer, r"\VarFileInfo\Translation", ctypes.byref(value), ctypes.byref(length)):
        return None
    translations = ctypes.cast(value, ctypes.POINTER(wintypes.WORD))
    tables = [
        f"{translations[index]:04x}{translations[index + 1]:04x}"
        for index in range(0, length.value // 2 - 1, 2)
    ]
    for table in tables:
        key = fr"\StringFileInfo\{table}\ProductVersion"
        if query(buffer, key, ctypes.byref(value), ctypes.byref(length)) and length.value:
            product_version = ctypes.wstring_at(value, length.value).rstrip("\0")
            return product_version.split("_", 1)[0].strip() or None
    return None
