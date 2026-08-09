import type { TFunction } from "i18next";

export function localizeRuntimeHealthMessage(t: TFunction, message?: string | null): string {
  const normalized = (message || "").trim();
  if (!normalized) {
    return "";
  }
  if (normalized === "Backend process is responding.") {
    return t("workspace.backendResponding");
  }
  if (normalized === "Unity MCP bridge online" || normalized === "Unity bridge online" || normalized === "Unity MCP bridge is reachable.") {
    return t("workspace.unityBridgeOnline");
  }
  if (normalized === "Unity MCP bridge is not reachable.") {
    return t("workspace.unityBridgeNotReachable");
  }
  if (normalized === "Unity MCP is connected, but VRCForge Unity tools are missing or incomplete.") {
    return t("workspace.unityToolsMissing");
  }
  return normalized;
}
