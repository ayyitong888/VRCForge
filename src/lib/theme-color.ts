export type ThemeColorFormat = "hex" | "rgb" | "hsl";

export type ThemeHslColor = {
  h: number;
  s: number;
  l: number;
};

type ThemeRgbColor = {
  r: number;
  g: number;
  b: number;
};

const HEX_COLOR_PATTERN = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i;

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));

const componentToHex = (value: number) => Math.round(clamp(value, 0, 255)).toString(16).padStart(2, "0");

const rgbToHex = ({ r, g, b }: ThemeRgbColor) => `#${componentToHex(r)}${componentToHex(g)}${componentToHex(b)}`;

function parseHexColor(value: string): string | null {
  const match = value.trim().match(HEX_COLOR_PATTERN);
  if (!match) return null;
  const digits = match[1].toLowerCase();
  return digits.length === 3
    ? `#${digits.split("").map((digit) => `${digit}${digit}`).join("")}`
    : `#${digits}`;
}

function parseNumberList(value: string, functionName: string): string[] | null {
  const trimmed = value.trim().toLowerCase();
  const functional = trimmed.match(new RegExp(`^${functionName}\\((.*)\\)$`, "i"));
  const body = functional ? functional[1] : trimmed;
  if (body.includes("/")) return null;
  const parts = body.replace(/,/g, " ").trim().split(/\s+/).filter(Boolean);
  return parts.length === 3 ? parts : null;
}

function parseRgbColor(value: string): ThemeRgbColor | null {
  if (value.includes("%")) return null;
  const parts = parseNumberList(value, "rgb");
  if (!parts) return null;
  const [r, g, b] = parts.map(Number);
  if (![r, g, b].every((component) => Number.isFinite(component) && component >= 0 && component <= 255)) {
    return null;
  }
  return { r: Math.round(r), g: Math.round(g), b: Math.round(b) };
}

function hslToRgb({ h, s, l }: ThemeHslColor): ThemeRgbColor {
  const hue = ((h % 360) + 360) % 360;
  const saturation = clamp(s, 0, 100) / 100;
  const lightness = clamp(l, 0, 100) / 100;
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const segment = hue / 60;
  const secondary = chroma * (1 - Math.abs((segment % 2) - 1));
  let red = 0;
  let green = 0;
  let blue = 0;
  if (segment < 1) [red, green] = [chroma, secondary];
  else if (segment < 2) [red, green] = [secondary, chroma];
  else if (segment < 3) [green, blue] = [chroma, secondary];
  else if (segment < 4) [green, blue] = [secondary, chroma];
  else if (segment < 5) [red, blue] = [secondary, chroma];
  else [red, blue] = [chroma, secondary];
  const match = lightness - chroma / 2;
  return { r: (red + match) * 255, g: (green + match) * 255, b: (blue + match) * 255 };
}

function parseHslColor(value: string): ThemeHslColor | null {
  const parts = parseNumberList(value, "hsl");
  if (!parts || !parts[1].endsWith("%") || !parts[2].endsWith("%")) return null;
  const h = Number(parts[0].replace(/deg$/i, ""));
  const s = Number(parts[1].slice(0, -1));
  const l = Number(parts[2].slice(0, -1));
  if (!Number.isFinite(h) || !Number.isFinite(s) || !Number.isFinite(l) || s < 0 || s > 100 || l < 0 || l > 100) {
    return null;
  }
  return { h: ((h % 360) + 360) % 360, s, l };
}

function hexToRgb(value: string): ThemeRgbColor | null {
  const normalized = parseHexColor(value);
  if (!normalized) return null;
  return {
    r: Number.parseInt(normalized.slice(1, 3), 16),
    g: Number.parseInt(normalized.slice(3, 5), 16),
    b: Number.parseInt(normalized.slice(5, 7), 16),
  };
}

export function themeColorToHsl(value: string): ThemeHslColor | null {
  const rgb = hexToRgb(value);
  if (!rgb) return null;
  const red = rgb.r / 255;
  const green = rgb.g / 255;
  const blue = rgb.b / 255;
  const maximum = Math.max(red, green, blue);
  const minimum = Math.min(red, green, blue);
  const delta = maximum - minimum;
  let hue = 0;
  if (delta > 0) {
    if (maximum === red) hue = ((green - blue) / delta) % 6;
    else if (maximum === green) hue = (blue - red) / delta + 2;
    else hue = (red - green) / delta + 4;
    hue *= 60;
    if (hue < 0) hue += 360;
  }
  const lightness = (maximum + minimum) / 2;
  const saturation = delta === 0 ? 0 : delta / (1 - Math.abs(2 * lightness - 1));
  return { h: hue, s: saturation * 100, l: lightness * 100 };
}

export function parseThemeColor(value: string): string | null {
  const hex = parseHexColor(value);
  if (hex) return hex;
  const normalized = value.trim().toLowerCase();
  if (normalized.startsWith("hsl") || normalized.includes("%")) {
    const hsl = parseHslColor(normalized);
    return hsl ? rgbToHex(hslToRgb(hsl)) : null;
  }
  const rgb = parseRgbColor(normalized);
  return rgb ? rgbToHex(rgb) : null;
}

const rounded = (value: number) => {
  const result = Math.round(value * 10) / 10;
  return Number.isInteger(result) ? String(result) : result.toFixed(1);
};

export function formatThemeColor(value: string, format: ThemeColorFormat): string {
  const normalized = parseHexColor(value) ?? "#000000";
  if (format === "hex") return normalized.toUpperCase();
  const rgb = hexToRgb(normalized) ?? { r: 0, g: 0, b: 0 };
  if (format === "rgb") return `rgb(${rgb.r}, ${rgb.g}, ${rgb.b})`;
  const hsl = themeColorToHsl(normalized) ?? { h: 0, s: 0, l: 0 };
  return `hsl(${rounded(hsl.h)} ${rounded(hsl.s)}% ${rounded(hsl.l)}%)`;
}

function relativeLuminance({ r, g, b }: ThemeRgbColor): number {
  const channels = [r, g, b].map((value) => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

export function readableForegroundForHsl(color: ThemeHslColor): string {
  const luminance = relativeLuminance(hslToRgb(color));
  const whiteContrast = 1.05 / (luminance + 0.05);
  const darkContrast = (luminance + 0.05) / 0.05;
  return whiteContrast >= darkContrast ? "0 0% 100%" : "0 0% 8%";
}
