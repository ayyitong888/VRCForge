export type ExecutionMode = "approval" | "auto" | "roslyn_full_auto";

export type PermissionState = {
  executionMode: ExecutionMode;
  perActionApproval: boolean;
  autoApprove?: boolean;
  roslynFullAuto: boolean;
  roslynRiskAcknowledged: boolean;
  allowWriteRequests: boolean;
  allowRoslynAdvanced: boolean;
  roslynEnvEnabled: boolean;
};

export type AgentNotes = {
  ok: boolean;
  path: string;
  exists: boolean;
  content: string;
};

export type AgentTool = {
  name: string;
  description: string;
  category: string;
  write: boolean;
  advanced: boolean;
  available: boolean;
};

export type AgentSkill = {
  schema?: string;
  name: string;
  title: string;
  description?: string;
  category?: string;
  source: "builtin" | "user" | string;
  enabled: boolean;
  available: boolean;
  permissionMode: string;
  riskLevel?: string;
  whenToUse?: string;
  inputs?: string[];
  outputs?: string[];
  sideEffects?: string;
  backupRestore?: string;
  tools?: string[];
  allowedTools?: string[];
  disallowedTools?: string[];
  entrypointTool?: string;
  userInvocable?: boolean;
  disableModelInvocation?: boolean;
  argumentHint?: string;
  requiresEnv?: string[];
  requiresBinaries?: string[];
  supportedOs?: string[];
  supportFiles?: string[];
  testCommand?: string;
  instructions?: string;
  advanced?: boolean;
  write?: boolean;
  tags?: string[];
  storagePath?: string;
  skillType?: string;
  validation?: { status?: "ok" | "warning" | "error" | string; reasons?: string[] };
  availabilityReasons?: string[];
};

export type AgentSkillRegistry = {
  ok: boolean;
  schema: string;
  skills: AgentSkill[];
  count: number;
  availableCount: number;
  builtinCount: number;
  userCount: number;
  warningCount?: number;
  errorCount?: number;
};

export type AgentSkillCheck = {
  ok: boolean;
  schema: string;
  count: number;
  errorCount: number;
  warningCount: number;
  checks: Array<{
    name: string;
    title?: string;
    source?: string;
    skillType?: string;
    status: "ok" | "warning" | "error" | string;
    reasons?: string[];
    available?: boolean;
  }>;
};

export type AgentManifest = {
  ok: boolean;
  name: string;
  version: string;
  enabled: boolean;
  toolCount: number;
  tools: AgentTool[];
  skills: AgentSkill[];
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
    reply?: string;
    planner: string;
    plannerLabel?: string;
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

export type ProviderModelList = {
  provider: string;
  providerLabel?: string;
  baseUrl?: string;
  models: Array<{ id: string; label: string }>;
  modelCount: number;
  selectedModel?: string;
};

export async function fetchProviderModels(
  endpoint: string,
  config: { provider: string; api_key?: string; base_url?: string; model?: string },
): Promise<ProviderModelList> {
  return requestJson(`${endpoint}/api/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function fetchAgentNotes(endpoint: string): Promise<AgentNotes> {
  return requestJson<AgentNotes>(`${endpoint}/api/app/agent-notes`);
}

export async function saveAgentNotes(endpoint: string, content: string): Promise<{ ok: boolean; path: string; bytes: number }> {
  return requestJson(`${endpoint}/api/app/agent-notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export type StoredChats<T> = {
  ok: boolean;
  path: string;
  exists: boolean;
  chats: T[];
  count: number;
};

export async function fetchChats<T>(endpoint: string): Promise<StoredChats<T>> {
  return requestJson<StoredChats<T>>(`${endpoint}/api/app/chats`);
}

export async function saveChats<T>(endpoint: string, chats: T[]): Promise<{ ok: boolean; path: string; count: number }> {
  return requestJson(`${endpoint}/api/app/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chats }),
  });
}

export type ProjectPrefs = {
  customPaths: string[];
  hiddenPaths: string[];
};

export async function fetchProjectPrefs(endpoint: string): Promise<ProjectPrefs> {
  const payload = await requestJson<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>(
    `${endpoint}/api/app/projects/prefs`,
  );
  return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
}

export async function saveProjectPrefs(endpoint: string, prefs: ProjectPrefs): Promise<ProjectPrefs> {
  const payload = await requestJson<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>(
    `${endpoint}/api/app/projects/prefs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customPaths: prefs.customPaths, hiddenPaths: prefs.hiddenPaths }),
    },
  );
  return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
}

export async function fetchSkills(endpoint: string): Promise<AgentSkillRegistry> {
  return requestJson<AgentSkillRegistry>(`${endpoint}/api/app/skills`);
}

export async function checkSkills(endpoint: string): Promise<AgentSkillCheck> {
  return requestJson<AgentSkillCheck>(`${endpoint}/api/app/skills/check`);
}

export async function createSkill(endpoint: string, skill: Partial<AgentSkill>): Promise<AgentSkillRegistry & { skill: AgentSkill }> {
  return requestJson(`${endpoint}/api/app/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
}

export async function updateSkill(endpoint: string, skillId: string, skill: Partial<AgentSkill>): Promise<AgentSkillRegistry & { skill: AgentSkill }> {
  return requestJson(`${endpoint}/api/app/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
}

export async function deleteSkill(endpoint: string, skillId: string): Promise<AgentSkillRegistry> {
  return requestJson(`${endpoint}/api/app/skills/${encodeURIComponent(skillId)}`, {
    method: "DELETE",
  });
}

export type ChatHistoryEntry = {
  role: "user" | "agent";
  text: string;
};

export async function sendAgentMessage(
  endpoint: string,
  message: string,
  sessionId?: string,
  history?: ChatHistoryEntry[],
): Promise<AgentRuntimeResponse> {
  return requestJson(`${endpoint}/api/app/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId || null,
      message,
      history: history ?? [],
    }),
  });
}

export async function compactAgentHistory(
  endpoint: string,
  history: ChatHistoryEntry[],
): Promise<{ ok: boolean; summary: string; provider?: string; model?: string; entryCount?: number }> {
  return requestJson(`${endpoint}/api/app/agent/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ history }),
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
