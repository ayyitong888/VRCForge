from __future__ import annotations

import ctypes
import os
import re
import struct
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from desktop_uia import DesktopUiaError, WindowsUiaAdapter

WINDOWS_DESKTOP_OPERATIONS = {
    "list_windows",
    "inspect_window",
    "cursor_position",
    "screenshot",
    "focus_window",
    "move_pointer",
    "click",
    "drag",
    "scroll",
    "type_text",
    "key_press",
    "focus_element",
    "invoke_element",
    "set_value",
    "secondary_action",
    "wait",
    "sequence",
}


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


_WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
_ENUMWINDOWSPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class _PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", wintypes.HDC),
        ("fErase", wintypes.BOOL),
        ("rcPaint", _RECT),
        ("fRestore", wintypes.BOOL),
        ("fIncUpdate", wintypes.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class WindowsDesktopActivityOverlay:
    WS_POPUP = 0x80000000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    LWA_ALPHA = 0x00000002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    WM_PAINT = 0x000F
    WM_ERASEBKGND = 0x0014
    WM_LBUTTONUP = 0x0202
    WM_TIMER = 0x0113
    WM_HOTKEY = 0x0312
    WM_MOUSEACTIVATE = 0x0021
    WM_NCHITTEST = 0x0084
    WM_QUIT = 0x0012
    MA_NOACTIVATE = 3
    HTTRANSPARENT = -1
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_NOREPEAT = 0x4000
    VK_F12 = 0x7B
    HOTKEY_ID = 0x5646
    PULSE_TIMER_ID = 0x5647
    DT_CENTER = 0x0001
    DT_VCENTER = 0x0004
    DT_SINGLELINE = 0x0020
    TRANSPARENT = 1
    DEFAULT_GUI_FONT = 17

    def __init__(self) -> None:
        if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
            raise DesktopExecutorError("The desktop activity overlay is available only on Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._windows: list[int] = []
        self._edge_alphas: dict[int, tuple[int, int]] = {}
        self._banner_hwnd = 0
        self._ready = threading.Event()
        self._error = ""
        self._cancel_callback: Callable[[], None] | None = None
        self._message = "VRCForge Computer Use  |  Ctrl+Shift+F12 to stop"
        self._accent_color = self._rgb(37, 99, 235)
        self._banner_color = self._rgb(255, 255, 255)
        self._banner_text_color = self._rgb(25, 31, 42)
        self._pulse_high = False
        self._class_name = f"VRCForgeDesktopOverlay_{os.getpid()}_{id(self)}"
        self._wnd_proc_callback = _WNDPROC(self._wnd_proc)

    def _configure_signatures(self) -> None:
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        self.user32.RegisterClassW.restype = wintypes.ATOM
        self.user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self.user32.UnregisterClassW.restype = wintypes.BOOL
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
        self.user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        self.user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
        self.user32.SetTimer.restype = ctypes.c_size_t
        self.user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        self.user32.KillTimer.restype = wintypes.BOOL
        self.user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        self.user32.SetWindowRgn.restype = ctypes.c_int
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = wintypes.BOOL
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self.user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        self.user32.BeginPaint.restype = wintypes.HDC
        self.user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(_PAINTSTRUCT)]
        self.user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        self.user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(_RECT), wintypes.HBRUSH]
        self.user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(_RECT), wintypes.UINT]
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self.gdi32.GetStockObject.argtypes = [ctypes.c_int]
        self.gdi32.GetStockObject.restype = wintypes.HGDIOBJ
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.gdi32.CreateRoundRectRgn.restype = wintypes.HRGN

    @staticmethod
    def _rgb(red: int, green: int, blue: int) -> int:
        return red | (green << 8) | (blue << 16)

    def show(self, cancel_callback: Callable[[], None], message: str = "", *, theme: str = "light") -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._cancel_callback = cancel_callback
        self._message = message.strip() or "VRCForge Computer Use  |  Ctrl+Shift+F12 to stop"
        if str(theme).strip().lower() == "dark":
            self._accent_color = self._rgb(96, 145, 255)
            self._banner_color = self._rgb(28, 31, 38)
            self._banner_text_color = self._rgb(245, 247, 250)
        else:
            self._accent_color = self._rgb(37, 99, 235)
            self._banner_color = self._rgb(255, 255, 255)
            self._banner_text_color = self._rgb(25, 31, 42)
        self._error = ""
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="vrcforge-desktop-overlay", daemon=True)
        self._thread.start()
        if not self._ready.wait(3.0):
            raise DesktopExecutorError("Desktop activity overlay did not start in time.")
        if self._error:
            raise DesktopExecutorError(self._error)

    def hide(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if self._thread_id:
            self.user32.PostThreadMessageW(self._thread_id, self.WM_QUIT, 0, 0)
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None
        self._thread_id = 0
        self._windows = []
        self._edge_alphas = {}
        self._banner_hwnd = 0
        self._cancel_callback = None

    def _wnd_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == self.WM_PAINT:
            paint = _PAINTSTRUCT()
            hdc = self.user32.BeginPaint(hwnd, ctypes.byref(paint))
            rect = _RECT()
            self.user32.GetClientRect(hwnd, ctypes.byref(rect))
            banner = int(hwnd) == int(self._banner_hwnd)
            color = self._banner_color if banner else self._accent_color
            brush = self.gdi32.CreateSolidBrush(color)
            try:
                self.user32.FillRect(hdc, ctypes.byref(rect), brush)
                if banner:
                    self.gdi32.SetBkMode(hdc, self.TRANSPARENT)
                    self.gdi32.SetTextColor(hdc, self._banner_text_color)
                    font = self.gdi32.GetStockObject(self.DEFAULT_GUI_FONT)
                    previous = self.gdi32.SelectObject(hdc, font)
                    try:
                        self.user32.DrawTextW(
                            hdc,
                            self._message,
                            -1,
                            ctypes.byref(rect),
                            self.DT_CENTER | self.DT_VCENTER | self.DT_SINGLELINE,
                        )
                    finally:
                        if previous:
                            self.gdi32.SelectObject(hdc, previous)
            finally:
                if brush:
                    self.gdi32.DeleteObject(brush)
                self.user32.EndPaint(hwnd, ctypes.byref(paint))
            return 0
        if message == self.WM_ERASEBKGND:
            return 1
        if message == self.WM_NCHITTEST and int(hwnd) != int(self._banner_hwnd):
            return self.HTTRANSPARENT
        if message == self.WM_MOUSEACTIVATE:
            return self.MA_NOACTIVATE
        if message == self.WM_LBUTTONUP and int(hwnd) == int(self._banner_hwnd):
            callback = self._cancel_callback
            if callback is not None:
                callback()
            return 0
        if message == self.WM_TIMER and int(wparam) == self.PULSE_TIMER_ID:
            self._pulse_high = not self._pulse_high
            for edge_hwnd, (low_alpha, high_alpha) in self._edge_alphas.items():
                alpha = high_alpha if self._pulse_high else low_alpha
                self.user32.SetLayeredWindowAttributes(edge_hwnd, 0, alpha, self.LWA_ALPHA)
            return 0
        if message == self.WM_HOTKEY and int(wparam) == self.HOTKEY_ID:
            callback = self._cancel_callback
            if callback is not None:
                callback()
            return 0
        return int(self.user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _run(self) -> None:
        instance = self.kernel32.GetModuleHandleW(None)
        self._thread_id = int(self.kernel32.GetCurrentThreadId())
        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = self._wnd_proc_callback
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        registered = False
        try:
            if not self.user32.RegisterClassW(ctypes.byref(window_class)):
                raise DesktopExecutorError(f"RegisterClassW failed with Windows error {ctypes.get_last_error()}.")
            registered = True
            left = int(self.user32.GetSystemMetrics(76))
            top = int(self.user32.GetSystemMetrics(77))
            width = int(self.user32.GetSystemMetrics(78))
            height = int(self.user32.GetSystemMetrics(79))
            banner_width = min(760, max(420, int(self.user32.GetSystemMetrics(0)) - 40))
            banner_height = 58
            primary_width = int(self.user32.GetSystemMetrics(0))
            banner_left = (primary_width - banner_width) // 2
            banner_top = 12
            layouts: list[tuple[int, int, int, int, bool, int, int]] = []
            for horizontal_inflate, vertical_inflate, low_alpha, high_alpha in (
                (80, 16, 2, 7),
                (52, 10, 3, 10),
                (28, 6, 5, 14),
            ):
                layouts.append(
                    (
                        banner_left - horizontal_inflate // 2,
                        banner_top - vertical_inflate // 2,
                        banner_width + horizontal_inflate,
                        banner_height + vertical_inflate,
                        False,
                        low_alpha,
                        high_alpha,
                    )
                )
            layouts.append((banner_left, banner_top, banner_width, banner_height, True, 246, 246))
            base_style = self.WS_EX_TOPMOST | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE | self.WS_EX_LAYERED
            for x, y, item_width, item_height, banner, low_alpha, high_alpha in layouts:
                ex_style = base_style if banner else base_style | self.WS_EX_TRANSPARENT
                hwnd = self.user32.CreateWindowExW(
                    ex_style,
                    self._class_name,
                    "VRCForge Desktop Activity",
                    self.WS_POPUP,
                    x,
                    y,
                    item_width,
                    item_height,
                    0,
                    0,
                    instance,
                    None,
                )
                if not hwnd:
                    raise DesktopExecutorError(f"CreateWindowExW failed with Windows error {ctypes.get_last_error()}.")
                self._windows.append(int(hwnd))
                if banner:
                    self._banner_hwnd = int(hwnd)
                else:
                    self._edge_alphas[int(hwnd)] = (low_alpha, high_alpha)
                region = self.gdi32.CreateRoundRectRgn(0, 0, item_width + 1, item_height + 1, item_height, item_height)
                if region:
                    self.user32.SetWindowRgn(hwnd, region, True)
                self.user32.SetLayeredWindowAttributes(hwnd, 0, high_alpha, self.LWA_ALPHA)
                self.user32.SetWindowPos(hwnd, -1, x, y, item_width, item_height, self.SWP_NOACTIVATE | self.SWP_SHOWWINDOW)
            if not self.user32.RegisterHotKey(
                0,
                self.HOTKEY_ID,
                self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT,
                self.VK_F12,
            ):
                raise DesktopExecutorError("Ctrl+Shift+F12 could not be registered as the Computer Use stop shortcut.")
            self.user32.SetTimer(0, self.PULSE_TIMER_ID, 650, None)
            self._ready.set()
            message = wintypes.MSG()
            while self.user32.GetMessageW(ctypes.byref(message), 0, 0, 0) > 0:
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # noqa: BLE001 - startup error is handed back to the worker.
            self._error = str(exc)
            self._ready.set()
        finally:
            self.user32.KillTimer(0, self.PULSE_TIMER_ID)
            self.user32.UnregisterHotKey(0, self.HOTKEY_ID)
            for hwnd in reversed(self._windows):
                self.user32.DestroyWindow(hwnd)
            self._windows = []
            self._edge_alphas = {}
            self._banner_hwnd = 0
            if registered:
                self.user32.UnregisterClassW(self._class_name, instance)


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

    KEY_CODES = {
        "backspace": 0x08,
        "tab": 0x09,
        "enter": 0x0D,
        "return": 0x0D,
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
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
        "win": 0x5B,
        "windows": 0x5B,
    }

    def __init__(self, capture_dir: Path) -> None:
        if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
            raise DesktopExecutorError("The native desktop executor is available only on Windows.")
        self.capture_dir = capture_dir
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.uia = WindowsUiaAdapter()
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

    def execute(self, action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        params = action.get("params") if isinstance(action.get("params"), dict) else {}
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
        operation = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().lower()).strip("_")
        aliases = {
            "list_apps": "list_windows",
            "capture": "screenshot",
            "move": "move_pointer",
            "drag_pointer": "drag",
            "type": "type_text",
            "press": "key_press",
            "invoke": "invoke_element",
            "set_element_value": "set_value",
            "sleep": "wait",
        }
        return aliases.get(operation, operation)

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
            for index, raw_step in enumerate(raw_steps):
                if not isinstance(raw_step, dict):
                    raise DesktopExecutorError(f"sequence step {index + 1} must be an object.")
                step_operation = self._normalize_operation(raw_step.get("operation"))
                if step_operation not in WINDOWS_DESKTOP_OPERATIONS or step_operation == "sequence":
                    raise DesktopExecutorError(f"Unsupported sequence operation at step {index + 1}: {step_operation or 'missing'}")
                step_params = dict(raw_step)
                step_params["operation"] = step_operation
                self._validate_operation_params(step_operation, step_params)
                normalized_steps.append((step_operation, step_params))
            results: list[dict[str, Any]] = []
            for index, (step_operation, step_params) in enumerate(normalized_steps):
                result = self._execute_operation(step_operation, step_params, action, cancel_check)
                results.append({"index": index + 1, "operation": step_operation, "result": result})
            return {"operation": operation, "stepCount": len(results), "steps": results, "summary": "Desktop sequence completed."}
        self._validate_operation_params(operation, params)
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any], CancelCheck], dict[str, Any]]] = {
            "list_windows": self._list_windows,
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
        if operation in {"inspect_window", "focus_window"}:
            self._resolve_window(params)
        elif operation == "screenshot":
            if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
                self._resolve_window(params)
            elif isinstance(params.get("region"), dict):
                region = params["region"]
                if int(region.get("width") or 0) <= 0 or int(region.get("height") or 0) <= 0:
                    raise DesktopExecutorError("Screenshot region width and height must be positive.")
        elif operation in {"move_pointer", "click"}:
            self._point_from_params(params)
            if operation == "click":
                if str(params.get("button") or "left").strip().lower() not in {"left", "right", "middle"}:
                    raise DesktopExecutorError("click button must be left, right, or middle.")
                int(params.get("clicks") or 1)
                int(params.get("intervalMs") or 80)
        elif operation == "drag":
            self._drag_points(params)
            int(params.get("durationMs") or 500)
            int(params.get("steps") or 20)
        elif operation == "scroll":
            if "x" in params or "y" in params:
                if "x" not in params or "y" not in params:
                    raise DesktopExecutorError("scroll coordinates require both x and y.")
                self._point_from_params(params)
            int(params.get("delta") or (int(params.get("notches") or -3) * self.WHEEL_DELTA))
        elif operation == "type_text":
            text = str(params.get("text") or "")
            if not text or len(text) > 8000:
                raise DesktopExecutorError("type_text requires between 1 and 8000 characters.")
            if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
                self._resolve_window(params)
            int(params.get("delayMs") or 0)
        elif operation == "key_press":
            raw_keys = params.get("keys")
            if isinstance(raw_keys, str):
                keys = [item.strip() for item in raw_keys.split("+") if item.strip()]
            elif isinstance(raw_keys, list):
                keys = [str(item).strip() for item in raw_keys if str(item).strip()]
            else:
                key = str(params.get("key") or "").strip()
                keys = [key] if key else []
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
            if operation == "secondary_action" and str(params.get("action") or "").strip().lower() not in {"invoke", "select", "expand", "collapse"}:
                raise DesktopExecutorError("secondary_action must be invoke, select, expand, or collapse.")

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

    def _window_info(self, hwnd: int) -> dict[str, Any]:
        rect = _RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise DesktopExecutorError(f"Unable to read window bounds for handle {hwnd}.")
        class_buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        process_id = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return {
            "windowHandle": int(hwnd),
            "title": self._window_title(hwnd),
            "className": class_buffer.value,
            "processId": int(process_id.value),
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
            return self._window_info(hwnd)
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
            return exact_matches[0]
        if len(matches) > 1:
            candidates = ", ".join(
                f"{item.get('title') or '<untitled>'} (pid {item.get('processId')}, hwnd {item.get('windowHandle')})"
                for item in matches[:5]
            )
            raise DesktopExecutorError(
                "Window title is ambiguous; use an exact title, processId, or windowHandle. Matches: " + candidates
            )
        return matches[0]

    def _list_windows(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        title_contains = str(params.get("titleContains") or "").strip().casefold()
        limit = max(1, min(int(params.get("limit") or 50), 100))
        windows = self._enumerate_windows(include_untitled=bool(params.get("includeUntitled")))
        if title_contains:
            windows = [item for item in windows if title_contains in str(item.get("title") or "").casefold()]
        windows = windows[:limit]
        return {"operation": "list_windows", "count": len(windows), "windows": windows, "summary": f"Found {len(windows)} visible windows."}

    def _inspect_window(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        window = self._resolve_window(params)
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
            accessibility = self.uia.inspect(int(window["windowHandle"]), limit=limit)
            elements = accessibility.get("elements") if isinstance(accessibility.get("elements"), list) else []
            result.update(
                {
                    "count": len(elements),
                    "controls": elements,
                    "accessibilityTree": True,
                    "truncated": bool(accessibility.get("truncated")),
                    "summary": f"Inspected {len(elements)} UI Automation elements.",
                }
            )
        except DesktopUiaError as exc:
            result["controls"] = controls
            result["accessibilityError"] = str(exc)[-500:]
        return result

    def _uia_action(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        window = self._resolve_window(params)
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
        if operation == "set_value":
            request["value"] = str(params.get("value") or params.get("text") or "")
        try:
            result = self.uia.execute(request)
        except DesktopUiaError as exc:
            raise DesktopExecutorError(str(exc)) from exc
        self._raise_if_cancelled(cancel_check)
        return {
            "operation": operation,
            "performed": result.get("performed"),
            "element": result.get("element"),
            "characterCount": len(str(request.get("value") or "")) if operation == "set_value" else 0,
            "summary": f"UI Automation {result.get('performed') or operation} completed.",
        }

    def _cursor_position(self, _params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        point = _POINT()
        if not self.user32.GetCursorPos(ctypes.byref(point)):
            raise DesktopExecutorError("GetCursorPos failed.")
        return {"operation": "cursor_position", "x": int(point.x), "y": int(point.y), "summary": "Read the current pointer position."}

    def _point_from_params(self, params: dict[str, Any]) -> tuple[int, int]:
        has_window = params.get("windowHandle") not in (None, "") or bool(str(params.get("titleContains") or "").strip())
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

    def _click(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        x, y = self._point_from_params(params)
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
        return {"operation": "click", "x": x, "y": y, "button": button, "clicks": clicks, "summary": f"Clicked {button} at ({x}, {y})."}

    def _drag_points(self, params: dict[str, Any]) -> tuple[tuple[int, int], tuple[int, int]]:
        common = {
            key: params[key]
            for key in ("windowHandle", "hwnd", "titleContains", "windowTitle", "processId", "pid", "relativeToWindow")
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
        if "x" in params and "y" in params:
            x, y = self._point_from_params(params)
            if not self.user32.SetCursorPos(x, y):
                raise DesktopExecutorError("SetCursorPos failed before scroll.")
        delta = int(params.get("delta") or (int(params.get("notches") or -3) * self.WHEEL_DELTA))
        delta = max(-12000, min(delta, 12000))
        self._send_mouse(self.MOUSEEVENTF_WHEEL, delta & 0xFFFFFFFF)
        return {"operation": "scroll", "delta": delta, "summary": f"Scrolled by {delta} wheel units."}

    def _focus_window(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        window = self._resolve_window(params)
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
        if re.fullmatch(r"f(?:[1-9]|1\d|2[0-4])", normalized):
            return 0x70 + int(normalized[1:]) - 1
        if len(normalized) == 1 and normalized.isascii() and normalized.isalnum():
            return ord(normalized.upper())
        raise DesktopExecutorError(f"Unsupported key name: {name}")

    def _key_press(self, params: dict[str, Any], _action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        raw_keys = params.get("keys")
        if isinstance(raw_keys, str):
            keys = [item.strip() for item in raw_keys.split("+") if item.strip()]
        elif isinstance(raw_keys, list):
            keys = [str(item).strip() for item in raw_keys if str(item).strip()]
        else:
            key = str(params.get("key") or "").strip()
            keys = [key] if key else []
        if not keys or len(keys) > 8:
            raise DesktopExecutorError("key_press requires one to eight keys.")
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
        if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
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
            captured = False
            if hwnd:
                captured = bool(self.user32.PrintWindow(hwnd, memory_dc, self.PW_RENDERFULLCONTENT))
            if not captured:
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
                raise DesktopExecutorError("Windows screenshot capture failed.")
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

    def _screenshot(self, params: dict[str, Any], action: dict[str, Any], cancel_check: CancelCheck) -> dict[str, Any]:
        self._raise_if_cancelled(cancel_check)
        hwnd = 0
        if params.get("windowHandle") not in (None, "") or str(params.get("titleContains") or "").strip():
            window = self._resolve_window(params)
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
        bitmap = self._capture_bitmap(left, top, width, height, hwnd=hwnd)
        self._raise_if_cancelled(cancel_check)
        action_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(action.get("actionId") or "desktop")).strip("_") or "desktop"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{timestamp}_{action_id}.bmp"
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        path = self.capture_dir / filename
        path.write_bytes(bitmap)
        pixels = bitmap[54:]
        stride = max(4, (len(pixels) // 4096 // 4) * 4)
        sampled_colors = {
            pixels[index : index + 3]
            for index in range(0, len(pixels) - 3, stride)
        }
        return {
            "operation": "screenshot",
            "format": "bmp",
            "artifactPath": str(path),
            "artifactRelativePath": f"desktop-captures/{filename}",
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "windowHandle": hwnd or None,
            "sampleColorCount": len(sampled_colors),
            "frameWarning": "uniform_frame" if len(sampled_colors) <= 1 else "",
            "summary": f"Captured a {width}x{height} desktop screenshot.",
        }
