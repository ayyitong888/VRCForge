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
