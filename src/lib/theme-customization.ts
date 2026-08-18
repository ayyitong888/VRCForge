import { parseThemeColor, themeColorToHsl } from "./theme-color";

export const THEME_PALETTE_IDS = ["default", "ocean", "violet", "sakura", "forest", "sunset", "custom"] as const;

export type ThemePaletteId = (typeof THEME_PALETTE_IDS)[number];

export const THEME_BACKGROUND_SCOPE_IDS = ["workspace", "app"] as const;

export type ThemeBackgroundScope = (typeof THEME_BACKGROUND_SCOPE_IDS)[number];

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
  surfaceColor: string;
  recentColors: string[];
  backgroundImagePath: string;
  backgroundOpacity: number;
  backgroundScope: ThemeBackgroundScope;
};

export const THEME_CUSTOMIZATION_STORAGE_KEY = "vrcforge_theme_customization";

export const DEFAULT_THEME_CUSTOMIZATION: ThemeCustomization = {
  palette: "default",
  accentColor: "",
  surfaceColor: "",
  recentColors: [],
  backgroundImagePath: "",
  backgroundOpacity: 0.18,
  backgroundScope: "workspace",
};

const LEGACY_IMAGE_DATA_URL_PATTERN = /^data:image\/(?:png|jpeg|webp|gif);base64,/i;

export function normalizeThemeCustomization(value: unknown): ThemeCustomization {
  const candidate = value && typeof value === "object" ? (value as Partial<ThemeCustomization>) : {};
  const accentColor = typeof candidate.accentColor === "string" ? parseThemeColor(candidate.accentColor) ?? "" : "";
  const surfaceColor = typeof candidate.surfaceColor === "string" ? parseThemeColor(candidate.surfaceColor) ?? "" : "";
  const recentColors = Array.isArray(candidate.recentColors)
    ? [...new Set(candidate.recentColors.flatMap((color) => typeof color === "string" ? [parseThemeColor(color)] : []))]
      .filter((color): color is string => Boolean(color))
      .slice(0, 3)
    : [];
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
  const backgroundScope = typeof candidate.backgroundScope === "string"
    && THEME_BACKGROUND_SCOPE_IDS.includes(candidate.backgroundScope as ThemeBackgroundScope)
    ? candidate.backgroundScope as ThemeBackgroundScope
    : DEFAULT_THEME_CUSTOMIZATION.backgroundScope;
  return {
    palette: requestedPalette,
    accentColor,
    surfaceColor,
    recentColors,
    backgroundImagePath,
    backgroundOpacity: Number.isFinite(opacity) ? Math.min(1, Math.max(0, opacity)) : DEFAULT_THEME_CUSTOMIZATION.backgroundOpacity,
    backgroundScope,
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
  const hsl = themeColorToHsl(color);
  return hsl ? `${Math.round(hsl.h)} ${Math.round(hsl.s)}% ${Math.round(hsl.l)}%` : null;
}
