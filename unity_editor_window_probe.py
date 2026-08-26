"""Bounded Windows probes and approved actions for project-bound Unity modals."""

from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any


_RELOAD_LABELS = {"reload", "重新加载", "重新載入", "リロード"}
_RELOAD_CONFIRMATION_SCHEMA = "vrcforge.unity_editor_reload_confirmation.v1"
_BUTTON_CLICK_MESSAGE = 0x00F5


class UnityReloadDialogError(ValueError):
    """Reject an unavailable or drifted project-bound Unity Reload dialog."""


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
        reload_button = next(
            (
                control
                for control in controls
                if isinstance(control, dict)
                and str(control.get("className") or "").casefold() == "button"
                and _normalize_control_label(control.get("text")) in _RELOAD_LABELS
            ),
            None,
        )
        has_reload_button = reload_button is not None
        is_dialog = str(window.get("className") or "").casefold() == "#32770" or bool(
            window.get("ownerWindow")
        )
        if has_reload_button or (reload_text and is_dialog):
            return {
                "windowHandle": window.get("windowHandle"),
                "title": str(window.get("title") or "")[:256],
                "className": str(window.get("className") or "")[:128],
                "reloadLabel": reload_text,
                "reloadButtonHandle": reload_button.get("windowHandle") if reload_button else None,
                "visible": bool(window.get("visible")),
                "enabled": bool(window.get("enabled")),
            }
    return None


def prepare_unity_reload_confirmation(
    params: dict[str, Any],
    _preview: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Freeze the exact project, process, dialog, and Reload button before approval."""

    arguments = params if isinstance(params, dict) else {}
    if arguments.get("confirmReload") is not True:
        raise UnityReloadDialogError("confirmReload=true is required to accept Unity's Reload dialog.")
    project_text = str(arguments.get("projectPath") or "").strip()
    if not project_text:
        raise UnityReloadDialogError("An exact existing Unity projectPath is required.")
    project_root = Path(project_text).expanduser().resolve()
    if not project_root.is_dir():
        raise UnityReloadDialogError("The selected Unity projectPath does not exist.")

    snapshot = probe_unity_reload_dialog(project_root)
    if snapshot.get("probeError"):
        raise UnityReloadDialogError("The Unity Reload dialog could not be inspected safely.")
    dialog = snapshot.get("dialog")
    if not snapshot.get("blocked") or not isinstance(dialog, dict):
        raise UnityReloadDialogError("The selected Unity project has no active Reload dialog.")
    process_id = _positive_handle(snapshot.get("unityProcessId"), "Unity process")
    window_handle = _positive_handle(dialog.get("windowHandle"), "Reload dialog window")
    button_handle = _positive_handle(dialog.get("reloadButtonHandle"), "Reload button")
    if dialog.get("visible") is not True or dialog.get("enabled") is not True:
        raise UnityReloadDialogError("The Unity Reload dialog is not visible and enabled.")

    prepared = {
        "projectPath": str(project_root),
        "confirmReload": True,
        "expectedUnityProcessId": process_id,
        "expectedWindowHandle": window_handle,
        "expectedReloadButtonHandle": button_handle,
    }
    return prepared, {
        "schema": _RELOAD_CONFIRMATION_SCHEMA,
        "projectPath": str(project_root),
        "unityProcessId": process_id,
        "windowHandle": window_handle,
        "reloadButtonHandle": button_handle,
        "reloadLabel": str(dialog.get("reloadLabel") or ""),
        "mayDiscardUnsavedEditorChanges": True,
        "checkpointAvailable": False,
    }


def confirm_unity_reload_dialog(params: dict[str, Any]) -> dict[str, Any]:
    """Click only the exact Reload button frozen by the approved preparation."""

    arguments = params if isinstance(params, dict) else {}
    if arguments.get("confirmReload") is not True:
        raise UnityReloadDialogError("confirmReload=true is required to accept Unity's Reload dialog.")
    project_text = str(arguments.get("projectPath") or "").strip()
    if not project_text:
        raise UnityReloadDialogError("An exact existing Unity projectPath is required.")
    project_root = Path(project_text).expanduser().resolve()
    process_id = _positive_handle(arguments.get("expectedUnityProcessId"), "approved Unity process")
    window_handle = _positive_handle(arguments.get("expectedWindowHandle"), "approved Reload dialog window")
    button_handle = _positive_handle(arguments.get("expectedReloadButtonHandle"), "approved Reload button")

    snapshot = probe_unity_reload_dialog(project_root)
    if snapshot.get("unityProcessId") != process_id:
        raise UnityReloadDialogError("The selected Unity process changed after Reload approval.")
    dialog = snapshot.get("dialog")
    if not snapshot.get("blocked") or not isinstance(dialog, dict):
        raise UnityReloadDialogError("The approved Unity Reload dialog is no longer present.")
    if dialog.get("windowHandle") != window_handle:
        raise UnityReloadDialogError("The approved Unity Reload dialog window changed.")
    if dialog.get("reloadButtonHandle") != button_handle:
        raise UnityReloadDialogError("The approved Unity Reload button changed.")
    if dialog.get("visible") is not True or dialog.get("enabled") is not True:
        raise UnityReloadDialogError("The approved Unity Reload dialog is not visible and enabled.")

    _post_reload_button_click(process_id, window_handle, button_handle)
    deadline = time.monotonic() + 2.0
    while True:
        after = probe_unity_reload_dialog(project_root)
        if not after.get("blocked"):
            return {
                "schema": _RELOAD_CONFIRMATION_SCHEMA,
                "ok": True,
                "projectPath": str(project_root),
                "unityProcessId": process_id,
                "windowHandle": window_handle,
                "reloadButtonHandle": button_handle,
                "reloadClicked": True,
                "dialogClosed": True,
            }
        if time.monotonic() >= deadline:
            raise UnityReloadDialogError("Unity received Reload, but the dialog did not close within two seconds.")
        time.sleep(0.05)


def _positive_handle(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise UnityReloadDialogError(f"An exact {label} identity is required.")
    return value


def _post_reload_button_click(process_id: int, window_handle: int, button_handle: int) -> None:
    """Post BM_CLICK only after live Win32 parent, process, class, and label checks."""

    if os.name != "nt":
        raise UnityReloadDialogError("Unity Reload confirmation requires Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.restype = wintypes.BOOL
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    if not user32.IsWindow(window_handle) or not user32.IsWindow(button_handle):
        raise UnityReloadDialogError("The approved Unity Reload dialog no longer exists.")
    if int(user32.GetParent(button_handle) or 0) != window_handle:
        raise UnityReloadDialogError("The approved Reload button no longer belongs to its dialog.")
    for handle in (window_handle, button_handle):
        actual_process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(actual_process_id))
        if int(actual_process_id.value) != process_id:
            raise UnityReloadDialogError("The approved Reload window belongs to a different process.")
        if not user32.IsWindowVisible(handle) or not user32.IsWindowEnabled(handle):
            raise UnityReloadDialogError("The approved Reload window or button is unavailable.")

    class_name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(button_handle, class_name, len(class_name))
    label = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(button_handle, label, len(label))
    if class_name.value.casefold() != "button" or _normalize_control_label(label.value) not in _RELOAD_LABELS:
        raise UnityReloadDialogError("The approved control is no longer the exact Unity Reload button.")
    ctypes.set_last_error(0)
    if not user32.PostMessageW(button_handle, _BUTTON_CLICK_MESSAGE, 0, 0):
        error_code = ctypes.get_last_error()
        raise UnityReloadDialogError(
            f"The approved Unity Reload button could not be clicked: Windows error {error_code}."
        )


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
