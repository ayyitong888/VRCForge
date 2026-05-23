export type PermissionState = {
  executionMode: "approval" | "roslyn_full_auto";
  perActionApproval: boolean;
  roslynFullAuto: boolean;
  roslynRiskAcknowledged: boolean;
  allowWriteRequests: boolean;
  allowRoslynAdvanced: boolean;
  roslynEnvEnabled: boolean;
};

export type AgentTool = {
  name: string;
  description: string;
  category: string;
  write: boolean;
  advanced: boolean;
  available: boolean;
};

export type AgentManifest = {
  ok: boolean;
  name: string;
  version: string;
  enabled: boolean;
  toolCount: number;
  tools: AgentTool[];
  writeTargets: Array<{ name: string; description: string; riskLevel: string; advanced: boolean }>;
  allowWriteRequests: boolean;
  allowRoslynAdvanced: boolean;
  executionMode: string;
  roslynFullAuto: boolean;
  roslynRiskAcknowledged: boolean;
};

export type HealthComponent = {
  status: "ok" | "warning" | "error" | "unknown";
  message: string;
  detail?: unknown;
};

export type AppBootstrap = {
  ok: boolean;
  app: {
    name: string;
    surface: string;
    browserRequired: boolean;
    legacyDashboardDebugOnly: boolean;
  };
  health: {
    ok: boolean;
    version: string;
    portableMode: boolean;
    components: Record<string, HealthComponent>;
  };
  agentManifest: AgentManifest;
  agentHealth: {
    ok: boolean;
    enabled: boolean;
    pendingApprovalCount: number;
  };
  permission: PermissionState;
  approvals: Array<Record<string, unknown>>;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
  ) {
    super(message);
  }
}

export async function fetchBootstrap(endpoint: string): Promise<AppBootstrap> {
  return requestJson<AppBootstrap>(`${endpoint}/api/app/bootstrap`);
}

export async function updatePermission(
  endpoint: string,
  executionMode: PermissionState["executionMode"],
  acknowledgeRoslynRisk = false,
): Promise<{ ok: boolean; permission: PermissionState }> {
  return requestJson(`${endpoint}/api/app/permission`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      execution_mode: executionMode,
      acknowledge_roslyn_risk: acknowledgeRoslynRisk,
    }),
  });
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = typeof payload === "object" && payload ? (payload as { detail?: unknown }).detail : payload;
    throw new ApiError(typeof detail === "string" ? detail : `HTTP ${response.status}`, response.status, detail);
  }
  return payload as T;
}
