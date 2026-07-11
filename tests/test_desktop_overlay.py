from __future__ import annotations

import ctypes
import sys
import threading
import time
import unittest
from ctypes import wintypes

from desktop_overlay_visuals import (
    build_directional_glow_pixels,
    resolve_desktop_overlay_copy,
    resolve_desktop_overlay_geometry,
    resolve_desktop_overlay_palette,
)


def _alpha_at(pixels: bytes, width: int, x: int, y: int) -> int:
    return pixels[(y * width + x) * 4 + 3]


class DesktopOverlayVisualTests(unittest.TestCase):
    def test_palette_and_copy_are_theme_and_locale_aware(self) -> None:
        light = resolve_desktop_overlay_palette("light")
        dark = resolve_desktop_overlay_palette("dark")

        self.assertEqual(light.theme, "light")
        self.assertEqual(dark.theme, "dark")
        self.assertLess(light.banner_alpha, 230)
        self.assertNotEqual(light.accent, dark.accent)
        self.assertIn("电脑", resolve_desktop_overlay_copy("zh-CN").title)
        self.assertIn("電腦", resolve_desktop_overlay_copy("zh-TW").title)
        self.assertIn("コンピューター", resolve_desktop_overlay_copy("ja-JP").title)

    def test_geometry_scales_and_keeps_stop_inside_banner(self) -> None:
        at_96 = resolve_desktop_overlay_geometry(
            virtual_rect=(-1920, 0, 4480, 1440),
            primary_width=2560,
            dpi=96,
        )
        at_144 = resolve_desktop_overlay_geometry(
            virtual_rect=(-1920, 0, 4480, 1440),
            primary_width=2560,
            dpi=144,
        )

        self.assertEqual(at_96.banner[2:], (620, 56))
        self.assertEqual(at_144.banner[2:], (930, 84))
        stop_left, stop_top, stop_width, stop_height = at_144.stop
        self.assertGreaterEqual(stop_left, 0)
        self.assertGreaterEqual(stop_top, 0)
        self.assertLessEqual(stop_left + stop_width, at_144.banner[2])
        self.assertLessEqual(stop_top + stop_height, at_144.banner[3])
        self.assertEqual(at_144.top_glow[0], -1920)

    def test_top_aurora_is_soft_directional_and_premultiplied(self) -> None:
        width, height = 17, 9
        pixels = build_directional_glow_pixels(
            width,
            height,
            color=(37, 99, 235),
            peak_alpha=32,
            focus_x=width // 2,
        )

        self.assertEqual(len(pixels), width * height * 4)
        self.assertGreater(_alpha_at(pixels, width, width // 2, 0), _alpha_at(pixels, width, 0, 0))
        self.assertGreater(_alpha_at(pixels, width, width // 2, 0), _alpha_at(pixels, width, width // 2, height - 1))
        self.assertEqual(_alpha_at(pixels, width, width // 2, height - 1), 0)
        for index in range(0, len(pixels), 4):
            blue, green, red, alpha = pixels[index : index + 4]
            self.assertLessEqual(max(red, green, blue), alpha)

    def test_corner_embers_fade_toward_the_center(self) -> None:
        width, height = 15, 7
        left = build_directional_glow_pixels(
            width,
            height,
            color=(37, 99, 235),
            peak_alpha=14,
            corner="left",
        )
        right = build_directional_glow_pixels(
            width,
            height,
            color=(37, 99, 235),
            peak_alpha=14,
            corner="right",
        )

        self.assertGreater(_alpha_at(left, width, 0, height - 1), _alpha_at(left, width, width - 1, height - 1))
        self.assertGreater(_alpha_at(right, width, width - 1, height - 1), _alpha_at(right, width, 0, height - 1))


@unittest.skipUnless(sys.platform == "win32", "Native overlay requires Win32")
class WindowsDesktopOverlayTests(unittest.TestCase):
    def test_native_overlay_is_capture_excluded_and_stop_only(self) -> None:
        from desktop_overlay import WindowsDesktopActivityOverlay

        cancelled = threading.Event()
        callback_count = 0

        def cancel() -> None:
            nonlocal callback_count
            callback_count += 1
            cancelled.set()

        overlay = WindowsDesktopActivityOverlay()
        try:
            overlay.show(cancel, theme="light")
            diagnostics = overlay.diagnostics()
            self.assertEqual(diagnostics["renderer"], "win32-layered-ambient-v2")
            self.assertTrue(diagnostics["visible"])
            self.assertTrue(diagnostics["captureExcluded"])
            self.assertEqual(diagnostics["windowCount"], 4)
            self.assertEqual(diagnostics["glowWindowCount"], 3)
            self.assertEqual(diagnostics["fontFamily"], "Segoe UI")

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.SendMessageW.restype = ctypes.c_ssize_t
            banner = overlay._banner_hwnd  # noqa: SLF001 - native smoke verifies the real hit target.
            stop_left, stop_top, stop_width, stop_height = overlay._geometry.stop  # noqa: SLF001
            previous_dpi_context = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            try:
                outside = (2 << 16) | 2
                user32.SendMessageW(banner, overlay.WM_LBUTTONUP, 0, outside)
                time.sleep(0.05)
                self.assertFalse(cancelled.is_set())

                stop_x = stop_left + stop_width // 2
                stop_y = stop_top + stop_height // 2
                inside = ((stop_y & 0xFFFF) << 16) | (stop_x & 0xFFFF)
                user32.SendMessageW(banner, overlay.WM_LBUTTONUP, 0, inside)
                self.assertTrue(cancelled.wait(1.0))
                user32.SendMessageW(banner, overlay.WM_LBUTTONUP, 0, inside)
                time.sleep(0.05)
            finally:
                if previous_dpi_context:
                    user32.SetThreadDpiAwarenessContext(previous_dpi_context)
            self.assertEqual(callback_count, 1)
            self.assertTrue(overlay.diagnostics()["stopping"])
        finally:
            overlay.hide()

        self.assertFalse(overlay.diagnostics()["visible"])
        self.assertEqual(overlay.diagnostics()["windowCount"], 0)


if __name__ == "__main__":
    unittest.main()
