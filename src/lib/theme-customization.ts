export const THEME_PALETTE_IDS = ["default", "ocean", "violet", "sakura", "forest", "sunset", "custom"] as const;

export type ThemePaletteId = (typeof THEME_PALETTE_IDS)[number];

export type ThemePaletteOption = {
  id: ThemePaletteId;
  swatches: readonly [string, string, string];
};

export const THEME_PALETTES: readonly ThemePaletteOption[] = [
  { id: "default", swatches: ["#2563eb", "#f8fafc", "#e2e8f0"] },
  { id: "ocean", swatches: ["#0891b2", "#38bdf8", "#ecfeff"] },
  { id: "violet", swatches: ["#7c3aed", "#c084fc", "#f5f3ff"] },
  { id: "sakura", swatches: ["#db2777", "#fb7185", "#fff1f2"] },
  { id: "forest", swatches: ["#059669", "#84cc16", "#ecfdf5"] },
  { id: "sunset", swatches: ["#ea580c", "#f59e0b", "#fff7ed"] },
  { id: "custom", swatches: ["#2563eb", "#8b5cf6", "#ec4899"] },
];

export type ThemeCustomization = {
  palette: ThemePaletteId;
  accentColor: string;
  backgroundImagePath: string;
  backgroundOpacity: number;
};

export const THEME_CUSTOMIZATION_STORAGE_KEY = "vrcforge_theme_customization";

export const DEFAULT_THEME_CUSTOMIZATION: ThemeCustomization = {
  palette: "default",
  accentColor: "",
  backgroundImagePath: "",
  backgroundOpacity: 0.18,
};

const HEX_COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
const LEGACY_IMAGE_DATA_URL_PATTERN = /^data:image\/(?:png|jpeg|webp|gif);base64,/i;

export function normalizeThemeCustomization(value: unknown): ThemeCustomization {
  const candidate = value && typeof value === "object" ? (value as Partial<ThemeCustomization>) : {};
  const accentColor = typeof candidate.accentColor === "string" && HEX_COLOR_PATTERN.test(candidate.accentColor)
    ? candidate.accentColor.toLowerCase()
    : "";
  const requestedPalette = typeof candidate.palette === "string" && THEME_PALETTE_IDS.includes(candidate.palette as ThemePaletteId)
    ? candidate.palette as ThemePaletteId
    : accentColor
      ? "custom"
      : "default";
  const backgroundImagePath =
    typeof candidate.backgroundImagePath === "string"
    && candidate.backgroundImagePath.length <= 4096
    && !candidate.backgroundImagePath.toLowerCase().startsWith("data:")
      ? candidate.backgroundImagePath
      : "";
  const opacity = Number(candidate.backgroundOpacity);
  return {
    palette: requestedPalette,
    accentColor,
    backgroundImagePath,
    backgroundOpacity: Number.isFinite(opacity) ? Math.min(1, Math.max(0, opacity)) : DEFAULT_THEME_CUSTOMIZATION.backgroundOpacity,
  };
}

export function loadThemeCustomization(): ThemeCustomization {
  try {
    const raw = window.localStorage.getItem(THEME_CUSTOMIZATION_STORAGE_KEY);
    return raw ? normalizeThemeCustomization(JSON.parse(raw)) : DEFAULT_THEME_CUSTOMIZATION;
  } catch {
    return DEFAULT_THEME_CUSTOMIZATION;
  }
}

export function loadLegacyThemeBackgroundDataUrl(): string {
  try {
    const raw = window.localStorage.getItem(THEME_CUSTOMIZATION_STORAGE_KEY);
    if (!raw) return "";
    const value = JSON.parse(raw) as { backgroundImageDataUrl?: unknown };
    return typeof value.backgroundImageDataUrl === "string" && LEGACY_IMAGE_DATA_URL_PATTERN.test(value.backgroundImageDataUrl)
      ? value.backgroundImageDataUrl
      : "";
  } catch {
    return "";
  }
}

export function hexColorToHslChannels(color: string): string | null {
  if (!HEX_COLOR_PATTERN.test(color)) {
    return null;
  }
  const red = Number.parseInt(color.slice(1, 3), 16) / 255;
  const green = Number.parseInt(color.slice(3, 5), 16) / 255;
  const blue = Number.parseInt(color.slice(5, 7), 16) / 255;
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  let hue = 0;
  if (delta > 0) {
    if (max === red) hue = ((green - blue) / delta) % 6;
    else if (max === green) hue = (blue - red) / delta + 2;
    else hue = (red - green) / delta + 4;
    hue *= 60;
    if (hue < 0) hue += 360;
  }
  const lightness = (max + min) / 2;
  const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1));
  return `${Math.round(hue)} ${Math.round(saturation * 100)}% ${Math.round(lightness * 100)}%`;
}
