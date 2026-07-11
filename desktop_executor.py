from __future__ import annotations

import ctypes
import os
import re
import struct
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from desktop_apps import DesktopAppCatalogError, WindowsAppCatalog
from desktop_capture import DesktopCaptureError, WindowsGraphicsCapture
from desktop_operations import WINDOWS_DESKTOP_OPERATIONS, canonical_desktop_operation, canonical_desktop_params
from desktop_uia import DesktopUiaCancelled, DesktopUiaError, WindowsUiaAdapter


class DesktopExecutorError(RuntimeError):
    pass


class DesktopActionCancelled(DesktopExecutorError):
    pass


CancelCheck = Callable[[], bool]


class DesktopController(Protocol):
    def execute(self, action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]: ...


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("payload",)
    _fields_ = [("type", wintypes.DWORD), ("payload", _INPUTUNION)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _RGBQUAD * 1)]


_ENUMWINDOWSPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)


class WindowsDesktopController:
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    MOUSEEVENTF_MOVE = 0x0001
    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010
    MOUSEEVENTF_MIDDLEDOWN = 0x0020
    MOUSEEVENTF_MIDDLEUP = 0x0040
    MOUSEEVENTF_WHEEL = 0x0800
    MOUSEEVENTF_HWHEEL = 0x1000
    WHEEL_DELTA = 120
    SW_RESTORE = 9
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    SRCCOPY = 0x00CC0020
    CAPTUREBLT = 0x40000000
    DIB_RGB_COLORS = 0
    PW_RENDERFULLCONTENT = 0x00000002
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    PROTECTED_PROCESS_NAMES = {
        "cmd.exe",
        "conhost.exe",
        "credentialuibroker.exe",
        "keepass.exe",
        "keepassxc.exe",
        "lockapp.exe",
        "msmpeng.exe",
        "powershell.exe",
        "pwsh.exe",
        "securityhealthservice.exe",
        "securityhealthsystray.exe",
        "windowsterminal.exe",
        "wt.exe",
        "1password.exe",
        "bitwarden.exe",
        "chatgpt.exe",
        "codex.exe",
    }
    PROTECTED_WINDOW_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "Credential Dialog Xaml Host"}
    SELF_PROCESS_NAMES = {"vrcforge.exe", "vrcforge_backend.exe"}

    KEY_CODES = {
        "backspace": 0x08,
        "tab": 0x09,
        "enter": 0x0D,
        "return": 0x0D,
        "shift": 0x10,
        "shiftl": 0xA0,
        "shiftr": 0xA1,
        "ctrl": 0x11,
        "control": 0x11,
        "controll": 0xA2,
        "controlr": 0xA3,
        "alt": 0x12,
        "altl": 0xA4,
        "altr": 0xA5,
        "pause": 0x13,
        "capslock": 0x14,
        "escape": 0x1B,
        "esc": 0x1B,
        "space": 0x20,
        "pageup": 0x21,
        "pagedown": 0x22,
        "end": 0x23,
        "home": 0x24,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "insert": 0x2D,
        "delete": 0x2E,
        "menu": 0x5D,
        "apps": 0x5D,
        "win": 0x5B,
        "windows": 0x5B,
        "comma": 0xBC,
        "less": 0xBC,
        "period": 0xBE,
        "greater": 0xBE,
        "slash": 0xBF,
        "question": 0xBF,
        "semicolon": 0xBA,
        "apostrophe": 0xDE,
        "quote": 0xDE,
        "bracketleft": 0xDB,
        "bracketright": 0xDD,
        "backslash": 0xDC,
        "minus": 0xBD,
        "equal": 0xBB,
        "grave": 0xC0,
        "numpadadd": 0x6B,
        "kpadd": 0x6B,
        "numpadsubtract": 0x6D,
        "kpsubtract": 0x6D,
        "numpadmultiply": 0x6A,
        "kpmultiply": 0x6A,
        "numpaddivide": 0x6F,
        "kpdivide": 0x6F,
        "numpaddecimal": 0x6E,
        "kpdecimal": 0x6E,
        "numpadenter": 0x0D,
        "kpenter": 0x0D,
    }

    def __init__(self, capture_dir: Path) -> None:
        if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
            raise DesktopExecutorError("The native desktop executor is available only on Windows.")
        self.capture_dir = capture_dir
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.apps = WindowsAppCatalog()
        self.capture = WindowsGraphicsCapture()
        self.uia = WindowsUiaAdapter()
        self._uia_snapshots: dict[int, dict[str, Any]] = {}
        self._screenshot_snapshots: dict[str, dict[str, Any]] = {}
        self._configure_signatures()
        try:
            if hasattr(self.user32, "SetProcessDpiAwarenessContext"):
                self.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            else:
                self.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

    def _configure_signatures(self) -> None:
        self.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        self.user32.SetCursorPos.restype = wintypes.BOOL
        self.user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        self.user32.GetCursorPos.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        self.user32.GetWindowTextLengthW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetClassNameW.restype = ctypes.c_int
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.EnumWindows.argtypes = [_ENUMWINDOWSPROC, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUMWINDOWSPROC, wintypes.LPARAM]
        self.user32.EnumChildWindows.restype = wintypes.BOOL
        self.user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
        self.user32.GetDlgCtrlID.restype = ctypes.c_int
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.SetForegroundWindow.restype = wintypes.BOOL
        self.user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self.user32.BringWindowToTop.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        self.user32.AttachThreadInput.restype = wintypes.BOOL
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self.user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        self.user32.PrintWindow.restype = wintypes.BOOL
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        self.gdi32.BitBlt.restype = wintypes.BOOL
        self.gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.LPVOID,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
        ]
        self.gdi32.GetDIBits.restype = ctypes.c_int
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

    def execute(self, action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        params = canonical_desktop_params(action.get("params"))
        operation = self._normalize_operation(params.get("operation"))
        if not operation and str(action.get("action") or "") == "desktop_rescue":
            operation = "list_windows"
        if operation not in WINDOWS_DESKTOP_OPERATIONS:
            raise DesktopExecutorError(
                "Computer Use requires params.operation: " + ", ".join(sorted(WINDOWS_DESKTOP_OPERATIONS))
            )
        return self._execute_operation(operation, params, action, cancel_check)

    @staticmethod
    def _normalize_operation(value: Any) -> str:
        return canonical_desktop_operation(value)

    def _execute_operation(
        self,
        operation: str,
        params: dict[str, Any],
        action: dict[str, Any],
        cancel_check: CancelCheck,
    ) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        if operation == "sequence":
            raw_steps = params.get("steps")
            if not isinstance(raw_steps, list) or not raw_steps:
                raise DesktopExecutorError("sequence requires a non-empty params.steps list.")
            if len(raw_steps) > 32:
                raise DesktopExecutorError("sequence supports at most 32 steps.")
            normalized_steps: list[tuple[str, dict[str, Any]]] = []
            target_keys = ("windowHandle", "hwnd", "titleContains", "windowTitle", "processId", "pid", "app", "processPath")
            target_required = {
                "focus_window",
                "click",
                "drag",
                "scroll",
                "type_text",
                "key_press",
                "focus_element",
                "invoke_element",
                "set_value",
                "secondary_action",
            }
            sticky_target: dict[str, Any] = {}
            for index, raw_step in enumerate(raw_steps):
                if not isinstance(raw_step, dict):
                    raise DesktopExecutorError(f"sequence step {index + 1} must be an object.")
                step_operation = self._normalize_operation(raw_step.get("operation"))
                if step_operation not in WINDOWS_DESKTOP_OPERATIONS or step_operation == "sequence":
                    raise DesktopExecutorError(f"Unsupported sequence operation at step {index + 1}: {step_operation or 'missing'}")
                step_params = canonical_desktop_params(raw_step)
                step_params["operation"] = step_operation
                has_target = any(step_params.get(key) not in (None, "") for key in target_keys)
                if step_operation in target_required and not has_target and sticky_target:
                    step_params.update(sticky_target)
                    has_target = True
                if has_target:
                    sticky_target = {
                        key: step_params[key]
                        for key in target_keys
                        if step_params.get(key) not in (None, "")
                    }
                self._validate_operation_params(step_operation, step_params)
                normalized_steps.append((step_operation, step_params))
            results: list[dict[str, Any]] = []
            for index, (step_operation, step_params) in enumerate(normalized_steps):
                result = self._execute_operation(step_operation, step_params, action, cancel_check)
                results.append({"index": index + 1, "operation": step_operation, "result": result})
            return {"operation": operation, "stepCount": len(results), "steps": results, "summary": "Desktop sequence completed."}
        self._validate_operation_params(operation, params)
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any], CancelCheck], dict[str, Any]]] = {
            "list_apps": self._list_apps,
            "launch_app": self._launch_app,
            "list_windows": self._list_windows,
            "get_window": self._get_window,
            "window_state": self._window_state,
            "inspect_window": self._inspect_window,
            "cursor_position": self._cursor_position,
            "screenshot": self._screenshot,
            "focus_window": self._focus_window,
            "move_pointer": self._move_pointer,
            "click": self._click,
            "drag": self._drag,
            "scroll": self._scroll,
            "type_text": self._type_text,
            "key_press": self._key_press,
            "focus_element": self._uia_action,
            "invoke_element": self._uia_action,
            "set_value": self._uia_action,
            "secondary_action": self._uia_action,
            "wait": self._wait,
        }
        return handlers[operation](params, action, cancel_check)

    def _validate_operation_params(self, operation: str, params: dict[str, Any]) -> None:
        has_window_target = (
            params.get("windowHandle") not in (None, "")
            or params.get("hwnd") not in (None, "")
            or bool(str(params.get("titleContains") or params.get("windowTitle") or "").strip())
        )
        if operation == "launch_app":
            selector = str(params.get("app") or params.get("appId") or params.get("name") or "").strip()
            if not selector:
                raise DesktopExecutorError("launch_app requires app, appId, or name from list_apps.")
            if len(selector) > 512 or any(character in selector for character in "\r\n\0"):
                raise DesktopExecutorError("The app selector is invalid.")
        elif operation in {"get_window", "window_state", "inspect_window", "focus_window"}:
            self._resolve_window(params)
        elif operation == "screenshot":
            if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
                self._resolve_window(params)
            elif isinstance(params.get("region"), dict):
                region = params["region"]
                if int(region.get("width") or 0) <= 0 or int(region.get("height") or 0) <= 0:
                    raise DesktopExecutorError("Screenshot region width and height must be positive.")
        elif operation in {"move_pointer", "click"}:
            if operation == "click" and not has_window_target:
                raise DesktopExecutorError("click requires a target window from list_apps or list_windows.")
            element_target = operation == "click" and any(
                params.get(key) not in (None, "") for key in ("elementIndex", "automationId", "name")
            )
            if element_target:
                self._resolve_window(params)
            else:
                self._point_from_params(params)
            if operation == "click":
                if str(params.get("button") or "left").strip().lower() not in {"left", "right", "middle"}:
                    raise DesktopExecutorError("click button must be left, right, or middle.")
                int(params.get("clicks") or 1)
                int(params.get("intervalMs") or 80)
        elif operation == "drag":
            if not has_window_target:
                raise DesktopExecutorError("drag requires a target window from list_apps or list_windows.")
            self._drag_points(params)
            int(params.get("durationMs") or 500)
            int(params.get("steps") or 20)
        elif operation == "scroll":
            if not has_window_target:
                raise DesktopExecutorError("scroll requires a target window from list_apps or list_windows.")
            if "x" not in params or "y" not in params:
                raise DesktopExecutorError("scroll requires both x and y coordinates inside the target window.")
            self._point_from_params(params)
            int(params.get("scrollX") or params.get("scroll_x") or 0)
            int(params.get("scrollY") or params.get("scroll_y") or params.get("delta") or (int(params.get("notches") or -3) * self.WHEEL_DELTA))
        elif operation == "type_text":
            if not has_window_target:
                raise DesktopExecutorError("type_text requires a target window from list_apps or list_windows.")
            text = str(params.get("text") or "")
            if not text or len(text) > 8000:
                raise DesktopExecutorError("type_text requires between 1 and 8000 characters.")
            if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
                self._resolve_window(params)
            int(params.get("delayMs") or 0)
        elif operation == "key_press":
            if not has_window_target:
                raise DesktopExecutorError("key_press requires a target window from list_apps or list_windows.")
            keys = self._key_names_from_params(params)
            if not keys or len(keys) > 8:
                raise DesktopExecutorError("key_press requires one to eight keys.")
            for key in keys:
                self._virtual_key(key)
            int(params.get("repeat") or 1)
            int(params.get("intervalMs") or 50)
        elif operation == "wait":
            int(params.get("durationMs") or params.get("ms") or 500)
        elif operation in {"focus_element", "invoke_element", "set_value", "secondary_action"}:
            self._resolve_window(params)
            if not any(params.get(key) not in (None, "") for key in ("elementIndex", "automationId", "name")):
                raise DesktopExecutorError("UI Automation actions require elementIndex, automationId, or name.")
            if operation == "set_value":
                if "value" not in params and "text" not in params:
                    raise DesktopExecutorError("set_value requires value.")
                if len(str(params.get("value") if "value" in params else params.get("text") or "")) > 8000:
                    raise DesktopExecutorError("set_value supports at most 8000 characters.")
            if operation == "secondary_action":
                action = re.sub(r"[^a-z0-9]+", "_", str(params.get("action") or "").strip().lower()).strip("_")
                supported = {
                    "raise",
                    "focus",
                    "invoke",
                    "select",
                    "expand",
                    "collapse",
                    "toggle",
                    "scroll_up",
                    "scroll_down",
                    "scroll_left",
                    "scroll_right",
                    "scroll_into_view",
                }
                if action not in supported:
                    raise DesktopExecutorError("secondary_action is not one of the supported UI Automation actions.")

    @staticmethod
    def _raise_if_cancelled(cancel_check: CancelCheck) -> None:
        if cancel_check():
            raise DesktopActionCancelled("Desktop action was cancelled by the user.")

    def _virtual_screen_rect(self) -> dict[str, int]:
        left = int(self.user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN))
        top = int(self.user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN))
        width = int(self.user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN))
        height = int(self.user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN))
        return {"left": left, "top": top, "width": width, "height": height, "right": left + width, "bottom": top + height}

    def _window_title(self, hwnd: int) -> str:
        length = int(self.user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def _process_path(self, process_id: int) -> str:
        if process_id <= 0:
            return ""
        handle = self.kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return ""
        try:
            capacity = 32768
            size = wintypes.DWORD(capacity)
            buffer = ctypes.create_unicode_buffer(capacity)
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return buffer.value[: int(size.value)]
        finally:
            self.kernel32.CloseHandle(handle)

    def _window_info(self, hwnd: int) -> dict[str, Any]:
        rect = _RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise DesktopExecutorError(f"Unable to read window bounds for handle {hwnd}.")
        class_buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        process_path = self._process_path(int(process_id.value))
        return {
            "windowHandle": int(hwnd),
            "id": int(hwnd),
            "title": self._window_title(hwnd),
            "className": class_buffer.value,
            "processId": int(process_id.value),
            "processPath": process_path,
            "app": process_path or f"pid:{int(process_id.value)}",
            "rect": {
                "left": int(rect.left),
                "top": int(rect.top),
                "right": int(rect.right),
                "bottom": int(rect.bottom),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
            },
            "minimized": bool(self.user32.IsIconic(hwnd)),
            "foreground": int(self.user32.GetForegroundWindow() or 0) == int(hwnd),
        }

    def _enumerate_windows(self, *, include_untitled: bool = False) -> list[dict[str, Any]]:
        windows: list[dict[str, Any]] = []
        @_ENUMWINDOWSPROC
        def collect(hwnd: int, _lparam: int) -> bool:
            try:
                if not self.user32.IsWindowVisible(hwnd):
                    return True
                title = self._window_title(hwnd)
                if not title and not include_untitled:
                    return True
                info = self._window_info(hwnd)
                rect = info["rect"]
                if rect["width"] <= 0 or rect["height"] <= 0:
                    return True
                windows.append(info)
            except Exception:
                return True
            return True

        if not self.user32.EnumWindows(collect, 0):
            raise DesktopExecutorError("EnumWindows failed.")
        return windows

    def _resolve_window(self, params: dict[str, Any]) -> dict[str, Any]:
        handle_value = params.get("windowHandle") or params.get("hwnd")
        if handle_value not in (None, ""):
            try:
                hwnd = int(str(handle_value), 0)
            except ValueError as exc:
                raise DesktopExecutorError("windowHandle must be an integer or 0x-prefixed handle.") from exc
            if not self.user32.IsWindow(hwnd):
                raise DesktopExecutorError(f"Window handle {hwnd} is no longer valid.")
            return self._validate_window_identity(self._window_info(hwnd), params)
        title_contains = str(params.get("titleContains") or params.get("windowTitle") or "").strip().casefold()
        if not title_contains:
            raise DesktopExecutorError("A windowHandle or titleContains value is required.")
        matches = [item for item in self._enumerate_windows() if title_contains in str(item.get("title") or "").casefold()]
        process_id = params.get("processId") or params.get("pid")
        if process_id not in (None, ""):
            try:
                expected_process_id = int(process_id)
            except (TypeError, ValueError) as exc:
                raise DesktopExecutorError("processId must be an integer.") from exc
            matches = [item for item in matches if int(item.get("processId") or 0) == expected_process_id]
        if not matches:
            raise DesktopExecutorError(f"No visible window title contains: {title_contains}")
        exact_matches = [item for item in matches if str(item.get("title") or "").casefold() == title_contains]
        if len(exact_matches) == 1:
            return self._validate_window_identity(exact_matches[0], params)
        if len(matches) > 1:
            candidates = ", ".join(
                f"{item.get('title') or '<untitled>'} (pid {item.get('processId')}, hwnd {item.get('windowHandle')})"
                for item in matches[:5]
            )
            raise DesktopExecutorError(
                "Window title is ambiguous; use an exact title, processId, or windowHandle. Matches: " + candidates
            )
        return self._validate_window_identity(matches[0], params)

    def _validate_window_identity(self, window: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        process_id = params.get("processId") or params.get("pid")
        if process_id not in (None, ""):
            try:
                expected_process_id = int(process_id)
            except (TypeError, ValueError) as exc:
                raise DesktopExecutorError("processId must be an integer.") from exc
            if int(window.get("processId") or 0) != expected_process_id:
                raise DesktopExecutorError("The window handle no longer belongs to the observed process.")
        expected_app = str(params.get("app") or params.get("processPath") or "").strip()
        actual_app = str(window.get("processPath") or window.get("app") or "").strip()
        if expected_app and actual_app and expected_app.casefold() != actual_app.casefold():
            raise DesktopExecutorError("The window handle no longer belongs to the observed application.")
        return window

    def _protected_target_reason(self, window: dict[str, Any]) -> str:
        process_name = Path(str(window.get("processPath") or "")).name.casefold()
        class_name = str(window.get("className") or "")
        if process_name in self.PROTECTED_PROCESS_NAMES:
            return f"Computer Use is blocked for protected application {process_name}."
        if class_name in self.PROTECTED_WINDOW_CLASSES:
            return f"Computer Use is blocked for protected window class {class_name}."
        return ""

    def _self_target_reason(self, window: dict[str, Any]) -> str:
        process_name = Path(str(window.get("processPath") or "")).name.casefold()
        if process_name in self.SELF_PROCESS_NAMES:
            return "Computer Use cannot control or inspect VRCForge's own window; target another application."
        return ""

    def _resolve_read_window(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._resolve_window(params)
        reason = self._protected_target_reason(window)
        if reason:
            raise DesktopExecutorError(reason)
        return window

    def _resolve_input_window(self, params: dict[str, Any]) -> dict[str, Any]:
        window = self._resolve_read_window(params)
        reason = self._self_target_reason(window)
        if reason:
            raise DesktopExecutorError(reason)
        return window

    def _list_windows(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        title_contains = str(params.get("titleContains") or "").strip().casefold()
        limit = max(1, min(int(params.get("limit") or 50), 100))
        windows = self._enumerate_windows(include_untitled=bool(params.get("includeUntitled")))
        if title_contains:
            windows = [item for item in windows if title_contains in str(item.get("title") or "").casefold()]
        windows = windows[:limit]
        return {"operation": "list_windows", "count": len(windows), "windows": windows, "summary": f"Found {len(windows)} visible windows."}

    def _list_apps(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        try:
            catalog_apps = self.apps.list_apps(refresh=bool(params.get("refresh")))
        except DesktopAppCatalogError as exc:
            raise DesktopExecutorError(str(exc)) from exc
        apps: list[dict[str, Any]] = [
            {
                "displayName": app["name"],
                "name": app["name"],
                "id": app["appId"],
                "appId": app["appId"],
                "isRunning": False,
                "windows": [],
            }
            for app in catalog_apps
        ]
        running: dict[str, dict[str, Any]] = {}
        for window in self._enumerate_windows():
            app_id = str(window.get("processPath") or window.get("app") or f"pid:{window.get('processId')}")
            key = app_id.casefold()
            app = running.get(key)
            if app is None:
                display_name = Path(app_id).stem if window.get("processPath") else str(window.get("title") or app_id)
                app = {
                    "displayName": display_name,
                    "name": display_name,
                    "id": app_id,
                    "appId": app_id,
                    "isRunning": True,
                    "windows": [],
                }
                running[key] = app
            app["windows"].append(window)
        apps.extend(running.values())
        query = str(params.get("query") or params.get("nameContains") or "").strip().casefold()
        if query:
            apps = [
                app
                for app in apps
                if query in str(app["name"]).casefold() or query in str(app["appId"]).casefold()
            ]
        apps.sort(key=lambda item: (not bool(item["isRunning"]), str(item["displayName"]).casefold(), str(item["id"]).casefold()))
        limit = max(1, min(int(params.get("limit") or 200), 500))
        apps = apps[:limit]
        return {
            "operation": "list_apps",
            "count": len(apps),
            "apps": apps,
            "summary": f"Found {len(apps)} registered Windows applications.",
        }

    def _launch_app(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        selector = str(params.get("app") or params.get("appId") or params.get("name") or "").strip()
        before = {int(item["windowHandle"]) for item in self._enumerate_windows(include_untitled=True)}
        try:
            candidate = self.apps.resolve_app(selector)
            process_name = Path(str(candidate.get("appId") or "")).name.casefold()
            display_name = str(candidate.get("name") or "").strip().casefold()
            if process_name in self.PROTECTED_PROCESS_NAMES or display_name in {
                "command prompt",
                "powershell",
                "windows powershell",
                "windows terminal",
                "windows security",
                "chatgpt",
                "codex",
                "1password",
                "bitwarden",
                "keepass",
                "keepassxc",
            }:
                raise DesktopExecutorError(f"Computer Use is blocked from launching protected application {candidate['name']}.")
            app = self.apps.launch_app(selector)
        except DesktopAppCatalogError as exc:
            raise DesktopExecutorError(str(exc)) from exc
        timeout_ms = max(250, min(int(params.get("timeoutMs") or 5000), 15_000))
        deadline = time.monotonic() + timeout_ms / 1000
        candidates: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            self._raise_if_cancelled(cancel_check)
            windows = self._enumerate_windows()
            candidates = [item for item in windows if int(item["windowHandle"]) not in before]
            if not candidates:
                app_name = app["name"].casefold()
                candidates = [item for item in windows if app_name in str(item.get("title") or "").casefold()]
            if candidates:
                break
            time.sleep(0.1)
        return {
            "operation": "launch_app",
            "app": app,
            "launched": True,
            "window": candidates[0] if len(candidates) == 1 else None,
            "windows": candidates[:10],
            "windowDetected": bool(candidates),
            "summary": f"Asked Windows to launch {app['name']}.",
        }

    def _get_window(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        window = self._resolve_window(params)
        return {"operation": "get_window", "window": window, "summary": f"Resolved window: {window['title'] or window['windowHandle']}."}

    def _window_state(self, params: dict[str, Any], action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        include_screenshot = params.get("includeScreenshot", params.get("include_screenshot", True)) is not False
        include_text = params.get("includeText", params.get("include_text", False)) is True
        window = self._resolve_read_window(params)
        if include_text:
            reason = self._self_target_reason(window)
            if reason:
                raise DesktopExecutorError(reason)
        resolved = {**params, "windowHandle": int(window["windowHandle"]), "titleContains": ""}
        inspection = self._inspect_window(resolved, action, cancel_check) if include_text else None
        screenshot = self._screenshot(resolved, action, cancel_check) if include_screenshot else None
        screenshots = []
        if screenshot:
            screenshots.append(
                {
                    **screenshot,
                    "id": Path(str(screenshot.get("artifactRelativePath") or screenshot.get("artifactPath") or "capture")).name,
                    "originX": screenshot.get("left"),
                    "originY": screenshot.get("top"),
                    "zIndex": 0,
                }
            )
        accessibility = None
        if inspection:
            accessibility = {
                **inspection,
                "focused_element": inspection.get("focusedElementText") or "",
                "selected_elements": inspection.get("selectedElementTexts") or [],
                "selected_text": inspection.get("selectedText") or "",
                "document_text": inspection.get("documentText") or "",
            }
        if screenshot and accessibility:
            summary = "Read the target window screenshot and accessibility state."
        elif screenshot:
            summary = "Read the target window screenshot."
        elif accessibility:
            summary = "Read the target window accessibility state."
        else:
            summary = "Resolved the target window without requesting screenshot or accessibility data."
        return {
            "operation": "window_state",
            "window": window,
            "screenshot": screenshot,
            "screenshots": screenshots,
            "accessibility": accessibility,
            "summary": summary,
        }

    @staticmethod
    def _format_accessibility_element(element: dict[str, Any]) -> str:
        index = int(element.get("index") or 0)
        control_type = str(element.get("controlType") or "ControlType.Custom").removeprefix("ControlType.")
        name = str(element.get("name") or "").replace("\r", " ").replace("\n", " ").strip()
        automation_id = str(element.get("automationId") or "").strip()
        details = [f'[{index}] {control_type} "{name[:160]}"']
        if automation_id:
            details.append(f"automationId={automation_id[:120]}")
        if element.get("focused"):
            details.append("focused")
        if element.get("selected"):
            details.append("selected")
        return " ".join(details)

    @classmethod
    def _format_accessibility_tree(cls, window: dict[str, Any], elements: list[dict[str, Any]]) -> str:
        lines = [f'Window: "{str(window.get("title") or "")[:240]}", App: {str(window.get("app") or "")[:320]}']
        for element in elements:
            depth = max(0, min(int(element.get("depth") or 0), 12))
            lines.append(f"{'  ' * depth}{cls._format_accessibility_element(element)}")
        return "\n".join(lines)[:32_000]

    def _inspect_window(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        window = self._resolve_input_window(params)
        limit = max(1, min(int(params.get("limit") or 120), 300))
        controls: list[dict[str, Any]] = []
        @_ENUMWINDOWSPROC
        def collect(hwnd: int, _lparam: int) -> bool:
            if len(controls) >= limit:
                return False
            try:
                info = self._window_info(hwnd)
                info["controlId"] = int(self.user32.GetDlgCtrlID(hwnd))
                info["visible"] = bool(self.user32.IsWindowVisible(hwnd))
                controls.append(info)
            except Exception:
                pass
            return True

        self.user32.EnumChildWindows(int(window["windowHandle"]), collect, 0)
        result = {
            "operation": "inspect_window",
            "window": window,
            "count": len(controls),
            "nativeControls": controls,
            "truncated": len(controls) >= limit,
            "accessibilityTree": False,
            "summary": f"Inspected {len(controls)} native child controls.",
        }
        try:
            accessibility = self.uia.inspect(int(window["windowHandle"]), limit=limit, cancel_check=cancel_check)
            elements = accessibility.get("elements") if isinstance(accessibility.get("elements"), list) else []
            result.update(
                {
                    "count": len(elements),
                    "controls": elements,
                    "treeItems": accessibility.get("tree") if isinstance(accessibility.get("tree"), list) else elements,
                    "tree": self._format_accessibility_tree(window, elements),
                    "focusedElement": accessibility.get("focusedElement"),
                    "focusedElementText": self._format_accessibility_element(accessibility["focusedElement"])
                    if isinstance(accessibility.get("focusedElement"), dict)
                    else "",
                    "selectedElements": accessibility.get("selectedElements")
                    if isinstance(accessibility.get("selectedElements"), list)
                    else [],
                    "selectedElementTexts": [
                        self._format_accessibility_element(item)
                        for item in accessibility.get("selectedElements", [])
                        if isinstance(item, dict)
                    ],
                    "documentText": str(accessibility.get("documentText") or ""),
                    "selectedText": str(accessibility.get("selectedText") or ""),
                    "accessibilityTree": True,
                    "truncated": bool(accessibility.get("truncated")),
                    "summary": f"Inspected {len(elements)} UI Automation elements.",
                }
            )
            self._cache_uia_snapshot(window, elements)
        except DesktopUiaCancelled as exc:
            raise DesktopActionCancelled(str(exc)) from exc
        except DesktopUiaError as exc:
            result["controls"] = controls
            result["accessibilityError"] = str(exc)[-500:]
        return result

    def _cache_uia_snapshot(self, window: dict[str, Any], elements: list[dict[str, Any]]) -> None:
        hwnd = int(window["windowHandle"])
        snapshots = getattr(self, "_uia_snapshots", None)
        if not isinstance(snapshots, dict):
            snapshots = {}
            self._uia_snapshots = snapshots
        snapshots[hwnd] = {
            "observedAt": time.monotonic(),
            "processId": int(window.get("processId") or 0),
            "processPath": str(window.get("processPath") or ""),
            "elements": [dict(item) for item in elements[:500] if isinstance(item, dict)],
        }
        if len(snapshots) > 20:
            oldest = min(snapshots, key=lambda key: float(snapshots[key].get("observedAt") or 0))
            snapshots.pop(oldest, None)

    def _observed_element_expectations(self, window: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        if params.get("elementIndex") in (None, ""):
            return {}
        hwnd = int(window["windowHandle"])
        snapshot = getattr(self, "_uia_snapshots", {}).get(hwnd)
        if not isinstance(snapshot, dict) or time.monotonic() - float(snapshot.get("observedAt") or 0) > 120:
            raise DesktopExecutorError(
                "The element index has no fresh accessibility observation; call window_state or inspect_window with text first."
            )
        if int(snapshot.get("processId") or 0) != int(window.get("processId") or 0):
            raise DesktopExecutorError("The element index belongs to a stale window process.")
        try:
            index = int(params["elementIndex"])
        except (TypeError, ValueError) as exc:
            raise DesktopExecutorError("elementIndex must be an integer.") from exc
        elements = snapshot.get("elements") if isinstance(snapshot.get("elements"), list) else []
        observed = next((item for item in elements if int(item.get("index") or 0) == index), None)
        if not isinstance(observed, dict):
            raise DesktopExecutorError("The element index was not present in the latest accessibility observation.")
        return {
            "expectedName": str(observed.get("name") or ""),
            "expectedAutomationId": str(observed.get("automationId") or ""),
            "expectedControlType": str(observed.get("controlType") or ""),
            "expectedClassName": str(observed.get("className") or ""),
        }

    def _uia_action(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        self._focus_window(params, {}, cancel_check)
        window = self._resolve_input_window(params)
        operation = self._normalize_operation(params.get("operation"))
        operation_map = {
            "focus_element": "focus",
            "invoke_element": "invoke",
            "set_value": "set_value",
            "secondary_action": "secondary_action",
        }
        request = {
            "operation": operation_map[operation],
            "windowHandle": int(window["windowHandle"]),
            "limit": max(1, min(int(params.get("limit") or 500), 500)),
        }
        for key in ("elementIndex", "automationId", "name", "controlType", "action"):
            if params.get(key) not in (None, ""):
                request[key] = params[key]
        request.update(self._observed_element_expectations(window, params))
        if operation == "secondary_action":
            request["action"] = re.sub(
                r"[^a-z0-9]+",
                "_",
                str(params.get("action") or "").strip().lower(),
            ).strip("_")
        if operation == "set_value":
            request["value"] = str(params.get("value") or params.get("text") or "")
        try:
            result = self.uia.execute(request, cancel_check)
        except DesktopUiaCancelled as exc:
            raise DesktopActionCancelled(str(exc)) from exc
        except DesktopUiaError as exc:
            raise DesktopExecutorError(str(exc)) from exc
        self._raise_if_cancelled(cancel_check)
        performed = str(result.get("performed") or "")
        if operation == "set_value" and performed == "keyboard_replace_required":
            value = str(request.get("value") or "")
            element = result.get("element") if isinstance(result.get("element"), dict) else {}
            rect = element.get("rect") if isinstance(element.get("rect"), dict) else {}
            window_rect = window.get("rect") if isinstance(window.get("rect"), dict) else {}
            width = int(rect.get("width") or 0)
            height = int(rect.get("height") or 0)
            if width <= 0 or height <= 0:
                raise DesktopExecutorError("The editable UI Automation element has no clickable bounds.")
            click_params = {
                key: item
                for key, item in params.items()
                if key not in {"elementIndex", "automationId", "name", "controlType"}
            }
            self._click(
                {
                    **click_params,
                    "x": int(rect.get("left") or 0) + width // 2 - int(window_rect.get("left") or 0),
                    "y": int(rect.get("top") or 0) + height // 2 - int(window_rect.get("top") or 0),
                    "button": "left",
                    "clicks": 1,
                },
                {},
                cancel_check,
            )
            self._key_press({**params, "key": "Control_L+a"}, {}, cancel_check)
            if value:
                self._type_text({**params, "text": value}, {}, cancel_check)
            else:
                self._key_press({**params, "key": "BackSpace"}, {}, cancel_check)
            performed = "keyboard_replace"
        return {
            "operation": operation,
            "performed": performed,
            "element": result.get("element"),
            "characterCount": len(str(request.get("value") or "")) if operation == "set_value" else 0,
            "summary": f"UI Automation {performed or operation} completed.",
        }

    def _cursor_position(self, _params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        point = _POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise DesktopExecutorError("GetCursorPos failed.")
        return {"operation": "cursor_position", "x": int(point.x), "y": int(point.y), "summary": "Read the current pointer position."}

    def _point_from_params(self, params: dict[str, Any]) -> tuple[int, int]:
        has_window = (
            params.get("windowHandle") not in (None, "")
            or params.get("hwnd") not in (None, "")
            or bool(str(params.get("titleContains") or params.get("windowTitle") or "").strip())
        )
        has_ratio = "xRatio" in params or "yRatio" in params
        if has_ratio and not has_window:
            raise DesktopExecutorError("xRatio and yRatio require windowHandle or titleContains.")
        if has_ratio and params.get("relativeToWindow", True) is False:
            raise DesktopExecutorError("Pointer ratios are always relative to the target window.")
        if has_ratio:
            if "xRatio" not in params or "yRatio" not in params:
                raise DesktopExecutorError("Pointer ratio operations require both xRatio and yRatio.")
            x_ratio = float(params["xRatio"])
            y_ratio = float(params["yRatio"])
            if not 0 <= x_ratio <= 1 or not 0 <= y_ratio <= 1:
                raise DesktopExecutorError("Pointer ratios must be between 0 and 1.")
            x = 0
            y = 0
        else:
            if "x" not in params or "y" not in params:
                raise DesktopExecutorError("Pointer operations require x/y or xRatio/yRatio coordinates.")
            x = int(params["x"])
            y = int(params["y"])
        if has_window:
            window = self._resolve_window(params)
            if params.get("relativeToWindow", True):
                rect = window["rect"]
                if has_ratio:
                    x = round(int(rect["width"]) * x_ratio)
                    y = round(int(rect["height"]) * y_ratio)
                x += int(rect["left"])
                y += int(rect["top"])
        screen = self._virtual_screen_rect()
        if x < screen["left"] or x >= screen["right"] or y < screen["top"] or y >= screen["bottom"]:
            raise DesktopExecutorError(f"Pointer coordinates ({x}, {y}) are outside the virtual desktop.")
        return x, y

    def _move_pointer(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        x, y = self._point_from_params(params)
        if not self.user32.SetCursorPos(x, y):
            raise DesktopExecutorError("SetCursorPos failed.")
        return {"operation": "move_pointer", "x": x, "y": y, "summary": f"Moved the pointer to ({x}, {y})."}

    def _send_mouse(self, flags: int, mouse_data: int = 0) -> None:
        item = _INPUT(type=self.INPUT_MOUSE, mi=_MOUSEINPUT(0, 0, mouse_data, flags, 0, 0))
        sent = int(self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT)))
        if sent != 1:
            raise DesktopExecutorError(f"SendInput mouse event failed with Windows error {ctypes.get_last_error()}.")

    def _element_click_point(self, params: dict[str, Any], cancel_check: CancelCheck) -> tuple[int, int, dict[str, Any]]:
        window = self._resolve_input_window(params)
        request: dict[str, Any] = {
            "operation": "resolve",
            "windowHandle": int(window["windowHandle"]),
            "limit": max(1, min(int(params.get("limit") or 500), 500)),
            "textLimit": 256,
        }
        for key in ("elementIndex", "automationId", "name", "controlType"):
            if params.get(key) not in (None, ""):
                request[key] = params[key]
        request.update(self._observed_element_expectations(window, params))
        try:
            result = self.uia.execute(request, cancel_check)
        except DesktopUiaCancelled as exc:
            raise DesktopActionCancelled(str(exc)) from exc
        except DesktopUiaError as exc:
            raise DesktopExecutorError(str(exc)) from exc
        element = result.get("element") if isinstance(result.get("element"), dict) else {}
        if element.get("enabled") is False:
            raise DesktopExecutorError("The target UI Automation element is disabled.")
        if element.get("offscreen") is True:
            raise DesktopExecutorError("The target UI Automation element is offscreen; scroll it into view first.")
        rect = element.get("rect") if isinstance(element.get("rect"), dict) else {}
        width = int(rect.get("width") or 0)
        height = int(rect.get("height") or 0)
        if width <= 0 or height <= 0:
            raise DesktopExecutorError("The target UI Automation element has no clickable bounds.")
        x = int(rect.get("left") or 0) + width // 2
        y = int(rect.get("top") or 0) + height // 2
        screen = self._virtual_screen_rect()
        if x < screen["left"] or x >= screen["right"] or y < screen["top"] or y >= screen["bottom"]:
            raise DesktopExecutorError("The target UI Automation element is outside the virtual desktop.")
        return x, y, element

    def _click(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._focus_window(params, {}, cancel_check)
        self._validate_screenshot_binding(params)
        element_target = any(params.get(key) not in (None, "") for key in ("elementIndex", "automationId", "name"))
        if element_target:
            x, y, element = self._element_click_point(params, cancel_check)
        else:
            x, y = self._point_from_params(params)
            element = None
        button = str(params.get("button") or "left").strip().lower()
        flags = {
            "left": (self.MOUSEEVENTF_LEFTDOWN, self.MOUSEEVENTF_LEFTUP),
            "right": (self.MOUSEEVENTF_RIGHTDOWN, self.MOUSEEVENTF_RIGHTUP),
            "middle": (self.MOUSEEVENTF_MIDDLEDOWN, self.MOUSEEVENTF_MIDDLEUP),
        }.get(button)
        if flags is None:
            raise DesktopExecutorError("click button must be left, right, or middle.")
        clicks = max(1, min(int(params.get("clicks") or 1), 3))
        interval = max(0, min(int(params.get("intervalMs") or 80), 1000)) / 1000
        self._raise_if_cancelled(cancel_check)
        if not self.user32.SetCursorPos(x, y):
            raise DesktopExecutorError("SetCursorPos failed before click.")
        for index in range(clicks):
            self._raise_if_cancelled(cancel_check)
            self._send_mouse(flags[0])
            self._send_mouse(flags[1])
            if index + 1 < clicks and interval:
                time.sleep(interval)
        return {
            "operation": "click",
            "x": x,
            "y": y,
            "button": button,
            "clicks": clicks,
            "element": element,
            "summary": f"Clicked {button} at ({x}, {y}).",
        }

    def _drag_points(self, params: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
        common = {
            key: params[key]
            for key in ("windowHandle", "hwnd", "titleContains", "windowTitle", "processId", "pid", "app", "processPath", "relativeToWindow")
            if key in params
        }

        def point(prefix: str) -> tuple[int, int]:
            candidate = dict(common)
            ratio_x = params.get(f"{prefix}XRatio")
            ratio_y = params.get(f"{prefix}YRatio")
            if ratio_x is not None or ratio_y is not None:
                candidate["xRatio"] = ratio_x
                candidate["yRatio"] = ratio_y
            else:
                candidate["x"] = params.get(f"{prefix}X")
                candidate["y"] = params.get(f"{prefix}Y")
            return self._point_from_params(candidate)

        return point("from"), point("to")

    def _drag(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._focus_window(params, {}, cancel_check)
        self._validate_screenshot_binding(params)
        (from_x, from_y), (to_x, to_y) = self._drag_points(params)
        duration_ms = max(50, min(int(params.get("durationMs") or 500), 5000))
        steps = max(2, min(int(params.get("steps") or max(4, duration_ms // 25)), 120))
        self._raise_if_cancelled(cancel_check)
        if not self.user32.SetCursorPos(from_x, from_y):
            raise DesktopExecutorError("SetCursorPos failed before drag.")
        self._send_mouse(self.MOUSEEVENTF_LEFTDOWN)
        try:
            for index in range(1, steps + 1):
                self._raise_if_cancelled(cancel_check)
                ratio = index / steps
                x = round(from_x + (to_x - from_x) * ratio)
                y = round(from_y + (to_y - from_y) * ratio)
                if not self.user32.SetCursorPos(x, y):
                    raise DesktopExecutorError("SetCursorPos failed during drag.")
                time.sleep(duration_ms / steps / 1000)
        finally:
            self._send_mouse(self.MOUSEEVENTF_LEFTUP)
        return {
            "operation": "drag",
            "fromX": from_x,
            "fromY": from_y,
            "toX": to_x,
            "toY": to_y,
            "durationMs": duration_ms,
            "summary": f"Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y}).",
        }

    def _scroll(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        self._focus_window(params, {}, cancel_check)
        self._validate_screenshot_binding(params)
        if "x" in params and "y" in params:
            x, y = self._point_from_params(params)
            if not self.user32.SetCursorPos(x, y):
                raise DesktopExecutorError("SetCursorPos failed before scroll.")
        has_axis_deltas = any(key in params for key in ("scrollX", "scroll_x", "scrollY", "scroll_y"))
        if has_axis_deltas:
            scroll_x = max(-12000, min(int(params.get("scrollX") or params.get("scroll_x") or 0), 12000))
            scroll_y = max(-12000, min(int(params.get("scrollY") or params.get("scroll_y") or 0), 12000))
            if scroll_y:
                self._send_mouse(self.MOUSEEVENTF_WHEEL, (-scroll_y) & 0xFFFFFFFF)
            if scroll_x:
                self._send_mouse(self.MOUSEEVENTF_HWHEEL, scroll_x & 0xFFFFFFFF)
            return {
                "operation": "scroll",
                "scrollX": scroll_x,
                "scrollY": scroll_y,
                "summary": f"Scrolled by ({scroll_x}, {scroll_y}) wheel units.",
            }
        delta = max(-12000, min(int(params.get("delta") or (int(params.get("notches") or -3) * self.WHEEL_DELTA)), 12000))
        self._send_mouse(self.MOUSEEVENTF_WHEEL, delta & 0xFFFFFFFF)
        return {"operation": "scroll", "delta": delta, "scrollX": 0, "scrollY": -delta, "summary": f"Scrolled by {delta} wheel units."}

    def _focus_window(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        window = self._resolve_input_window(params)
        hwnd = int(window["windowHandle"])
        if window.get("minimized"):
            self.user32.ShowWindow(hwnd, self.SW_RESTORE)
        current_thread = int(self.kernel32.GetCurrentThreadId())
        target_thread = int(self.user32.GetWindowThreadProcessId(hwnd, None))
        foreground = int(self.user32.GetForegroundWindow() or 0)
        foreground_thread = int(self.user32.GetWindowThreadProcessId(foreground, None)) if foreground else 0
        attached_target = bool(target_thread and target_thread != current_thread and self.user32.AttachThreadInput(current_thread, target_thread, True))
        attached_foreground = bool(
            foreground_thread
            and foreground_thread not in {current_thread, target_thread}
            and self.user32.AttachThreadInput(current_thread, foreground_thread, True)
        )
        try:
            self.user32.BringWindowToTop(hwnd)
            focused = bool(self.user32.SetForegroundWindow(hwnd))
        finally:
            if attached_foreground:
                self.user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                self.user32.AttachThreadInput(current_thread, target_thread, False)
        if not focused and int(self.user32.GetForegroundWindow() or 0) != hwnd:
            raise DesktopExecutorError("Windows denied foreground focus for the target window.")
        updated = self._window_info(hwnd)
        return {"operation": "focus_window", "window": updated, "summary": f"Focused window: {updated.get('title') or hwnd}"}

    def _send_key(self, virtual_key: int, *, key_up: bool = False, scan_code: int = 0, unicode_input: bool = False) -> None:
        flags = self.KEYEVENTF_KEYUP if key_up else 0
        if unicode_input:
            flags |= self.KEYEVENTF_UNICODE
        item = _INPUT(
            type=self.INPUT_KEYBOARD,
            ki=_KEYBDINPUT(0 if unicode_input else virtual_key, scan_code, flags, 0, 0),
        )
        sent = int(self.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(_INPUT)))
        if sent != 1:
            raise DesktopExecutorError(f"SendInput keyboard event failed with Windows error {ctypes.get_last_error()}.")

    def _virtual_key(self, name: str) -> int:
        normalized = str(name or "").strip().lower().replace("_", "")
        if normalized in self.KEY_CODES:
            return self.KEY_CODES[normalized]
        numpad_match = re.fullmatch(r"(?:kp|numpad)([0-9])", normalized)
        if numpad_match:
            return 0x60 + int(numpad_match.group(1))
        if re.fullmatch(r"f(?:[1-9]|1\d|2[0-4])", normalized):
            return 0x70 + int(normalized[1:]) - 1
        if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
            return ord(normalized.upper())
        raise DesktopExecutorError(f"Unsupported key name: {name}")

    @staticmethod
    def _key_names_from_params(params: dict[str, Any]) -> list[str]:
        raw_keys = params.get("keys")
        if isinstance(raw_keys, str):
            return [item.strip() for item in raw_keys.split("+") if item.strip()]
        if isinstance(raw_keys, list):
            keys: list[str] = []
            for raw_key in raw_keys:
                keys.extend(item.strip() for item in str(raw_key).split("+") if item.strip())
            return keys
        return [item.strip() for item in str(params.get("key") or "").split("+") if item.strip()]

    def _key_press(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        keys = self._key_names_from_params(params)
        if not keys or len(keys) > 8:
            raise DesktopExecutorError("key_press requires one to eight keys.")
        blocked_keys = {"win", "windows", "meta", "cmd", "command", "super", "os"}
        if any(str(key).strip().lower().replace("_l", "").replace("_r", "") in blocked_keys for key in keys):
            raise DesktopExecutorError("Computer Use does not allow Windows, Meta, Command, Super, or OS key shortcuts.")
        self._focus_window(params, {}, cancel_check)
        virtual_keys = [self._virtual_key(item) for item in keys]
        repeat = max(1, min(int(params.get("repeat") or 1), 20))
        interval = max(0, min(int(params.get("intervalMs") or 50), 1000)) / 1000
        for index in range(repeat):
            self._raise_if_cancelled(cancel_check)
            for virtual_key in virtual_keys:
                self._send_key(virtual_key)
            for virtual_key in reversed(virtual_keys):
                self._send_key(virtual_key, key_up=True)
            if index + 1 < repeat and interval:
                time.sleep(interval)
        return {"operation": "key_press", "keys": keys, "repeat": repeat, "summary": f"Pressed {'+'.join(keys)}."}

    def _type_text(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        text = str(params.get("text") or "")
        if not text:
            raise DesktopExecutorError("type_text requires non-empty text.")
        if len(text) > 8000:
            raise DesktopExecutorError("type_text supports at most 8000 characters.")
        self._focus_window(params, {}, cancel_check)
        delay = max(0, min(int(params.get("delayMs") or 0), 250)) / 1000
        utf16 = text.encode("utf-16-le")
        units = [int.from_bytes(utf16[index : index + 2], "little") for index in range(0, len(utf16), 2)]
        for unit in units:
            self._raise_if_cancelled(cancel_check)
            self._send_key(0, scan_code=unit, unicode_input=True)
            self._send_key(0, key_up=True, scan_code=unit, unicode_input=True)
            if delay:
                time.sleep(delay)
        return {"operation": "type_text", "characterCount": len(text), "summary": f"Typed {len(text)} characters."}

    def _wait(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        duration_ms = max(0, min(int(params.get("durationMs") or params.get("ms") or 500), 30_000))
        deadline = time.monotonic() + duration_ms / 1000
        while time.monotonic() < deadline:
            self._raise_if_cancelled(cancel_check)
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        return {"operation": "wait", "durationMs": duration_ms, "summary": f"Waited {duration_ms} ms."}

    def _capture_bitmap(self, left: int, top: int, width: int, height: int, hwnd: int = 0) -> bytes:
        if width <= 0 or height <= 0 or width > 16384 or height > 16384 or width * height > 80_000_000:
            raise DesktopExecutorError("Screenshot dimensions are invalid or exceed the capture limit.")
        source_dc = self.user32.GetDC(0)
        if not source_dc:
            raise DesktopExecutorError("GetDC failed for screenshot capture.")
        memory_dc = self.gdi32.CreateCompatibleDC(source_dc)
        bitmap = self.gdi32.CreateCompatibleBitmap(source_dc, width, height)
        previous = self.gdi32.SelectObject(memory_dc, bitmap) if memory_dc and bitmap else 0
        try:
            if not memory_dc or not bitmap or not previous:
                raise DesktopExecutorError("Unable to allocate a Windows screenshot bitmap.")
            if hwnd:
                captured = bool(self.user32.PrintWindow(hwnd, memory_dc, self.PW_RENDERFULLCONTENT))
            else:
                captured = bool(
                    self.gdi32.BitBlt(
                        memory_dc,
                        0,
                        0,
                        width,
                        height,
                        source_dc,
                        left,
                        top,
                        self.SRCCOPY | self.CAPTUREBLT,
                    )
                )
            if not captured:
                method = "PrintWindow" if hwnd else "BitBlt"
                raise DesktopExecutorError(f"Windows {method} screenshot capture failed.")
            image_size = width * height * 4
            info = _BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0
            info.bmiHeader.biSizeImage = image_size
            buffer = ctypes.create_string_buffer(image_size)
            rows = int(
                self.gdi32.GetDIBits(
                    memory_dc,
                    bitmap,
                    0,
                    height,
                    buffer,
                    ctypes.byref(info),
                    self.DIB_RGB_COLORS,
                )
            )
            if rows != height:
                raise DesktopExecutorError("GetDIBits did not return the full screenshot.")
            dib_header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, image_size, 0, 0, 0, 0)
            file_header = struct.pack("<2sIHHI", b"BM", 14 + len(dib_header) + image_size, 0, 0, 14 + len(dib_header))
            return file_header + dib_header + buffer.raw
        finally:
            if previous and memory_dc:
                self.gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if memory_dc:
                self.gdi32.DeleteDC(memory_dc)
            self.user32.ReleaseDC(0, source_dc)

    def _prune_capture_dir(self, *, keep_path: Path, max_files: int = 100, max_bytes: int = 512 * 1024 * 1024) -> None:
        try:
            root = self.capture_dir.resolve()
            keep = keep_path.resolve()
            files = [
                path
                for path in root.iterdir()
                if path.is_file() and path.suffix.casefold() in {".bmp", ".png"}
            ]
            files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
            retained_bytes = 0
            for index, path in enumerate(files):
                size = path.stat().st_size
                should_remove = path != keep and (index >= max_files or retained_bytes + size > max_bytes)
                if should_remove:
                    path.unlink(missing_ok=True)
                else:
                    retained_bytes += size
        except OSError:
            pass

    def _cache_screenshot_state(self, screenshot_id: str, window: dict[str, Any]) -> None:
        snapshots = getattr(self, "_screenshot_snapshots", None)
        if not isinstance(snapshots, dict):
            snapshots = {}
            self._screenshot_snapshots = snapshots
        snapshots[screenshot_id] = {
            "capturedAt": time.monotonic(),
            "windowHandle": int(window.get("windowHandle") or 0),
            "processId": int(window.get("processId") or 0),
            "processPath": str(window.get("processPath") or ""),
            "rect": dict(window.get("rect") or {}),
        }
        if len(snapshots) > 20:
            oldest = min(snapshots, key=lambda key: float(snapshots[key].get("capturedAt") or 0))
            snapshots.pop(oldest, None)

    def _validate_screenshot_binding(self, params: dict[str, Any]) -> None:
        screenshot_id = str(params.get("screenshotId") or "").strip()
        if not screenshot_id:
            return
        snapshot = getattr(self, "_screenshot_snapshots", {}).get(screenshot_id)
        if not isinstance(snapshot, dict) or time.monotonic() - float(snapshot.get("capturedAt") or 0) > 120:
            raise DesktopExecutorError("The screenshot id is missing or stale; refresh window_state before using its coordinates.")
        window = self._resolve_input_window(params)
        if (
            int(snapshot.get("windowHandle") or 0) != int(window.get("windowHandle") or 0)
            or int(snapshot.get("processId") or 0) != int(window.get("processId") or 0)
            or str(snapshot.get("processPath") or "").casefold() != str(window.get("processPath") or "").casefold()
        ):
            raise DesktopExecutorError("The screenshot id belongs to a different window or process.")
        observed_rect = snapshot.get("rect") if isinstance(snapshot.get("rect"), dict) else {}
        current_rect = window.get("rect") if isinstance(window.get("rect"), dict) else {}
        if any(int(observed_rect.get(key) or 0) != int(current_rect.get(key) or 0) for key in ("left", "top", "width", "height")):
            raise DesktopExecutorError("The target window moved or resized after the screenshot; refresh window_state.")

    def _screenshot(self, params: dict[str, Any], action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        hwnd = 0
        window: dict[str, Any] | None = None
        if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
            window = self._resolve_read_window(params)
            hwnd = int(window["windowHandle"])
            rect = window["rect"]
            left, top, width, height = rect["left"], rect["top"], rect["width"], rect["height"]
        elif isinstance(params.get("region"), dict):
            region = params["region"]
            left = int(region.get("left") or region.get("x") or 0)
            top = int(region.get("top") or region.get("y") or 0)
            width = int(region.get("width") or 0)
            height = int(region.get("height") or 0)
        else:
            screen = self._virtual_screen_rect()
            left, top, width, height = screen["left"], screen["top"], screen["width"], screen["height"]
        action_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(action.get("actionId") or "desktop")).strip("_") or "desktop"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        capture_backend = "gdi_virtual_desktop"
        occlusion_safe = False
        if hwnd:
            filename = f"{timestamp}_{action_id}.png"
            path = self.capture_dir / filename
            try:
                metadata = self.capture.capture_window(hwnd, path)
            except DesktopCaptureError as exc:
                if not bool(params.get("allowLegacyCapture")):
                    raise DesktopExecutorError(str(exc)) from exc
                bitmap = self._capture_bitmap(left, top, width, height, hwnd=hwnd)
                filename = f"{timestamp}_{action_id}.bmp"
                path = self.capture_dir / filename
                path.write_bytes(bitmap)
                pixels = bitmap[54:]
                stride = max(4, (len(pixels) // 4096 // 4) * 4)
                sampled_colors = {
                    pixels[index : index + 3]
                    for index in range(0, len(pixels) - 3, stride)
                }
                metadata = {
                    "format": "bmp",
                    "width": width,
                    "height": height,
                    "captureBackend": "print_window_compatibility",
                    "occlusionSafe": False,
                    "sampleColorCount": len(sampled_colors),
                    "frameWarning": "uniform_frame" if len(sampled_colors) <= 1 else "legacy_capture_requested",
                }
            capture_backend = str(metadata.get("captureBackend") or "windows_graphics_capture")
            occlusion_safe = bool(metadata.get("occlusionSafe"))
            width = int(metadata.get("width") or width)
            height = int(metadata.get("height") or height)
            sample_color_count = int(metadata.get("sampleColorCount") or 0)
            frame_warning = str(metadata.get("frameWarning") or "")
            image_format = str(metadata.get("format") or path.suffix.lstrip("."))
        else:
            bitmap = self._capture_bitmap(left, top, width, height)
            filename = f"{timestamp}_{action_id}.bmp"
            path = self.capture_dir / filename
            path.write_bytes(bitmap)
            pixels = bitmap[54:]
            stride = max(4, (len(pixels) // 4096 // 4) * 4)
            sampled_colors = {
                pixels[index : index + 3]
                for index in range(0, len(pixels) - 3, stride)
            }
            sample_color_count = len(sampled_colors)
            frame_warning = "uniform_frame" if sample_color_count <= 1 else ""
            image_format = "bmp"
        try:
            self._raise_if_cancelled(cancel_check)
        except DesktopActionCancelled:
            path.unlink(missing_ok=True)
            raise
        self._prune_capture_dir(keep_path=path)
        screenshot_id = filename
        if window is not None:
            self._cache_screenshot_state(screenshot_id, window)
        return {
            "operation": "screenshot",
            "format": image_format,
            "artifactPath": str(path),
            "artifactRelativePath": f"desktop-captures/{filename}",
            "id": screenshot_id,
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "windowHandle": hwnd or None,
            "sampleColorCount": sample_color_count,
            "frameWarning": frame_warning,
            "captureBackend": capture_backend,
            "occlusionSafe": occlusion_safe,
            "summary": f"Captured a {width}x{height} desktop screenshot.",
        }
