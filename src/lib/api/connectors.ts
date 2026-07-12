import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type ExternalAgentConnectorStatus = {
  ok: boolean;
  schema: string;
  mcp: {
    serverName: string;
    transport: string;
    url: string;
    loopbackOnly: boolean;
  };
  auth: {
    type: string;
    header: string;
    tokenEnvVar: string;
    headerTemplate: string;
    storesPlaintextToken: boolean;
  };
  gateway: {
    enabled: boolean;
    requiresToken: boolean;
    allowWriteRequests: boolean;
    tokenConfigured: boolean;
    approvalTokenConfigured: boolean;
    configPath?: string;
    mcpUrl?: string;
    restUrl?: string;
    pendingApprovalCount?: number;
    checkpointArchiveMaxSizeMb?: number;
    checkpointArchiveUsage?: {
      ok?: boolean;
      schema?: string;
      directory?: string;
      defaultDirectory?: string;
      relocated?: boolean;
      sizeBytes?: number;
      sizeMb?: number;
      archiveCount?: number;
      protectedCount?: number;
      maxSizeMb?: number;
      archives?: Array<{
        checkpointId?: string;
        path?: string;
        sizeBytes?: number;
        sizeMb?: number;
        modifiedAt?: number;
        protected?: boolean;
        label?: string;
      }>;
    };
    checkpointArchivePrune?: {
      ok?: boolean;
      schema?: string;
      directory?: string;
      maxSizeMb?: number;
      limitEnabled?: boolean;
      initialBytes?: number;
      remainingBytes?: number;
      remainingMb?: number;
      archiveCount?: number;
      deletedCount?: number;
      deletedBytes?: number;
      protectedCount?: number;
    };
    checkpointArchiveDelete?: {
      ok?: boolean;
      error?: string;
      directory?: string;
      requestedCount?: number;
      deletedCount?: number;
      deletedBytes?: number;
      protectedSkipped?: string[];
      archiveCount?: number;
    };
    checkpointArchiveRelocate?: {
      ok?: boolean;
      code?: string;
      error?: string;
      directory?: string;
      from?: string;
      to?: string;
      unchanged?: boolean;
      copiedCount?: number;
      rewrittenCount?: number;
      removedOldCount?: number;
      archiveCount?: number;
    };
  };
  clients?: Record<
    "codexApp" | "codexCli" | "claudeCode" | "claudeCowork" | "generic",
    {
      label?: string;
      scope?: "user" | "project" | string;
      configPath?: string;
      requestedConfigPath?: string;
      installed?: boolean;
      conflict?: boolean;
      installable?: boolean;
      requiresConfigPath?: boolean;
      lastError?: string;
      sharedConfigGroup?: string;
      cliDetected?: boolean | null;
      cliPath?: string;
      cliSource?: string;
      cliError?: string;
      appDetected?: boolean | null;
      appMatches?: string[];
      appError?: string;
      bridge?: unknown;
      restartInstruction?: string;
    }
  >;
  clientConfigs: {
    codex?: { format: string; text: string; config?: unknown };
    codexStdio?: { format: string; text: string; config?: unknown; transport?: string };
    claudeCode?: { format: string; text: string; config?: unknown };
    claudeCodeStdio?: { format: string; text: string; config?: unknown; transport?: string };
    claudeCowork?: { format: string; text: string; config?: unknown; transport?: string };
    generic?: { format: string; text: string; config?: unknown; transport?: string };
    genericHttp?: { format: string; text: string; config?: unknown; transport?: string };
  };
  launcher?: {
    stdioBridge?: {
      command?: string;
      args?: string[];
      cwd?: string;
      startsOrReconnectsRuntime?: boolean;
      readsGatewayTokenFromLocalConfig?: boolean;
      storesPlaintextToken?: boolean;
    };
    httpPreflight?: {
      url?: string;
      tokenEnvVar?: string;
      requiresRuntimeAlreadyOnline?: boolean;
    };
    smoke?: {
      command?: string;
      args?: string[];
      preflightArgs?: string[];
      liveWriteRollbackArgs?: string[];
    };
  };
  skillsProjection?: {
    recommendedDirectory?: string;
    layout?: string;
    projectionMode?: string;
    secretPolicy?: string;
  };
  advertisedTools?: Array<{ name?: string; category?: string; write?: boolean }>;
  writeTargets?: Array<{ name?: string; riskLevel?: string; advanced?: boolean }>;
  lastCalls?: Array<{ event?: string; createdAt?: string; agentName?: string; targetTool?: string; status?: string; riskLevel?: string }>;
  lastConnectorAction?: ExternalAgentConnectorActionResult;
};

