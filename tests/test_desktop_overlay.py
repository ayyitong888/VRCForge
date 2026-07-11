from __future__ import annotations

import ctypes
import sys
import threading
import time
import unittest
from ctypes import wintypes

from desktop_overlay_visuals import (
    build_directional_glow_pixels,
    build_edge_glow_pixels,
    parse_overlay_accent,
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

    def test_custom_accent_overrides_palette_and_derives_companions(self) -> None:
        self.assertEqual(parse_overlay_accent("#8b5cf6"), (139, 92, 246))
        self.assertEqual(parse_overlay_accent("8B5CF6"), (139, 92, 246))
        self.assertEqual(parse_overlay_accent("#f0a"), (255, 0, 170))
        self.assertIsNone(parse_overlay_accent(""))
        self.assertIsNone(parse_overlay_accent("#12345"))
        self.assertIsNone(parse_overlay_accent("not-a-color"))

        default_light = resolve_desktop_overlay_palette("light")
        untouched = resolve_desktop_overlay_palette("light", accent="nonsense")
        self.assertEqual(untouched, default_light)

        custom = resolve_desktop_overlay_palette("light", accent="#8b5cf6")
        self.assertEqual(custom.accent, (139, 92, 246))
        self.assertNotEqual(custom.border, default_light.border)
        self.assertNotEqual(custom.icon_surface, default_light.icon_surface)
        # Companion tints must stay theme-consistent and stop must stay red.
        self.assertEqual(custom.surface, default_light.surface)
        self.assertEqual(custom.stop, default_light.stop)
        self.assertEqual(custom.banner_alpha, default_light.banner_alpha)

        dark_custom = resolve_desktop_overlay_palette("dark", accent="8b5cf6")
        self.assertEqual(dark_custom.accent, (139, 92, 246))
        self.assertEqual(dark_custom.stop, resolve_desktop_overlay_palette("dark").stop)

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

        # Perimeter ring strips hug the virtual-screen edges on every DPI.
        for geometry in (at_96, at_144):
            left_rect = geometry.left_edge_glow
            right_rect = geometry.right_edge_glow
            bottom_rect = geometry.bottom_edge_glow
            self.assertEqual(left_rect[0], -1920)
            self.assertEqual(left_rect[3], 1440)
            self.assertEqual(right_rect[0] + right_rect[2], -1920 + 4480)
            self.assertEqual(right_rect[3], 1440)
            self.assertEqual(bottom_rect[0], -1920)
            self.assertEqual(bottom_rect[2], 4480)
            self.assertEqual(bottom_rect[1] + bottom_rect[3], 1440)
            self.assertEqual(left_rect[2], right_rect[2])
        self.assertGreater(at_144.left_edge_glow[2], at_96.left_edge_glow[2])
        self.assertGreater(at_144.bottom_edge_glow[3], at_96.bottom_edge_glow[3])

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

    def test_edge_glows_peak_at_their_screen_edge_and_fade_inward(self) -> None:
        width, height = 15, 7
        color = (37, 99, 235)
        left = build_edge_glow_pixels(width, height, color=color, peak_alpha=18, side="left")
        right = build_edge_glow_pixels(width, height, color=color, peak_alpha=18, side="right")
        bottom = build_edge_glow_pixels(width, height, color=color, peak_alpha=18, side="bottom")

        for pixels in (left, right, bottom):
            self.assertEqual(len(pixels), width * height * 4)

        # Left strip: brightest at x=0, fully faded at the inner edge; uniform per column.
        self.assertEqual(_alpha_at(left, width, 0, 0), 18)
        self.assertGreater(_alpha_at(left, width, 0, 3), _alpha_at(left, width, width // 2, 3))
        self.assertEqual(_alpha_at(left, width, width - 1, 3), 0)
        self.assertEqual(_alpha_at(left, width, 2, 0), _alpha_at(left, width, 2, height - 1))

        # Right strip mirrors the left one.
        self.assertEqual(_alpha_at(right, width, width - 1, 3), 18)
        self.assertEqual(_alpha_at(right, width, 0, 3), 0)

        # Bottom strip: brightest on the last row, faded at the top; uniform per row.
        self.assertEqual(_alpha_at(bottom, width, 4, height - 1), 18)
        self.assertEqual(_alpha_at(bottom, width, 4, 0), 0)
        self.assertEqual(_alpha_at(bottom, width, 0, 4), _alpha_at(bottom, width, width - 1, 4))

        # Premultiplied BGRA invariant holds for the ring strips too.
        for pixels in (left, right, bottom):
            for index in range(0, len(pixels), 4):
                blue, green, red, alpha = pixels[index : index + 4]
                self.assertLessEqual(max(red, green, blue), alpha)

        with self.assertRaises(ValueError):
            build_edge_glow_pixels(width, height, color=color, peak_alpha=18, side="top")


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
            self.assertEqual(diagnostics["windowCount"], 5)
            self.assertEqual(diagnostics["glowWindowCount"], 4)
            self.assertEqual(diagnostics["fontFamily"], "Segoe UI")
            self.assertEqual(diagnostics["accentSource"], "theme")
            self.assertEqual(diagnostics["accent"], "#2563eb")

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
