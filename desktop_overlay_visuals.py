from __future__ import annotations

from dataclasses import dataclass, replace


Rgb = tuple[int, int, int]
Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class DesktopOverlayPalette:
    theme: str
    accent: Rgb
    surface: Rgb
    text: Rgb
    muted_text: Rgb
    border: Rgb
    icon_surface: Rgb
    stop: Rgb
    stop_surface: Rgb
    banner_alpha: int
    glow_peak_alpha: int
    glow_rest_alpha: int


@dataclass(frozen=True)
class DesktopOverlayCopy:
    title: str
    detail: str
    stop: str
    stopping: str


@dataclass(frozen=True)
class DesktopOverlayGeometry:
    scale: float
    dpi: int
    banner: Rect
    stop: Rect
    top_glow: Rect
    left_edge_glow: Rect
    right_edge_glow: Rect
    bottom_edge_glow: Rect
    top_glow_focus_x: int


def normalize_overlay_theme(theme: str) -> str:
    return "dark" if str(theme or "").strip().lower() == "dark" else "light"


def parse_overlay_accent(value: str) -> Rgb | None:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def _mix(color: Rgb, toward: Rgb, ratio: float) -> Rgb:
    safe_ratio = max(0.0, min(float(ratio), 1.0))
    return (
        round(color[0] + (toward[0] - color[0]) * safe_ratio),
        round(color[1] + (toward[1] - color[1]) * safe_ratio),
        round(color[2] + (toward[2] - color[2]) * safe_ratio),
    )


def resolve_desktop_overlay_palette(theme: str, accent: str | None = None) -> DesktopOverlayPalette:
    normalized = normalize_overlay_theme(theme)
    if normalized == "dark":
        palette = DesktopOverlayPalette(
            theme="dark",
            accent=(96, 145, 255),
            surface=(28, 31, 38),
            text=(245, 247, 250),
            muted_text=(178, 185, 198),
            border=(86, 112, 170),
            icon_surface=(40, 52, 76),
            stop=(255, 116, 132),
            stop_surface=(63, 36, 45),
            banner_alpha=230,
            glow_peak_alpha=36,
            glow_rest_alpha=25,
        )
        border_ratio, icon_ratio = 0.35, 0.82
    else:
        palette = DesktopOverlayPalette(
            theme="light",
            accent=(37, 99, 235),
            surface=(246, 249, 255),
            text=(25, 31, 42),
            muted_text=(91, 101, 117),
            border=(150, 174, 226),
            icon_surface=(226, 235, 255),
            stop=(190, 35, 66),
            stop_surface=(255, 231, 236),
            banner_alpha=218,
            glow_peak_alpha=32,
            glow_rest_alpha=21,
        )
        border_ratio, icon_ratio = 0.52, 0.90
    custom_accent = parse_overlay_accent(accent or "")
    if custom_accent is None:
        return palette
    return replace(
        palette,
        accent=custom_accent,
        border=_mix(custom_accent, palette.surface, border_ratio),
        icon_surface=_mix(custom_accent, palette.surface, icon_ratio),
    )


def resolve_desktop_overlay_copy(locale_name: str) -> DesktopOverlayCopy:
    normalized = str(locale_name or "").replace("_", "-").lower()
    if normalized.startswith(("zh-tw", "zh-hk", "zh-mo")):
        return DesktopOverlayCopy(
            title="VRCForge 正在使用你的電腦",
            detail="電腦操作已啟用 · Ctrl+Shift+F12 可隨時停止",
            stop="停止",
            stopping="正在停止",
        )
    if normalized.startswith("zh"):
        return DesktopOverlayCopy(
            title="VRCForge 正在使用你的电脑",
            detail="电脑操作已启用 · Ctrl+Shift+F12 可随时停止",
            stop="停止",
            stopping="正在停止",
        )
    if normalized.startswith("ja"):
        return DesktopOverlayCopy(
            title="VRCForge がコンピューターを操作中",
            detail="コンピューター操作が有効です · Ctrl+Shift+F12 で停止",
            stop="停止",
            stopping="停止中",
        )
    return DesktopOverlayCopy(
        title="VRCForge is using your computer",
        detail="Computer control is active · Ctrl+Shift+F12 stops it at any time",
        stop="Stop",
        stopping="Stopping",
    )


