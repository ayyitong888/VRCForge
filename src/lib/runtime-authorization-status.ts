import type { ApiConfig } from "./api";

export type RuntimeAuthorizationStatus = {
  source: "external" | "internal" | "both" | "none";
  status: "ok" | "warning" | "error" | "unknown";
  externalState: "connected" | "waiting" | "off" | "unavailable";
  externalConnected: boolean;
  internalConfigured: boolean;
  internalTestPassed: boolean;
  internalKind: "api" | "oauth" | "none";
};

type ComponentStatus = {
  status?: string;
  detail?: unknown;
} | null | undefined;

function detailRecord(detail: unknown): Record<string, unknown> | null {
  return detail && typeof detail === "object" ? detail as Record<string, unknown> : null;
}

/**
 * Resolve the user-facing authorization source from facts already loaded by
 * bootstrap. Gateway readiness and model labels are deliberately ignored:
 * neither proves that an external client has an authenticated session.
 */
export function resolveRuntimeAuthorizationStatus({
  runtimeConnected,
  externalAgentComponent,
  apiConfig,
  providerTestPassed = false,
}: {
  runtimeConnected: boolean;
  externalAgentComponent?: ComponentStatus;
  apiConfig?: Pick<ApiConfig, "configured" | "provider" | "apiKeyPresent"> | null;
  providerTestPassed?: boolean;
}): RuntimeAuthorizationStatus {
  const detail = detailRecord(externalAgentComponent?.detail);
  const externalConnected = runtimeConnected && detail?.connected === true;
  const externalState: RuntimeAuthorizationStatus["externalState"] = externalConnected
    ? "connected"
    : externalAgentComponent?.status === "error"
      ? "unavailable"
      : detail?.gatewayEnabled === true
        ? "waiting"
        : "off";
  const provider = String(apiConfig?.provider || "").trim().toLowerCase();
  const internalConfigured = runtimeConnected
    && apiConfig?.configured === true
    && (provider !== "chatgpt" || apiConfig.apiKeyPresent === true);
  const internalTestPassed = internalConfigured && providerTestPassed === true;
  const internalKind: RuntimeAuthorizationStatus["internalKind"] = internalConfigured
    ? provider === "chatgpt" ? "oauth" : "api"
    : "none";
  const source: RuntimeAuthorizationStatus["source"] = externalConnected && internalTestPassed
    ? "both"
    : externalConnected
      ? "external"
      : internalTestPassed
        ? "internal"
        : "none";

  if (!runtimeConnected) {
    return { source, status: "error", externalState: "unavailable", externalConnected, internalConfigured, internalTestPassed, internalKind };
  }
  if (externalConnected || internalTestPassed) {
    return { source, status: "ok", externalState, externalConnected, internalConfigured, internalTestPassed, internalKind };
  }
  if (externalAgentComponent?.status === "error") {
    return { source, status: "unknown", externalState, externalConnected, internalConfigured, internalTestPassed, internalKind };
  }
  return { source, status: "warning", externalState, externalConnected, internalConfigured, internalTestPassed, internalKind };
}

export function runtimeAuthorizationMessageKey(status: RuntimeAuthorizationStatus): string {
  if (status.source === "external") return "workspace.externalAgentConnected";
  if (status.source === "both") return "workspace.authorizationBoth";
  if (status.source === "internal") {
    return status.internalKind === "oauth"
      ? "workspace.internalOAuthConnected"
      : "workspace.internalApiConfigured";
  }
  if (status.internalConfigured) {
    return status.internalKind === "oauth"
      ? "workspace.internalOAuthNeedsTest"
      : "workspace.internalApiNeedsTest";
  }
  if (status.status === "error") return "workspace.authorizationDisconnected";
  if (status.externalState === "waiting") return "workspace.externalAgentWaiting";
  if (status.externalState === "unavailable") return "workspace.externalAgentUnavailable";
  return "workspace.authorizationNotConfigured";
}

export function runtimeAuthorizationLabelKey(status: RuntimeAuthorizationStatus): string {
  if (status.source === "external") return "workspace.externalAgent";
  if (status.source === "internal") return "workspace.internalAgent";
  if (status.source === "both") return "workspace.authorization";
  if (status.internalConfigured) return "workspace.internalAgent";
  if (status.source === "none") return "workspace.agentConnection";
  return "workspace.externalAgent";
}