export type ExternalAgentConnectorClient = "codexApp" | "codexCli" | "claudeCode" | "claudeCowork" | "generic";

export type ExternalAgentConnectorActionResult = {
  ok: boolean;
  client?: string;
  action?: "install" | "uninstall" | string;
  stage?: string;
  configPath?: string;
  backupPath?: string;
  changed?: boolean;
  installed?: boolean;
  removed?: boolean;
  restartRequired?: boolean;
  restartInstruction?: string;
  bridge?: unknown;
  handshake?: {
    ok?: boolean;
    connected?: boolean;
    ready?: boolean;
    stage?: string;
    toolCount?: number;
    toolsSample?: string[];
    hasBridgePreflight?: boolean;
    hasRequestApply?: boolean;
    stderrTail?: string[];
    error?: string;
    warning?: string;
    suggestion?: string;
  };
  error?: string;
  suggestion?: string;
};

export async function fetchExternalAgentConnectors(
  endpoint: string,
  projectPath?: string,
  configPath?: string,
): Promise<ExternalAgentConnectorStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ExternalAgentConnectorStatus>("fetch_external_agent_connectors", {
      request: { projectPath: projectPath || undefined, configPath: configPath || undefined, timeoutMs: 30000 },
    });
  }
  const queryParams = new URLSearchParams();
  if (projectPath) queryParams.set("projectPath", projectPath);
  if (configPath) queryParams.set("configPath", configPath);
  const query = queryParams.size ? `?${queryParams.toString()}` : "";
  return requestJson<ExternalAgentConnectorStatus>(`${endpoint}/api/app/external-agent/connectors${query}`);
}

export async function updateExternalAgentGateway(
  endpoint: string,
  request: {
    enabled?: boolean;
    allowWriteRequests?: boolean;
    revokeToken?: boolean;
    checkpointArchiveMaxSizeMb?: number;
    deleteCheckpointArchiveIds?: string[];
    checkpointArchiveDirectory?: string;
  },
): Promise<ExternalAgentConnectorStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ExternalAgentConnectorStatus>("update_external_agent_gateway", {
      request: { ...request, timeoutMs: 60000 },
    });
  }
  return requestJson<ExternalAgentConnectorStatus>(`${endpoint}/api/app/external-agent/gateway`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function installExternalAgentConnector(
  endpoint: string,
  request: { client: ExternalAgentConnectorClient; projectPath?: string; configPath?: string },
): Promise<ExternalAgentConnectorStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ExternalAgentConnectorStatus>("install_external_agent_connector", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<ExternalAgentConnectorStatus>(`${endpoint}/api/app/external-agent/connectors/install`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function uninstallExternalAgentConnector(
  endpoint: string,
  request: { client: ExternalAgentConnectorClient; projectPath?: string; configPath?: string },
): Promise<ExternalAgentConnectorStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ExternalAgentConnectorStatus>("uninstall_external_agent_connector", {
      request: { ...request, timeoutMs: 60000 },
    });
  }
  return requestJson<ExternalAgentConnectorStatus>(`${endpoint}/api/app/external-agent/connectors/uninstall`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
