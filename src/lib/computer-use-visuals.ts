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
  { glowOpacity: string; edgeOpacity: string; pulseDuration: string }
> = {
  starting: { glowOpacity: "0.18", edgeOpacity: "0", pulseDuration: "2.4s" },
  running: { glowOpacity: "0.14", edgeOpacity: "0", pulseDuration: "2.1s" },
  stopping: { glowOpacity: "0.08", edgeOpacity: "0", pulseDuration: "2.8s" },
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
      "--computer-use-surface": `hsl(var(--card) / ${theme === "dark" ? "0.94" : "0.97"})`,
      "--computer-use-surface-muted": `hsl(var(--primary) / ${theme === "dark" ? "0.16" : "0.09"})`,
      "--computer-use-outline": "transparent",
      "--computer-use-glow-opacity": phaseTokens.glowOpacity,
      "--computer-use-glow-rest-opacity": restOpacity,
      "--computer-use-edge-opacity": phaseTokens.edgeOpacity,
      "--computer-use-pulse-duration": phaseTokens.pulseDuration,
    },
  };
}
