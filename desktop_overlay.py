from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Callable

from desktop_overlay_visuals import (
    DesktopOverlayGeometry,
    build_directional_glow_pixels,
    resolve_desktop_overlay_copy,
    resolve_desktop_overlay_geometry,
    resolve_desktop_overlay_palette,
)


class DesktopOverlayError(RuntimeError):
    pass


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


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


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


_WNDPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
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


@dataclass
class _LayeredSurface:
    hwnd: int
    left: int
    top: int
    width: int
    height: int
    memory_dc: int
    bitmap: int
    previous_bitmap: int
    kind: str


class WindowsDesktopActivityOverlay:
    WS_POPUP = 0x80000000
    WS_EX_TOPMOST = 0x00000008
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000
    LWA_ALPHA = 0x00000002
    ULW_ALPHA = 0x00000002
    AC_SRC_OVER = 0x00
    AC_SRC_ALPHA = 0x01
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    SWP_NOACTIVATE = 0x0010
    SWP_FRAMECHANGED = 0x0020
    SWP_SHOWWINDOW = 0x0040
    GWL_EXSTYLE = -20
    WM_PAINT = 0x000F
    WM_ERASEBKGND = 0x0014
    WM_LBUTTONUP = 0x0202
    WM_TIMER = 0x0113
    WM_HOTKEY = 0x0312
    WM_MOUSEACTIVATE = 0x0021
    WM_NCHITTEST = 0x0084
    WM_QUIT = 0x0012
    MA_NOACTIVATE = 3
    HTCLIENT = 1
    HTTRANSPARENT = -1
    MOD_CONTROL = 0x0002
    MOD_SHIFT = 0x0004
    MOD_NOREPEAT = 0x4000
    VK_F12 = 0x7B
    HOTKEY_ID = 0x5646
    PULSE_TIMER_ID = 0x5647
    PULSE_INTERVAL_MS = 250
    SPI_GETCLIENTAREAANIMATION = 0x1042
    DT_LEFT = 0x0000
    DT_CENTER = 0x0001
    DT_VCENTER = 0x0004
    DT_SINGLELINE = 0x0020
    DT_END_ELLIPSIS = 0x00008000
    TRANSPARENT = 1
    PS_SOLID = 0
    NULL_BRUSH = 5
    DEFAULT_CHARSET = 1
    CLEARTYPE_QUALITY = 5
    FW_NORMAL = 400
    FW_SEMIBOLD = 600
    WDA_MONITOR = 0x00000001
    WDA_EXCLUDEFROMCAPTURE = 0x00000011
    SM_CXSCREEN = 0
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    def __init__(self) -> None:
        if sys.platform != "win32" or not hasattr(ctypes, "WinDLL"):
            raise DesktopOverlayError("The desktop activity overlay is available only on Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_signatures()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._windows: list[int] = []
        self._surfaces: list[_LayeredSurface] = []
        self._banner_hwnd = 0
        self._ready = threading.Event()
        self._error = ""
        self._cancel_callback: Callable[[], None] | None = None
        self._cancel_requested = False
        self._detail_override = ""
        self._palette = resolve_desktop_overlay_palette("light")
        self._copy = resolve_desktop_overlay_copy("en-US")
        self._geometry: DesktopOverlayGeometry | None = None
        self._capture_affinity: dict[int, bool] = {}
        self._capture_affinity_errors: dict[int, tuple[int, int]] = {}
        self._pulse_step = 0
        self._timer_id = 0
        self._motion_enabled = True
        self._hotkey_available = False
        self._class_name = f"VRCForgeDesktopOverlay_{os.getpid()}_{id(self)}"
        self._wnd_proc_callback = _WNDPROC(self._wnd_proc)

    def _configure_signatures(self) -> None:
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self.kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        self.kernel32.GetUserDefaultLocaleName.argtypes = [wintypes.LPWSTR, ctypes.c_int]
        self.kernel32.GetUserDefaultLocaleName.restype = ctypes.c_int
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
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
        self.user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
        self.user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE),
            wintypes.HDC,
            ctypes.POINTER(_POINT),
            wintypes.COLORREF,
            ctypes.POINTER(_BLENDFUNCTION),
            wintypes.DWORD,
        ]
        self.user32.UpdateLayeredWindow.restype = wintypes.BOOL
        self.user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        self.user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
        self.user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
        self.user32.SystemParametersInfoW.restype = wintypes.BOOL
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
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        self.user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(_RECT), wintypes.UINT]
        self.user32.InvalidateRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT), wintypes.BOOL]
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.GetSystemMetrics.restype = ctypes.c_int
        if hasattr(self.user32, "GetDpiForSystem"):
            self.user32.GetDpiForSystem.restype = wintypes.UINT
        if hasattr(self.user32, "SetThreadDpiAwarenessContext"):
            self.user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            self.user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        self.gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(_BITMAPINFO), wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        self.gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
        self.gdi32.CreatePen.restype = wintypes.HPEN
        self.gdi32.CreateFontW.restype = wintypes.HFONT
        self.gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
        self.gdi32.RoundRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.gdi32.Rectangle.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.gdi32.Ellipse.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.gdi32.MoveToEx.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.POINTER(_POINT)]
        self.gdi32.LineTo.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self.gdi32.GetStockObject.argtypes = [ctypes.c_int]
        self.gdi32.GetStockObject.restype = wintypes.HGDIOBJ

    @staticmethod
    def _colorref(color: tuple[int, int, int]) -> int:
        red, green, blue = color
        return red | (green << 8) | (blue << 16)

    @staticmethod
    def _signed_word(value: int) -> int:
        return ctypes.c_short(value & 0xFFFF).value

    def show(self, cancel_callback: Callable[[], None], message: str = "", *, theme: str = "light") -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._cancel_callback = cancel_callback
            self._cancel_requested = False
            self._detail_override = str(message or "").strip()
            self._palette = resolve_desktop_overlay_palette(theme)
            self._error = ""
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, name="vrcforge-desktop-overlay", daemon=True)
            self._thread.start()
        if not self._ready.wait(4.0):
            self.hide()
            raise DesktopOverlayError("Desktop activity overlay did not start in time.")
        if self._error:
            error = self._error
            self.hide()
            raise DesktopOverlayError(error)

    def hide(self) -> None:
        with self._lock:
            thread = self._thread
            thread_id = self._thread_id
        if thread is None:
            return
        if thread_id:
            self.user32.PostThreadMessageW(thread_id, self.WM_QUIT, 0, 0)
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=4.0)
        with self._lock:
            if not thread.is_alive():
                self._thread = None
                self._thread_id = 0
                self._cancel_callback = None

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            geometry = self._geometry
            thread = self._thread
            affinity = list(self._capture_affinity.values())
            return {
                "renderer": "win32-layered-ambient-v2",
                "visible": bool(thread and thread.is_alive() and self._banner_hwnd),
                "theme": self._palette.theme,
                "dpi": geometry.dpi if geometry else 0,
                "windowCount": len(self._windows),
                "glowWindowCount": len(self._surfaces),
                "captureExcluded": bool(affinity) and all(affinity),
                "fontFamily": "Segoe UI",
                "pulseIntervalMs": self.PULSE_INTERVAL_MS if self._motion_enabled else 0,
                "motionEnabled": self._motion_enabled,
                "hotkeyAvailable": self._hotkey_available,
                "singleSurfaceStrategy": "native-for-embedded-bridge",
                "stopping": self._cancel_requested,
                "bannerSize": list(geometry.banner[2:]) if geometry else [],
                "stopHitTargetSize": list(geometry.stop[2:]) if geometry else [],
            }

    def _request_cancel(self) -> None:
        with self._lock:
            if self._cancel_requested:
                return
            self._cancel_requested = True
            callback = self._cancel_callback
            banner = self._banner_hwnd
        if banner:
            self.user32.InvalidateRect(banner, None, True)
        if callback is not None:
            callback()

    def _point_in_stop(self, x: int, y: int) -> bool:
        if self._geometry is None:
            return False
        left, top, width, height = self._geometry.stop
        return left <= x < left + width and top <= y < top + height

    def _wnd_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        try:
            if message == self.WM_PAINT:
                paint = _PAINTSTRUCT()
                hdc = self.user32.BeginPaint(hwnd, ctypes.byref(paint))
                if int(hwnd) == int(self._banner_hwnd):
                    self._paint_banner(hdc)
                self.user32.EndPaint(hwnd, ctypes.byref(paint))
                return 0
            if message == self.WM_ERASEBKGND:
                return 1
            if message == self.WM_NCHITTEST:
                if int(hwnd) != int(self._banner_hwnd):
                    return self.HTTRANSPARENT
                window_rect = _RECT()
                self.user32.GetWindowRect(hwnd, ctypes.byref(window_rect))
                screen_x = self._signed_word(int(lparam))
                screen_y = self._signed_word(int(lparam) >> 16)
                return self.HTCLIENT if self._point_in_stop(screen_x - window_rect.left, screen_y - window_rect.top) else self.HTTRANSPARENT
            if message == self.WM_MOUSEACTIVATE:
                return self.MA_NOACTIVATE
            if message == self.WM_LBUTTONUP and int(hwnd) == int(self._banner_hwnd):
                if self._point_in_stop(self._signed_word(int(lparam)), self._signed_word(int(lparam) >> 16)):
                    self._request_cancel()
                return 0
            if message == self.WM_TIMER and int(wparam) == int(self._timer_id):
                self._pulse_glows()
                return 0
            if message == self.WM_HOTKEY and int(wparam) == self.HOTKEY_ID:
                self._request_cancel()
                return 0
        except Exception as exc:  # noqa: BLE001 - user32 callbacks cannot propagate exceptions.
            with self._lock:
                self._error = str(exc)
        return int(self.user32.DefWindowProcW(hwnd, message, wparam, lparam))

    def _locale_name(self) -> str:
        buffer = ctypes.create_unicode_buffer(85)
        return str(buffer.value or "en-US") if self.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)) > 0 else "en-US"

    def _dpi(self) -> int:
        return max(96, int(self.user32.GetDpiForSystem())) if hasattr(self.user32, "GetDpiForSystem") else 96

    def _client_animations_enabled(self) -> bool:
        enabled = wintypes.BOOL(True)
        if not self.user32.SystemParametersInfoW(self.SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
            return True
        return bool(enabled.value)

    def _set_capture_exclusion(self, hwnd: int) -> bool:
        ctypes.set_last_error(0)
        applied = bool(self.user32.SetWindowDisplayAffinity(hwnd, self.WDA_EXCLUDEFROMCAPTURE))
        exclude_error = int(ctypes.get_last_error()) if not applied else 0
        monitor_error = 0
        if not applied:
            ctypes.set_last_error(0)
            applied = bool(self.user32.SetWindowDisplayAffinity(hwnd, self.WDA_MONITOR))
            monitor_error = int(ctypes.get_last_error()) if not applied else 0
        affinity = wintypes.DWORD(0)
        read_back = bool(self.user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)))
        excluded = bool(applied and read_back and affinity.value in {self.WDA_MONITOR, self.WDA_EXCLUDEFROMCAPTURE})
        self._capture_affinity[int(hwnd)] = excluded
        self._capture_affinity_errors[int(hwnd)] = (exclude_error, monitor_error)
        return excluded

    def _verify_capture_exclusion(self, hwnd: int) -> bool:
        affinity = wintypes.DWORD(0)
        active = bool(
            self.user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
            and affinity.value in {self.WDA_MONITOR, self.WDA_EXCLUDEFROMCAPTURE}
        )
        self._capture_affinity[int(hwnd)] = active
        return active

    def _create_window(self, instance: int, *, title: str, rect: tuple[int, int, int, int], click_through: bool) -> int:
        left, top, width, height = rect
        ex_style = self.WS_EX_TOPMOST | self.WS_EX_TOOLWINDOW | self.WS_EX_NOACTIVATE
        if click_through:
            ex_style |= self.WS_EX_TRANSPARENT
        hwnd = self.user32.CreateWindowExW(ex_style, self._class_name, title, self.WS_POPUP, left, top, width, height, 0, 0, instance, None)
        if not hwnd:
            raise DesktopOverlayError(f"CreateWindowExW failed with Windows error {ctypes.get_last_error()}.")
        self._windows.append(int(hwnd))
        if not self._set_capture_exclusion(int(hwnd)):
            raise DesktopOverlayError(
                "The Computer Use overlay could not be excluded from desktop capture "
                f"(errors: {self._capture_affinity_errors.get(int(hwnd))})."
            )
        current_style = int(self.user32.GetWindowLongPtrW(hwnd, self.GWL_EXSTYLE))
        ctypes.set_last_error(0)
        previous_style = int(
            self.user32.SetWindowLongPtrW(hwnd, self.GWL_EXSTYLE, current_style | self.WS_EX_LAYERED)
        )
        if not previous_style and ctypes.get_last_error():
            raise DesktopOverlayError(
                f"SetWindowLongPtrW failed with Windows error {ctypes.get_last_error()}."
            )
        self.user32.SetWindowPos(
            hwnd,
            -1,
            left,
            top,
            width,
            height,
            self.SWP_NOACTIVATE | self.SWP_FRAMECHANGED | self.SWP_SHOWWINDOW,
        )
        return int(hwnd)

    def _create_layered_surface(self, hwnd: int, *, rect: tuple[int, int, int, int], pixels: bytes, kind: str) -> None:
        left, top, width, height = rect
        if len(pixels) != width * height * 4:
            raise DesktopOverlayError(f"Invalid {kind} pixel buffer size.")
        memory_dc = self.gdi32.CreateCompatibleDC(0)
        if not memory_dc:
            raise DesktopOverlayError("CreateCompatibleDC failed for the desktop overlay.")
        info = _BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = self.BI_RGB
        bits = ctypes.c_void_p()
        bitmap = self.gdi32.CreateDIBSection(memory_dc, ctypes.byref(info), self.DIB_RGB_COLORS, ctypes.byref(bits), 0, 0)
        if not bitmap or not bits.value:
            self.gdi32.DeleteDC(memory_dc)
            raise DesktopOverlayError("CreateDIBSection failed for the desktop overlay.")
        ctypes.memmove(bits, pixels, len(pixels))
        previous = self.gdi32.SelectObject(memory_dc, bitmap)
        surface = _LayeredSurface(int(hwnd), left, top, width, height, int(memory_dc), int(bitmap), int(previous or 0), kind)
        self._surfaces.append(surface)
        self._present_surface(surface, 255)
        if not self._verify_capture_exclusion(int(hwnd)):
            raise DesktopOverlayError(f"The {kind} overlay lost its desktop capture exclusion.")

    def _present_surface(self, surface: _LayeredSurface, opacity: int) -> None:
        destination = _POINT(surface.left, surface.top)
        size = _SIZE(surface.width, surface.height)
        source = _POINT(0, 0)
        blend = _BLENDFUNCTION(self.AC_SRC_OVER, 0, max(0, min(int(opacity), 255)), self.AC_SRC_ALPHA)
        if not self.user32.UpdateLayeredWindow(surface.hwnd, 0, ctypes.byref(destination), ctypes.byref(size), surface.memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), self.ULW_ALPHA):
            raise DesktopOverlayError(f"UpdateLayeredWindow failed for {surface.kind} with Windows error {ctypes.get_last_error()}.")

    def _release_surfaces(self) -> None:
        for surface in reversed(self._surfaces):
            if surface.previous_bitmap:
                self.gdi32.SelectObject(surface.memory_dc, surface.previous_bitmap)
            if surface.bitmap:
                self.gdi32.DeleteObject(surface.bitmap)
            if surface.memory_dc:
                self.gdi32.DeleteDC(surface.memory_dc)
        self._surfaces = []

    def _pulse_glows(self) -> None:
        if not self._surfaces:
            return
        self._pulse_step = (self._pulse_step + 1) % 16
        wave = (1.0 - math.cos(2.0 * math.pi * self._pulse_step / 16.0)) / 2.0
        peak = max(1, self._palette.glow_peak_alpha)
        low_ratio = max(0.4, min(self._palette.glow_rest_alpha / peak, 1.0))
        opacity = round(255 * (low_ratio + (1.0 - low_ratio) * wave))
        for surface in self._surfaces:
            self._present_surface(surface, opacity)

    def _make_font(self, pixel_height: int, weight: int) -> int:
        return int(self.gdi32.CreateFontW(-max(10, pixel_height), 0, 0, 0, weight, 0, 0, 0, self.DEFAULT_CHARSET, 0, 0, self.CLEARTYPE_QUALITY, 0, "Segoe UI") or 0)

    def _paint_banner(self, hdc: int) -> None:
        if self._geometry is None:
            return
        _, _, width, height = self._geometry.banner
        scale = self._geometry.scale
        palette = self._palette
        copy = self._copy

        surface_brush = self.gdi32.CreateSolidBrush(self._colorref(palette.surface))
        border_pen = self.gdi32.CreatePen(self.PS_SOLID, max(1, round(scale)), self._colorref(palette.border))
        old_brush = self.gdi32.SelectObject(hdc, surface_brush)
        old_pen = self.gdi32.SelectObject(hdc, border_pen)
        radius = round(16 * scale)
        self.gdi32.RoundRect(hdc, 0, 0, width, height, radius * 2, radius * 2)
        self.gdi32.SelectObject(hdc, old_brush)
        self.gdi32.SelectObject(hdc, old_pen)
        self.gdi32.DeleteObject(surface_brush)
        self.gdi32.DeleteObject(border_pen)

        icon_size = round(36 * scale)
        icon_left = round(10 * scale)
        icon_top = (height - icon_size) // 2
        icon_brush = self.gdi32.CreateSolidBrush(self._colorref(palette.icon_surface))
        icon_pen = self.gdi32.CreatePen(self.PS_SOLID, max(1, round(scale)), self._colorref(palette.border))
        old_brush = self.gdi32.SelectObject(hdc, icon_brush)
        old_pen = self.gdi32.SelectObject(hdc, icon_pen)
        icon_radius = round(9 * scale)
        self.gdi32.RoundRect(hdc, icon_left, icon_top, icon_left + icon_size, icon_top + icon_size, icon_radius * 2, icon_radius * 2)
        self.gdi32.SelectObject(hdc, old_brush)
        self.gdi32.SelectObject(hdc, old_pen)
        self.gdi32.DeleteObject(icon_brush)
        self.gdi32.DeleteObject(icon_pen)

        accent_pen = self.gdi32.CreatePen(self.PS_SOLID, max(1, round(1.5 * scale)), self._colorref(palette.accent))
        old_pen = self.gdi32.SelectObject(hdc, accent_pen)
        old_brush = self.gdi32.SelectObject(hdc, self.gdi32.GetStockObject(self.NULL_BRUSH))
        monitor_left = icon_left + round(9 * scale)
        monitor_top = icon_top + round(9 * scale)
        monitor_width = round(18 * scale)
        monitor_height = round(13 * scale)
        self.gdi32.RoundRect(hdc, monitor_left, monitor_top, monitor_left + monitor_width, monitor_top + monitor_height, round(3 * scale), round(3 * scale))
        center_x = monitor_left + monitor_width // 2
        self.gdi32.MoveToEx(hdc, center_x, monitor_top + monitor_height, None)
        self.gdi32.LineTo(hdc, center_x, monitor_top + monitor_height + round(4 * scale))
        self.gdi32.MoveToEx(hdc, center_x - round(4 * scale), monitor_top + monitor_height + round(4 * scale), None)
        self.gdi32.LineTo(hdc, center_x + round(4 * scale), monitor_top + monitor_height + round(4 * scale))
        self.gdi32.SelectObject(hdc, old_brush)
        self.gdi32.SelectObject(hdc, old_pen)
        self.gdi32.DeleteObject(accent_pen)

        dot_size = round(7 * scale)
        dot_left = icon_left + icon_size - dot_size + round(scale)
        dot_top = icon_top + icon_size - dot_size + round(scale)
        dot_brush = self.gdi32.CreateSolidBrush(self._colorref(palette.accent))
        dot_pen = self.gdi32.CreatePen(self.PS_SOLID, max(1, round(scale)), self._colorref(palette.surface))
        old_brush = self.gdi32.SelectObject(hdc, dot_brush)
        old_pen = self.gdi32.SelectObject(hdc, dot_pen)
        self.gdi32.Ellipse(hdc, dot_left, dot_top, dot_left + dot_size, dot_top + dot_size)
        self.gdi32.SelectObject(hdc, old_brush)
        self.gdi32.SelectObject(hdc, old_pen)
        self.gdi32.DeleteObject(dot_brush)
        self.gdi32.DeleteObject(dot_pen)

        stop_left, stop_top, stop_width, stop_height = self._geometry.stop
        stop_surface = palette.icon_surface if self._cancel_requested else palette.stop_surface
        stop_color = palette.muted_text if self._cancel_requested else palette.stop
        stop_brush = self.gdi32.CreateSolidBrush(self._colorref(stop_surface))
        stop_pen = self.gdi32.CreatePen(self.PS_SOLID, max(1, round(scale)), self._colorref(stop_color))
        old_brush = self.gdi32.SelectObject(hdc, stop_brush)
        old_pen = self.gdi32.SelectObject(hdc, stop_pen)
        self.gdi32.RoundRect(hdc, stop_left, stop_top, stop_left + stop_width, stop_top + stop_height, stop_height, stop_height)
        self.gdi32.SelectObject(hdc, old_brush)
        self.gdi32.SelectObject(hdc, old_pen)
        self.gdi32.DeleteObject(stop_brush)
        self.gdi32.DeleteObject(stop_pen)

        stop_icon = round(8 * scale)
        stop_icon_left = stop_left + round(12 * scale)
        stop_icon_top = stop_top + (stop_height - stop_icon) // 2
        stop_icon_brush = self.gdi32.CreateSolidBrush(self._colorref(stop_color))
        old_brush = self.gdi32.SelectObject(hdc, stop_icon_brush)
        self.gdi32.Rectangle(hdc, stop_icon_left, stop_icon_top, stop_icon_left + stop_icon, stop_icon_top + stop_icon)
        self.gdi32.SelectObject(hdc, old_brush)
        self.gdi32.DeleteObject(stop_icon_brush)

        title_font = self._make_font(round(14 * scale), self.FW_SEMIBOLD)
        detail_font = self._make_font(round(11 * scale), self.FW_NORMAL)
        stop_font = self._make_font(round(11 * scale), self.FW_SEMIBOLD)
        self.gdi32.SetBkMode(hdc, self.TRANSPARENT)
        text_left = icon_left + icon_size + round(12 * scale)
        text_right = stop_left - round(12 * scale)
        title_rect = _RECT(text_left, round(7 * scale), text_right, round(29 * scale))
        detail_rect = _RECT(text_left, round(27 * scale), text_right, height - round(6 * scale))
        for font, color, text, rect, flags in (
            (title_font, palette.text, copy.stopping if self._cancel_requested else copy.title, title_rect, self.DT_LEFT),
            (detail_font, palette.muted_text, self._detail_override or copy.detail, detail_rect, self.DT_LEFT),
        ):
            if font:
                previous_font = self.gdi32.SelectObject(hdc, font)
                self.gdi32.SetTextColor(hdc, self._colorref(color))
                self.user32.DrawTextW(hdc, text, -1, ctypes.byref(rect), flags | self.DT_VCENTER | self.DT_SINGLELINE | self.DT_END_ELLIPSIS)
                self.gdi32.SelectObject(hdc, previous_font)
        if stop_font:
            previous_font = self.gdi32.SelectObject(hdc, stop_font)
            self.gdi32.SetTextColor(hdc, self._colorref(stop_color))
            stop_rect = _RECT(stop_icon_left + stop_icon + round(7 * scale), stop_top, stop_left + stop_width - round(8 * scale), stop_top + stop_height)
            self.user32.DrawTextW(hdc, copy.stopping if self._cancel_requested else copy.stop, -1, ctypes.byref(stop_rect), self.DT_CENTER | self.DT_VCENTER | self.DT_SINGLELINE | self.DT_END_ELLIPSIS)
            self.gdi32.SelectObject(hdc, previous_font)
        for font in (title_font, detail_font, stop_font):
            if font:
                self.gdi32.DeleteObject(font)

        shimmer_pen = self.gdi32.CreatePen(self.PS_SOLID, max(1, round(scale)), self._colorref(palette.accent))
        old_pen = self.gdi32.SelectObject(hdc, shimmer_pen)
        self.gdi32.MoveToEx(hdc, round(width * 0.38), height - 1, None)
        self.gdi32.LineTo(hdc, round(width * 0.62), height - 1)
        self.gdi32.SelectObject(hdc, old_pen)
        self.gdi32.DeleteObject(shimmer_pen)

    def _run(self) -> None:
        instance = self.kernel32.GetModuleHandleW(None)
        self._thread_id = int(self.kernel32.GetCurrentThreadId())
        previous_dpi_context = None
        if hasattr(self.user32, "SetThreadDpiAwarenessContext"):
            previous_dpi_context = self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        window_class = _WNDCLASSW()
        window_class.lpfnWndProc = self._wnd_proc_callback
        window_class.hInstance = instance
        window_class.lpszClassName = self._class_name
        registered = False
        try:
            if not self.user32.RegisterClassW(ctypes.byref(window_class)):
                raise DesktopOverlayError(f"RegisterClassW failed with Windows error {ctypes.get_last_error()}.")
            registered = True
            virtual_rect = (
                int(self.user32.GetSystemMetrics(self.SM_XVIRTUALSCREEN)),
                int(self.user32.GetSystemMetrics(self.SM_YVIRTUALSCREEN)),
                int(self.user32.GetSystemMetrics(self.SM_CXVIRTUALSCREEN)),
                int(self.user32.GetSystemMetrics(self.SM_CYVIRTUALSCREEN)),
            )
            self._geometry = resolve_desktop_overlay_geometry(virtual_rect=virtual_rect, primary_width=int(self.user32.GetSystemMetrics(self.SM_CXSCREEN)), dpi=self._dpi())
            self._copy = resolve_desktop_overlay_copy(self._locale_name())
            self._motion_enabled = self._client_animations_enabled()
            top_rect = self._geometry.top_glow
            top_pixels = build_directional_glow_pixels(top_rect[2], top_rect[3], color=self._palette.accent, peak_alpha=self._palette.glow_peak_alpha, focus_x=self._geometry.top_glow_focus_x)
            top_hwnd = self._create_window(instance, title="VRCForge Desktop Ambient", rect=top_rect, click_through=True)
            self._create_layered_surface(top_hwnd, rect=top_rect, pixels=top_pixels, kind="top-aurora")
            corner_alpha = max(6, round(self._palette.glow_peak_alpha * 0.42))
            for kind, rect, corner in (
                ("bottom-left-ember", self._geometry.bottom_left_glow, "left"),
                ("bottom-right-ember", self._geometry.bottom_right_glow, "right"),
            ):
                pixels = build_directional_glow_pixels(rect[2], rect[3], color=self._palette.accent, peak_alpha=corner_alpha, corner=corner)
                hwnd = self._create_window(instance, title="VRCForge Desktop Ambient", rect=rect, click_through=True)
                self._create_layered_surface(hwnd, rect=rect, pixels=pixels, kind=kind)

            banner_rect = self._geometry.banner
            self._banner_hwnd = self._create_window(instance, title="VRCForge Desktop Activity", rect=banner_rect, click_through=False)
            radius = round(16 * self._geometry.scale)
            region = self.gdi32.CreateRoundRectRgn(0, 0, banner_rect[2] + 1, banner_rect[3] + 1, radius * 2, radius * 2)
            if region and not self.user32.SetWindowRgn(self._banner_hwnd, region, True):
                self.gdi32.DeleteObject(region)
            if not self.user32.SetLayeredWindowAttributes(self._banner_hwnd, 0, self._palette.banner_alpha, self.LWA_ALPHA):
                raise DesktopOverlayError(f"SetLayeredWindowAttributes failed with Windows error {ctypes.get_last_error()}.")
            self.user32.InvalidateRect(self._banner_hwnd, None, True)
            if not self._verify_capture_exclusion(self._banner_hwnd):
                raise DesktopOverlayError("The Computer Use banner lost its desktop capture exclusion.")
            self._hotkey_available = bool(self.user32.RegisterHotKey(self._banner_hwnd, self.HOTKEY_ID, self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT, self.VK_F12))
            if self._motion_enabled:
                self._timer_id = int(self.user32.SetTimer(self._banner_hwnd, self.PULSE_TIMER_ID, self.PULSE_INTERVAL_MS, None) or 0)
                if not self._timer_id:
                    self._motion_enabled = False
            self._ready.set()
            message = wintypes.MSG()
            while True:
                result = int(self.user32.GetMessageW(ctypes.byref(message), 0, 0, 0))
                if result == -1:
                    raise DesktopOverlayError(f"GetMessageW failed with Windows error {ctypes.get_last_error()}.")
                if result == 0:
                    break
                self.user32.TranslateMessage(ctypes.byref(message))
                self.user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:  # noqa: BLE001 - startup errors are handed back to the worker.
            with self._lock:
                self._error = str(exc)
            self._ready.set()
        finally:
            if self._timer_id and self._banner_hwnd:
                self.user32.KillTimer(self._banner_hwnd, self._timer_id)
            if self._hotkey_available and self._banner_hwnd:
                self.user32.UnregisterHotKey(self._banner_hwnd, self.HOTKEY_ID)
            for hwnd in reversed(self._windows):
                self.user32.DestroyWindow(hwnd)
            self._release_surfaces()
            self._windows = []
            self._capture_affinity = {}
            self._capture_affinity_errors = {}
            self._banner_hwnd = 0
            self._geometry = None
            self._timer_id = 0
            self._hotkey_available = False
            if registered:
                self.user32.UnregisterClassW(self._class_name, instance)
            if previous_dpi_context and hasattr(self.user32, "SetThreadDpiAwarenessContext"):
                self.user32.SetThreadDpiAwarenessContext(previous_dpi_context)
            with self._lock:
                self._thread_id = 0
                self._cancel_callback = None
