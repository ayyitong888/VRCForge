import type { TFunction } from "i18next";

export function localizeRuntimeHealthMessage(t: TFunction, message?: string | null): string {
  const normalized = (message || "").trim();
  if (!normalized) {
    return "";
  }
  if (normalized === "Backend process is responding.") {
    return t("workspace.backendResponding");
  }
  if (normalized === "VRCForge MCP Core is bundled with the plugin.") {
    return t("workspace.unityPluginReady");
  }
  if (normalized === "VRCForge MCP Core status is refreshing.") {
    return t("workspace.unityPluginChecking");
  }
  if (normalized === "VRCForge MCP Core is missing from the plugin install.") {
    return t("workspace.unityPluginMissing");
  }
  if (normalized === "Unity MCP bridge online" || normalized === "Unity bridge online" || normalized === "Unity MCP bridge is reachable.") {
    return t("workspace.unityBridgeOnline");
  }
  if (normalized === "Unity MCP bridge is not reachable.") {
    return t("workspace.unityBridgeNotReachable");
  }
  if (normalized === "Unity MCP bridge status is refreshing.") {
    return t("workspace.unityConnectionChecking");
  }
  if (normalized === "Unity MCP is connected, but VRCForge Unity tools are missing or incomplete.") {
    return t("workspace.unityToolsMissing");
  }
  if (normalized === "Unity instance is registered with MCP.") {
    return t("workspace.unityProjectOpen");
  }
  if (normalized === "MCP server is reachable, but no Unity instance is registered.") {
    return t("workspace.unityProjectNotOpen");
  }
  if (normalized === "Unity instance status is refreshing.") {
    return t("workspace.unityProjectChecking");
  }
  if (normalized === "VRCForge Unity tools are registered.") {
    return t("workspace.toolsReady");
  }
  if (normalized === "VRCForge Unity tool status is refreshing.") {
    return t("workspace.toolsChecking");
  }
  if (normalized === "An authenticated external Agent is connected.") {
    return t("workspace.externalAgentConnected");
  }
  if (normalized === "External Agent access is ready; waiting for a connection.") {
    return t("workspace.externalAgentWaiting");
  }
  if (normalized === "External Agent access is off.") {
    return t("workspace.externalAgentOff");
  }
  if (normalized === "External Agent connection status is unavailable.") {
    return t("workspace.externalAgentUnavailable");
  }
  return normalized;
}
