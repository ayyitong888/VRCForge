export type ThemeCustomization = {
  accentColor: string;
  backgroundImageDataUrl: string;
  backgroundOpacity: number;
};

export const THEME_CUSTOMIZATION_STORAGE_KEY = "vrcforge_theme_customization";
export const MAX_THEME_BACKGROUND_BYTES = 2 * 1024 * 1024;

export const DEFAULT_THEME_CUSTOMIZATION: ThemeCustomization = {
  accentColor: "",
  backgroundImageDataUrl: "",
  backgroundOpacity: 0.18,
};

const HEX_COLOR_PATTERN = /^#[0-9a-f]{6}$/i;
const IMAGE_DATA_URL_PATTERN = /^data:image\/(?:png|jpeg|webp|gif);base64,/i;

export function normalizeThemeCustomization(value: unknown): ThemeCustomization {
  const candidate = value && typeof value === "object" ? (value as Partial<ThemeCustomization>) : {};
  const accentColor = typeof candidate.accentColor === "string" && HEX_COLOR_PATTERN.test(candidate.accentColor)
    ? candidate.accentColor.toLowerCase()
    : "";
  const backgroundImageDataUrl =
    typeof candidate.backgroundImageDataUrl === "string"
    && candidate.backgroundImageDataUrl.length <= MAX_THEME_BACKGROUND_BYTES * 1.5
    && IMAGE_DATA_URL_PATTERN.test(candidate.backgroundImageDataUrl)
      ? candidate.backgroundImageDataUrl
      : "";
  const opacity = Number(candidate.backgroundOpacity);
  return {
    accentColor,
    backgroundImageDataUrl,
    backgroundOpacity: Number.isFinite(opacity) ? Math.min(0.5, Math.max(0.06, opacity)) : DEFAULT_THEME_CUSTOMIZATION.backgroundOpacity,
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
