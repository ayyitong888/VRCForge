import i18n from "../i18n";
import type { ExternalAgentConnectorClient, ExternalAgentConnectorStatus } from "./api";

export const CONNECTOR_CLIENT_LABELS: Record<ExternalAgentConnectorClient, string> = {
  codexApp: "Codex App",
  codexCli: "Codex CLI",
  claudeCode: "Claude Code CLI",
  claudeCowork: "Claude Cowork App",
  generic: "Generic MCP client",
};

export function normalizeConnectorClient(client?: string): ExternalAgentConnectorClient | "" {
  if (client === "codex") {
    return "codexApp";
  }
  return client === "codexApp" || client === "codexCli" || client === "claudeCode" || client === "claudeCowork" || client === "generic"
    ? client
    : "";
}

export function formatConnectorActionMessage(client: ExternalAgentConnectorClient, action?: ExternalAgentConnectorStatus["lastConnectorAction"]) {
  const label = CONNECTOR_CLIENT_LABELS[client] || client;
  if (!action) {
    return `${label} updated`;
  }
  const verb = action.action === "uninstall" ? "removed" : "installed";
  if (!action.ok) {
    return `${label} ${action.action || "action"} failed: ${action.error || action.stage || "see details"}`;
  }
  if (action.action === "install") {
    const toolCount = action.handshake?.toolCount;
    const ready = action.handshake?.ready ? i18n.t("connector.ready") : action.handshake?.connected ? i18n.t("connector.connected") : "checked";
    return `${label} installed; ${ready}${toolCount !== undefined ? `, ${toolCount} tools` : ""}`;
  }
  return `${label} ${verb}`;
}
