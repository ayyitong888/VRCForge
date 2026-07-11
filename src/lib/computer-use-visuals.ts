import type { CSSProperties } from "react";
import type { ThemeMode } from "./app-preferences";

export type ComputerUseVisualPhase = "starting" | "running" | "stopping";

type ComputerUseVisualProperties = CSSProperties & Record<`--computer-use-${string}`, string>;

type ComputerUseVisualTokens = {
  palette: string;
  style: ComputerUseVisualProperties;
};

const PHASE_TOKENS: Record<
  ComputerUseVisualPhase,
  {
    glowOpacity: string;
    edgeOpacity: string;
    pulseDuration: string;
    shimmerOpacity: string;
    shimmerDuration: string;
    motionPlay: string;
  }
> = {
  starting: {
    glowOpacity: "0.2",
    edgeOpacity: "0",
    pulseDuration: "2.2s",
    shimmerOpacity: "0.85",
    shimmerDuration: "1.8s",
    motionPlay: "running",
  },
  running: {
    glowOpacity: "0.15",
    edgeOpacity: "0",
    pulseDuration: "2.6s",
    shimmerOpacity: "0.6",
    shimmerDuration: "2.6s",
    motionPlay: "running",
  },
  stopping: {
    glowOpacity: "0.07",
    edgeOpacity: "0",
    pulseDuration: "3.2s",
    shimmerOpacity: "0.18",
    shimmerDuration: "2.6s",
    motionPlay: "paused",
  },
};

/**
 * Resolves the app theme accent (`--primary`, "H S% L%") to a `#rrggbb` hex
 * string so it can travel to the native overlay as an independent parameter.
 * Returns "" when the token is missing or unparsable; the native side then
 * falls back to its built-in light/dark palette accents.
 */
export function resolveComputerUseAccentHex(): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return "";
  }
  let raw = "";
  try {
    raw = getComputedStyle(document.documentElement).getPropertyValue("--primary").trim();
  } catch {
    return "";
  }
  const match = raw.match(/^([\d.]+)(?:deg)?[,\s]+([\d.]+)%[,\s]+([\d.]+)%$/);
  if (!match) {
    return "";
  }
  const hue = Number(match[1]);
  const saturation = Number(match[2]) / 100;
  const lightness = Number(match[3]) / 100;
  if (!Number.isFinite(hue) || !Number.isFinite(saturation) || !Number.isFinite(lightness)) {
    return "";
  }
  const chroma = (1 - Math.abs(2 * lightness - 1)) * Math.max(0, Math.min(saturation, 1));
  const huePrime = (((hue % 360) + 360) % 360) / 60;
  const secondary = chroma * (1 - Math.abs((huePrime % 2) - 1));
  let channels: [number, number, number];
  if (huePrime < 1) channels = [chroma, secondary, 0];
  else if (huePrime < 2) channels = [secondary, chroma, 0];
  else if (huePrime < 3) channels = [0, chroma, secondary];
  else if (huePrime < 4) channels = [0, secondary, chroma];
  else if (huePrime < 5) channels = [secondary, 0, chroma];
  else channels = [chroma, 0, secondary];
  const base = Math.max(0, Math.min(lightness, 1)) - chroma / 2;
  const toHex = (value: number) =>
    Math.max(0, Math.min(255, Math.round((value + base) * 255)))
      .toString(16)
      .padStart(2, "0");
  return `#${toHex(channels[0])}${toHex(channels[1])}${toHex(channels[2])}`;
}

/**
 * Maps Computer Use state onto semantic app theme colors. Future color themes
 * only need to provide the standard theme tokens or add a palette resolver here.
 */
export function resolveComputerUseVisualTokens(
  theme: ThemeMode,
  phase: ComputerUseVisualPhase,
): ComputerUseVisualTokens {
  const phaseTokens = PHASE_TOKENS[phase];
  const restOpacity = (Number(phaseTokens.glowOpacity) * 0.68).toFixed(2);
  return {
    palette: `semantic-${theme}`,
    style: {
      "--computer-use-accent": "hsl(var(--primary))",
      "--computer-use-on-accent": "hsl(var(--primary-foreground))",
      "--computer-use-surface": `hsl(var(--card) / ${theme === "dark" ? "0.82" : "0.86"})`,
      "--computer-use-surface-muted": `hsl(var(--primary) / ${theme === "dark" ? "0.16" : "0.09"})`,
      "--computer-use-outline": "transparent",
      "--computer-use-glow-opacity": phaseTokens.glowOpacity,
      "--computer-use-glow-rest-opacity": restOpacity,
      "--computer-use-edge-opacity": phaseTokens.edgeOpacity,
      "--computer-use-pulse-duration": phaseTokens.pulseDuration,
      "--computer-use-shimmer-opacity": phaseTokens.shimmerOpacity,
      "--computer-use-shimmer-duration": phaseTokens.shimmerDuration,
      "--computer-use-motion-play": phaseTokens.motionPlay,
    },
  };
}
