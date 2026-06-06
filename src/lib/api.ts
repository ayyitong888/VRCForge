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

export type ApiConfig = {
  provider: string;
  providerLabel?: string;
  api_key?: string;
  apiKeyPresent: boolean;
  base_url?: string;
  model?: string;
  usesBaseUrl?: boolean;
  authHeader?: string;
  apiKeyRequired: boolean;
};

export type AgentApproval = {
  id: string;
  status: string;
  targetTool?: string;
  riskLevel?: string;
  reason?: string;
  createdAt?: string;
  preview?: {
    command?: string;
    cwd?: string;
    workspaceRoot?: string;
    riskReasons?: string[];
  };
};

export type AgentShellResult = {
  ok: boolean;
  command: string;
  cwd: string;
  exitCode: number;
  timedOut: boolean;
  durationSeconds: number;
  stdout: string;
  stderr: string;
  stdoutTruncated?: boolean;
  stderrTruncated?: boolean;
};

export type AgentSkillResult = {
  ok: boolean;
  status: "executed" | "failed" | "blocked" | string;
  tool: string;
  category?: string;
  write?: boolean;
  advanced?: boolean;
  summary?: string;
  paramsSummary?: Record<string, unknown>;
  result?: unknown;
  error?: string;
};

export type AgentRuntimeResponse = {
  ok: boolean;
  session_id: string;
  sessionId: string;
  turn_id: string;
  turnId: string;
  observe: Record<string, unknown>;
  plan: {
    summary: string;
    planner: string;
    shellNeeded: boolean;
    shellCommand?: string;
    skillNeeded?: boolean;
    skillTool?: string;
    skillCategory?: string;
    skillParams?: Record<string, unknown>;
    skillReason?: string;
    expectedResult?: string;
    nextStep?: string;
  };
  shell?: {
    ok: boolean;
    status: "executed" | "pending_approval" | "rejected" | string;
    classification?: {
      risk: "low" | "high" | "reject" | string;
      reasons: string[];
      command: string;
      cwd: string;
    };
    approval?: AgentApproval;
    approval_id?: string;
    approvalId?: string;
    result?: AgentShellResult;
    error?: string;
  };
  skill?: AgentSkillResult;
  result?: AgentShellResult;
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
    projectRoot?: string;
    projects?: {
      selectedProjectPath?: string;
      projects?: Array<{ name?: string; path?: string; editorVersion?: string; unityVersion?: string; sources?: string[] }>;
    };
  };
  agentManifest: AgentManifest;
  apiConfig?: ApiConfig;
  agentHealth: {
    ok: boolean;
    enabled: boolean;
    pendingApprovalCount: number;
  };
  permission: PermissionState;
  approvals: AgentApproval[];
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

export async function updateApiConfig(endpoint: string, config: { provider: string; api_key: string; base_url?: string; model?: string }) {
  return requestJson<{ ok?: boolean; apiConfig: ApiConfig }>(`${endpoint}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function sendAgentMessage(
  endpoint: string,
  message: string,
  sessionId?: string,
): Promise<AgentRuntimeResponse> {
  return requestJson(`${endpoint}/api/app/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId || null,
      message,
    }),
  });
}

export async function approveAgentApproval(
  endpoint: string,
  approvalId: string,
): Promise<{ ok: boolean; approval?: AgentApproval; execution?: { status?: string; result?: AgentShellResult; error?: string } }> {
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/approve`, {
    method: "POST",
  });
}

export async function rejectAgentApproval(
  endpoint: string,
  approvalId: string,
): Promise<{ ok: boolean; approval?: AgentApproval; message?: string }> {
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/reject`, {
    method: "POST",
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
