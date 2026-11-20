"""Request Unity's native Assets > Refresh command after installing Core on Windows."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from unity_editor_window_probe import resolve_unity_project_process_id


def request_installed_core_refresh(project: Path) -> dict:
    if os.name != "nt":
        return {"status": "native_menu_unavailable", "nextAction": "check_core_upgrade_status"}
    try:
        pid = resolve_unity_project_process_id(project)
        if pid is None:
            return {"status": "matching_editor_not_found", "nextAction": "check_core_upgrade_status"}
        dispatched = _request_assets_refresh(pid)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"status": "refresh_request_failed", "error": str(exc), "nextAction": "inspect_editor_state"}
    return {
        "status": "refresh_requested" if dispatched else "refresh_menu_unavailable",
        "unityProcessId": pid,
        "nextAction": "check_core_upgrade_status" if dispatched else "inspect_editor_state",
    }


def _request_assets_refresh(pid: int) -> bool:
    # EnumWindows sees only this desktop. All HWND/HMENU values are borrowed.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.GetMenu.argtypes = [wintypes.HWND]
    user32.GetMenu.restype = wintypes.HMENU
    user32.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
    user32.GetSubMenu.restype = wintypes.HMENU
    user32.GetMenuItemCount.argtypes = [wintypes.HMENU]
    user32.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
    user32.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
    user32.GetMenuItemID.restype = wintypes.UINT
    user32.GetMenuState.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    user32.GetMenuState.restype = wintypes.UINT
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

    def owner(hwnd):
        value = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
        return value.value

    def menu_item(menu, labels):
        for pos in range(user32.GetMenuItemCount(menu)):
            text = ctypes.create_unicode_buffer(256)
            user32.GetMenuStringW(menu, pos, text, len(text), 0x400)  # MF_BYPOSITION
            label = text.value.partition("\t")[0].replace("&", "").strip().casefold()
            if label in labels:
                return pos
        return None

    windows = []

    @callback
    def collect(hwnd, _):
        if owner(hwnd) == pid:
            classname = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, classname, len(classname))
            if classname.value == "UnityContainerWndClass" and user32.GetMenu(hwnd):
                windows.append(hwnd)
        return True

    if not user32.EnumWindows(collect, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    matches = []
    for hwnd in windows:
        menu = user32.GetMenu(hwnd)
        assets = menu_item(menu, {"assets", "アセット", "资源", "資源", "에셋"})
        if assets is None:
            continue
        submenu = user32.GetSubMenu(menu, assets)
        refresh = menu_item(submenu, {"refresh", "リフレッシュ", "更新", "刷新", "새로 고침", "새로고침"})
        if refresh is None or user32.GetMenuState(submenu, refresh, 0x400) & 3:
            continue
        command = user32.GetMenuItemID(submenu, refresh)
        if 0 < command <= 0xffff and user32.IsWindowEnabled(hwnd):
            matches.append((hwnd, command))
    if len(matches) != 1:
        return False
    hwnd, command = matches[0]
    if owner(hwnd) != pid:
        return False
    if not user32.PostMessageW(hwnd, 0x0111, command, 0):  # WM_COMMAND, native menu selection.
        raise ctypes.WinError(ctypes.get_last_error())
    return True
