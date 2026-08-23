"""Read-only Windows probe for Unity modal dialogs that block MCP work."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any


_RELOAD_LABELS = {"reload", "重新加载", "重新載入", "リロード"}


def _normalize_control_label(value: Any) -> str:
    """Normalize the small set of native button-label decorations we accept."""

    return str(value or "").replace("&", "").strip().casefold()


def probe_unity_reload_dialog(project_root: str | Path) -> dict[str, Any]:
    """Return a bounded, non-interactive snapshot of a project's Reload dialog."""

    root = Path(project_root).expanduser().resolve()
    descriptor = root / "Library" / "VRCForge" / "mcp-core.json"
    process_id = 0
    try:
        payload = json.loads(descriptor.read_text(encoding="utf-8"))
        raw_process_id = payload.get("processId") if isinstance(payload, dict) else None
        if isinstance(raw_process_id, int) and not isinstance(raw_process_id, bool):
            process_id = raw_process_id
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass

    result: dict[str, Any] = {
        "schema": "vrcforge.unity_editor_window_blocker.v1",
        "available": os.name == "nt" and process_id > 0,
        "projectPath": str(root),
        "unityProcessId": process_id or None,
        "blocked": False,
        "blockerCode": "",
        "dialog": None,
        "probeError": None,
    }
    if not result["available"]:
        return result

    try:
        windows = _enumerate_process_windows(process_id)
    except Exception as exc:  # noqa: BLE001 - this read-only boundary must fail explicitly.
        result.update(
            {
                "available": False,
                "probeError": {
                    "code": "unity_editor_window_probe_failed",
                    "message": str(exc)[:512] or type(exc).__name__,
                },
            }
        )
        return result
    dialog = classify_reload_dialog(windows)
    if dialog is not None:
        result.update(
            {
                "blocked": True,
                "blockerCode": "unity_editor_reload_dialog",
                "dialog": dialog,
            }
        )
    return result


def classify_reload_dialog(windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Classify a Reload modal from a bounded Win32 window/control snapshot."""

    for window in windows:
        if not isinstance(window, dict) or window.get("visible") is False:
            continue
        controls = window.get("controls") if isinstance(window.get("controls"), list) else []
        texts = [str(window.get("title") or "")]
        texts.extend(str(control.get("text") or "") for control in controls if isinstance(control, dict))
        normalized = {_normalize_control_label(text) for text in texts if str(text).strip()}
        reload_text = next((text for text in normalized if text in _RELOAD_LABELS), "")
        has_reload_button = any(
            isinstance(control, dict)
            and str(control.get("className") or "").casefold() == "button"
            and _normalize_control_label(control.get("text")) in _RELOAD_LABELS
            for control in controls
        )
        is_dialog = str(window.get("className") or "").casefold() == "#32770" or bool(
            window.get("ownerWindow")
        )
        if has_reload_button or (reload_text and is_dialog):
            return {
                "windowHandle": window.get("windowHandle"),
                "title": str(window.get("title") or "")[:256],
                "className": str(window.get("className") or "")[:128],
                "reloadLabel": reload_text,
                "visible": bool(window.get("visible")),
                "enabled": bool(window.get("enabled")),
            }
    return None


def _enumerate_process_windows(process_id: int) -> list[dict[str, Any]]:
    if process_id <= 0:
        return []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_callback, wintypes.LPARAM]
    user32.EnumChildWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.restype = wintypes.BOOL
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND

    def read_text(hwnd: int) -> str:
        length = min(max(int(user32.GetWindowTextLengthW(hwnd)), 0), 4096)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def read_class(hwnd: int) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    windows: list[dict[str, Any]] = []

    @enum_callback
    def collect_window(hwnd: int, _lparam: int) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) != process_id or not user32.IsWindowVisible(hwnd):
            return True
        controls: list[dict[str, Any]] = []

        @enum_callback
        def collect_control(child: int, _child_lparam: int) -> bool:
            text = read_text(child)
            class_name = read_class(child)
            if len(controls) < 256 and (text or class_name.casefold() == "button"):
                controls.append(
                    {
                        "windowHandle": int(child),
                        "text": text[:512],
                        "className": class_name[:128],
                    }
                )
            return True

        user32.EnumChildWindows(hwnd, collect_control, 0)
        if len(windows) < 64:
            windows.append(
                {
                    "windowHandle": int(hwnd),
                    "ownerWindow": int(user32.GetWindow(hwnd, 4) or 0),
                    "title": read_text(hwnd)[:512],
                    "className": read_class(hwnd)[:128],
                    "visible": True,
                    "enabled": bool(user32.IsWindowEnabled(hwnd)),
                    "controls": controls,
                }
            )
        return True

    ctypes.set_last_error(0)
    if not user32.EnumWindows(collect_window, 0):
        error_code = ctypes.get_last_error()
        if error_code:
            raise ctypes.WinError(error_code)
    return windows