def resolve_desktop_overlay_geometry(
    *,
    virtual_rect: Rect,
    primary_width: int,
    dpi: int,
) -> DesktopOverlayGeometry:
    virtual_left, virtual_top, virtual_width, virtual_height = virtual_rect
    safe_dpi = max(96, min(int(dpi or 96), 240))
    scale = safe_dpi / 96.0

    side_margin = round(18 * scale)
    banner_width = min(round(620 * scale), max(round(420 * scale), primary_width - side_margin * 2))
    banner_height = round(56 * scale)
    banner_left = max(side_margin, (primary_width - banner_width) // 2)
    banner_top = round(8 * scale)
    banner = (banner_left, banner_top, banner_width, banner_height)

    stop_width = round(86 * scale)
    stop_height = round(32 * scale)
    stop_right_margin = round(10 * scale)
    stop = (
        banner_width - stop_right_margin - stop_width,
        (banner_height - stop_height) // 2,
        stop_width,
        stop_height,
    )

    top_height = min(max(round(180 * scale), 120), max(120, int(virtual_height * 0.28)))
    top_glow = (virtual_left, virtual_top, virtual_width, top_height)
    edge_width = min(max(round(110 * scale), 80), max(80, int(virtual_width * 0.12)))
    edge_height = min(max(round(120 * scale), 90), max(90, int(virtual_height * 0.14)))
    left_edge = (virtual_left, virtual_top, edge_width, virtual_height)
    right_edge = (virtual_left + virtual_width - edge_width, virtual_top, edge_width, virtual_height)
    bottom_edge = (virtual_left, virtual_top + virtual_height - edge_height, virtual_width, edge_height)
    top_glow_focus_x = banner_left + banner_width // 2 - virtual_left

    return DesktopOverlayGeometry(
        scale=scale,
        dpi=safe_dpi,
        banner=banner,
        stop=stop,
        top_glow=top_glow,
        left_edge_glow=left_edge,
        right_edge_glow=right_edge,
        bottom_edge_glow=bottom_edge,
        top_glow_focus_x=top_glow_focus_x,
    )


def _premultiplied_pixel(color: Rgb, alpha: int) -> bytes:
    safe_alpha = max(0, min(int(alpha), 255))
    red, green, blue = color
    return bytes(
        (
            round(blue * safe_alpha / 255),
            round(green * safe_alpha / 255),
            round(red * safe_alpha / 255),
            safe_alpha,
        )
    )


def build_directional_glow_pixels(
    width: int,
    height: int,
    *,
    color: Rgb,
    peak_alpha: int,
    focus_x: int | None = None,
) -> bytes:
    safe_width = max(1, int(width))
    safe_height = max(1, int(height))
    peak = max(0, min(int(peak_alpha), 96))
    pixels = bytearray(safe_width * safe_height * 4)
    write = 0

    center = min(max(int(focus_x if focus_x is not None else safe_width // 2), 0), safe_width - 1)
    horizontal_radius = max(1.0, safe_width * 0.58)
    for y in range(safe_height):
        y_ratio = y / max(1, safe_height - 1)
        vertical = max(0.0, 1.0 - y_ratio) ** 2.15
        for x in range(safe_width):
            horizontal_distance = abs(x - center) / horizontal_radius
            horizontal = max(0.0, 1.0 - horizontal_distance * horizontal_distance)
            intensity = vertical * (0.26 + 0.74 * horizontal)
            alpha = round(peak * intensity)
            pixels[write : write + 4] = _premultiplied_pixel(color, alpha)
            write += 4
    return bytes(pixels)


def build_edge_glow_pixels(
    width: int,
    height: int,
    *,
    color: Rgb,
    peak_alpha: int,
    side: str,
) -> bytes:
    if side not in {"left", "right", "bottom"}:
        raise ValueError(f"Unsupported edge glow side: {side!r}")
    safe_width = max(1, int(width))
    safe_height = max(1, int(height))
    peak = max(0, min(int(peak_alpha), 96))

    if side in {"left", "right"}:
        row = bytearray()
        for x in range(safe_width):
            ratio = x / max(1, safe_width - 1)
            if side == "left":
                ratio = 1.0 - ratio
            alpha = round(peak * ratio**2.2)
            row += _premultiplied_pixel(color, alpha)
        return bytes(row) * safe_height

    rows = bytearray()
    for y in range(safe_height):
        ratio = y / max(1, safe_height - 1)
        alpha = round(peak * ratio**2.2)
        rows += _premultiplied_pixel(color, alpha) * safe_width
    return bytes(rows)
