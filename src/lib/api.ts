import { invoke } from "@tauri-apps/api/core";
import { isDesktopLoopbackApiUrl } from "./desktop-routing";

export type ExecutionMode = "approval" | "auto" | "roslyn_full_auto";

export type PermissionState = {
  executionMode: ExecutionMode;
  perActionApproval: boolean;
  autoApprove?: boolean;
  autoApproveDangerousRequiresApproval?: boolean;
  roslynFullAuto: boolean;
  fullPermission?: boolean;
  permissionLabel?: string;
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
  fullPermission?: boolean;
  permissionLabel?: string;
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

export type VisionConfig = {
  provider: string;
  providerLabel?: string;
  api_key?: string;
  apiKeyPresent: boolean;
  base_url?: string;
  model?: string;
  enabled: boolean;
  configured: boolean;
  apiKeyRequired: boolean;
};

export type ProviderModelInfo = {
  id: string;
  label: string;
  contextWindow?: number;
  inputTokenLimit?: number;
  maxInputTokens?: number;
  outputTokenLimit?: number;
  maxOutputTokens?: number;
};

export type DiagnosticsStatus = {
  ok: boolean;
  schema: string;
  debugLogging: boolean;
  configPath?: string;
  logsDir?: string;
  dashboardLogPath?: string;
  interactionLogPath?: string;
  supportBundleDir?: string;
  logRetentionHours?: number;
};

export type SupportBundleResult = {
  ok: boolean;
  schema: string;
  bundlePath: string;
  bundleUrl?: string;
  bytes: number;
  debugLogging: boolean;
  redacted: boolean;
};

export type AgentApproval = {
  id: string;
  status: string;
  targetTool?: string;
  riskLevel?: string;
  reason?: string;
  createdAt?: string;
  arguments?: Record<string, unknown>;
  paramsSummary?: Record<string, unknown>;
  preview?: {
    command?: string;
    cwd?: string;
    workspaceRoot?: string;
    riskReasons?: string[];
  } & Record<string, unknown>;
  checkpoint?: AgentCheckpoint;
};

export type AgentCheckpoint = {
  id: string;
  createdAt?: string;
  approvalId?: string;
  targetTool?: string;
  status?: string;
  ok?: boolean;
  error?: string;
  projectRoot?: string;
  gitRoot?: string;
  checkpointRef?: string;
  baseCommit?: string;
  createdCommit?: boolean;
  pathspecs?: string[];
  statusBefore?: string[];
};

export type AgentCheckpointPreview = {
  ok: boolean;
  checkpoint?: AgentCheckpoint;
  changedFiles?: string[];
  workingTreeStatus?: string[];
  error?: string;
};

export type WorkspaceDiffFile = {
  status: string;
  path: string;
  raw: string;
  additions?: number;
  deletions?: number;
  binary?: boolean;
};

export type WorkspaceDiffSummary = {
  ok: boolean;
  schema: string;
  requestedRoot?: string;
  gitRoot?: string;
  branch?: string;
  status: "changed" | "clean" | "not_git" | "missing" | "error" | string;
  fileCount: number;
  additions: number;
  deletions: number;
  files: WorkspaceDiffFile[];
  statusLines: string[];
  shortstat?: string;
  patch?: string;
  patchTruncated?: boolean;
  error?: string;
  fallbackFromProjectRoot?: string;
};

export type AgentMessageAttachment = {
  id: string;
  name: string;
  size: number;
  type: string;
  dataUrl?: string;
  text?: string;
  payloadKind?: "data_url" | "text" | "metadata" | string;
  truncated?: boolean;
  error?: string;
};

export type InterruptedApplyRecovery = {
  id: string;
  schema?: string;
  status?: string;
  createdAt?: string;
  updatedAt?: string;
  targetTool?: string;
  projectRoot?: string;
  checkpointId?: string;
  approvalId?: string;
  resolution?: string;
  error?: string;
  resolvedAt?: string;
  note?: string;
};

export type InterruptedApplyRecoveryPreview = {
  ok: boolean;
  recovery?: InterruptedApplyRecovery;
  checkpointPreview?: AgentCheckpointPreview;
  error?: string;
};

export type AdjustmentCheckpoint = {
  id: string;
  schema?: string;
  kind: "face" | "shader";
  label?: string;
  description?: string;
  checkpointId?: string;
  targetTool?: string;
  projectRoot?: string;
  avatarPath?: string;
  tags?: string[];
  compareGroup?: string;
  selected?: boolean;
  selectedAt?: string;
  selectedSlots?: string[];
  selectionSlot?: string;
  deletedAt?: string;
  overwriteCount?: number;
  createdAt?: string;
  updatedAt?: string;
  checkpoint?: Partial<AgentCheckpoint>;
};

export type AgentApprovalExecution = {
  status?: string;
  result?: AgentShellResult | Record<string, unknown>;
  error?: string;
  checkpoint?: AgentCheckpoint;
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

export type AgentReasoningTrace = {
  schema?: string;
  provider?: string;
  providerLabel?: string;
  model?: string;
  source?: string;
  collapsedDefault?: boolean;
  redacted?: boolean;
  itemCount?: number;
  items?: Array<{
    title?: string;
    kind?: string;
    text?: string;
    opaque?: boolean;
  }>;
};

export type AgentContextUsage = {
  schema?: string;
  source?: string;
  exact?: boolean;
  provider?: string;
  providerLabel?: string;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  cacheReadTokens?: number;
  requestCount?: number;
  sentHistoryEntryCount?: number;
  sentHistoryCharacterCount?: number;
  promptCharacterCount?: number;
  lastPromptCharacterCount?: number;
  unavailableReason?: string;
};

export type AgentVisionAnalysis = {
  schema?: string;
  status: "analyzed" | "unconfigured" | "error" | string;
  imageCount?: number;
  imageNames?: string[];
  text?: string;
  provider?: string;
  providerLabel?: string;
  model?: string;
  source?: "main" | "visionProfile" | string;
  usage?: AgentContextUsage;
  reason?: string;
  error?: string;
  notice?: string;
};

export type AgentRuntimeResponse = {
  ok: boolean;
  session_id: string;
  sessionId: string;
  turn_id: string;
  turnId: string;
  clientTurnId?: string;
  approval_id?: string;
  approvalId?: string;
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
  reasoning?: AgentReasoningTrace;
  contextUsage?: AgentContextUsage;
  attachments?: AgentMessageAttachment[];
  write?: {
    ok?: boolean;
    status?: string;
    tool?: string;
    approval_id?: string;
    approvalId?: string;
    paramsSummary?: Record<string, unknown>;
    result?: unknown;
    error?: string;
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
  vision?: AgentVisionAnalysis;
};

export type AgentRuntimeRun = {
  id?: string;
  schema?: string;
  event?: string;
  lastEvent?: string;
  status?: string;
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  agent?: string;
  messageSummary?: string;
  provider?: string;
  providerLabel?: string;
  model?: string;
  projectRoot?: string;
  planSummary?: string;
  planner?: string;
  nextStep?: string;
  stepCount?: number;
  eventCount?: number;
  attachmentCount?: number;
  approvalId?: string;
  approvalIds?: string[];
  checkpointId?: string;
  checkpointIds?: string[];
  targetTool?: string;
  shellStatus?: string;
  skillStatus?: string;
  skillTool?: string;
  writeStatus?: string;
  writeTool?: string;
  resultSummary?: unknown;
  error?: string;
  createdAt?: string;
  updatedAt?: string;
  steps?: Array<{
    index?: number;
    kind?: string;
    tool?: string;
    summary?: string;
    status?: string;
    provider?: string;
    providerLabel?: string;
    model?: string;
    source?: string;
    usage?: AgentContextUsage;
    imageCount?: number;
  }>;
};

export type AgentRuntimeRunLedger = {
  ok: boolean;
  schema?: string;
  runs: AgentRuntimeRun[];
  events?: AgentRuntimeRun[];
  count: number;
};

export type DesktopRuntimeSnapshot = {
  ok: boolean;
  schema?: string;
  workspaceDiff?: WorkspaceDiffSummary;
  approvals?: { approvals?: AgentApproval[]; count?: number };
  runs?: AgentRuntimeRunLedger;
  desktopActions?: { actions?: AgentDesktopAction[]; count?: number };
  goals?: { goals?: AgentGoal[]; count?: number };
  memory?: { memories?: AgentMemory[]; count?: number };
};

export type AgentDesktopAction = {
  schema?: string;
  id?: string;
  action?: string;
  status?: string;
  sessionId?: string;
  clientTurnId?: string;
  projectRoot?: string;
  promptSummary?: string;
  resultSummary?: Record<string, unknown>;
  error?: string;
  createdAt?: string;
  updatedAt?: string;
};

export type AgentGoal = {
  schema?: string;
  id?: string;
  goalId: string;
  title?: string;
  summary?: string;
  status?: "active" | "paused" | "completed" | "cancelled" | string;
  projectRoot?: string;
  sessionId?: string;
  approvalPolicy?: string;
  createdAt?: string;
  updatedAt?: string;
};

export type AgentMemory = {
  schema?: string;
  id?: string;
  memoryId: string;
  scope?: "user" | "project" | string;
  kind?: string;
  text?: string;
  projectRoot?: string;
  source?: string;
  status?: string;
  createdAt?: string;
  updatedAt?: string;
};

export type HealthComponent = {
  status: "ok" | "warning" | "error" | "unknown";
  message: string;
  detail?: unknown;
};

export type DoctorStatus = "ok" | "warning" | "error" | "unknown";

export type DoctorCheck = {
  id: string;
  section?: string;
  title: string;
  status: DoctorStatus;
  message: string;
  whatFailed?: string;
  whyItMatters: string;
  howToFix: string;
  fixCommand?: string;
  fixable?: boolean;
  actions?: string[];
  detail?: unknown;
};

export type DoctorSummary = {
  okCount: number;
  warningCount: number;
  errorCount: number;
  unknownCount: number;
};

export type DoctorReport = {
  ok: boolean;
  schema: "vrcforge.doctor.v1" | string;
  scope?: string;
  projectContentInspected?: boolean;
  generatedAt: string;
  version: string;
  selectedUnityEnvironment?: {
    configured: boolean;
    label?: string;
  };
  summary: DoctorSummary;
  sections?: Array<{
    name: string;
    summary: DoctorSummary;
    checkIds: string[];
  }>;
  checks: DoctorCheck[];
};

export type UnityMcpRepairResult = {
  ok: boolean;
  schema: "vrcforge.unity_mcp_repair.v1" | string;
  status: "healthy" | "recovered" | "needs_user_action" | "failed" | string;
  generatedAt: string;
  projectPath?: string;
  phases: Array<{
    id: string;
    status: "ok" | "warning" | "error" | "skipped" | string;
    message: string;
    detail?: unknown;
  }>;
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
};

export type ProjectSnapshot = {
  selectedProjectPath?: string;
  unityEditorPath?: string;
  projects?: Array<{ name?: string; path?: string; editorVersion?: string; unityVersion?: string; sources?: string[] }>;
  scan?: {
    status?: string;
    cached?: boolean;
    refreshing?: boolean;
    updatedAt?: string;
    startedAt?: string;
    durationMs?: number;
    error?: string;
    addedCount?: number;
    removedCount?: number;
    projectCount?: number;
    addedProjects?: Array<{ name?: string; path?: string; source?: string }>;
    removedProjects?: Array<{ name?: string; path?: string; source?: string }>;
  };
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
    projects?: ProjectSnapshot;
  };
  agentManifest: AgentManifest;
  apiConfig?: ApiConfig;
  visionConfig?: VisionConfig;
  agentHealth: {
    ok: boolean;
    enabled: boolean;
    pendingApprovalCount: number;
  };
  permission: PermissionState;
  approvals: AgentApproval[];
};

export type AppHealth = AppBootstrap["health"];

export type UnityReadinessRefresh = {
  ok: boolean;
  schema: "vrcforge.unity_readiness_refresh.v1" | string;
  unityStatus?: Record<string, unknown>;
  health: AppHealth;
};

export type AppSessionHandshake = {
  ok: boolean;
  authRequired?: boolean;
  appSessionToken?: string;
  app_session_token?: string;
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

let appSessionToken = "";

export function setAppSessionToken(token: string) {
  appSessionToken = token.trim();
}

export async function fetchBootstrap(endpoint: string, options: { refreshProjects?: boolean } = {}): Promise<AppBootstrap> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AppBootstrap>("fetch_app_bootstrap", {
      request: { refreshProjects: Boolean(options.refreshProjects), timeoutMs: 30000 },
    });
  }
  const url = new URL(`${endpoint}/api/app/bootstrap`);
  if (options.refreshProjects) {
    url.searchParams.set("refreshProjects", "true");
  }
  return requestJson<AppBootstrap>(url.toString(), { preferTauriIpc: true });
}

export async function fetchAppHealth(endpoint: string): Promise<AppHealth> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AppHealth>("fetch_app_health", {
      request: { timeoutMs: 20000 },
    });
  }
  return requestJson<AppHealth>(`${endpoint}/api/health`, { timeoutMs: 20000 });
}

export async function refreshProjects(endpoint: string): Promise<ProjectSnapshot> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProjectSnapshot>("refresh_projects", {
      request: { timeoutMs: 30000 },
    });
  }
  return requestJson<ProjectSnapshot>(`${endpoint}/api/projects/refresh`, { method: "POST", timeoutMs: 30000 });
}

export async function refreshUnityReadiness(endpoint: string): Promise<UnityReadinessRefresh> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<UnityReadinessRefresh>("refresh_unity_readiness", {
      request: { timeoutMs: 20000 },
    });
  }
  return requestJson<UnityReadinessRefresh>(`${endpoint}/api/app/unity/readiness/refresh`, { method: "POST", timeoutMs: 20000 });
}

export async function fetchAppSession(endpoint: string): Promise<AppSessionHandshake> {
  return requestJson<AppSessionHandshake>(`${endpoint}/api/app/session`, { timeoutMs: 5000 });
}

export async function fetchWorkspaceDiff(endpoint: string, root = "", includePatch = false): Promise<WorkspaceDiffSummary> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<WorkspaceDiffSummary>("fetch_workspace_diff", {
      request: { root: root.trim() || undefined, includePatch, timeoutMs: 30000 },
    });
  }
  const url = new URL(`${endpoint}/api/app/workspace/diff`);
  if (root.trim()) {
    url.searchParams.set("root", root.trim());
  }
  if (includePatch) {
    url.searchParams.set("includePatch", "true");
  }
  return requestJson<WorkspaceDiffSummary>(url.toString(), { preferTauriIpc: true });
}

export async function fetchDoctor(endpoint: string): Promise<DoctorReport> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DoctorReport>("fetch_doctor", {
      request: { timeoutMs: 30000 },
    });
  }
  return requestJson<DoctorReport>(`${endpoint}/api/app/doctor`);
}

export async function repairUnityMcpBridge(
  endpoint: string,
  request: { projectPath?: string; allowUnityRelaunch?: boolean; waitSeconds?: number; closeTimeoutSeconds?: number } = {},
): Promise<UnityMcpRepairResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<UnityMcpRepairResult>("repair_unity_mcp_bridge", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<UnityMcpRepairResult>(`${endpoint}/api/app/doctor/unity-mcp/repair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchDiagnostics(endpoint: string): Promise<DiagnosticsStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DiagnosticsStatus>("fetch_diagnostics", {
      request: { timeoutMs: 30000 },
    });
  }
  return requestJson<DiagnosticsStatus>(`${endpoint}/api/app/diagnostics`);
}

export async function updateDiagnostics(endpoint: string, request: { debugLogging: boolean }): Promise<DiagnosticsStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DiagnosticsStatus>("update_diagnostics", {
      request: { ...request, timeoutMs: 30000 },
    });
  }
  return requestJson<DiagnosticsStatus>(`${endpoint}/api/app/diagnostics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function exportSupportBundle(endpoint: string, request: { includeFullPaths?: boolean; logLimit?: number } = {}): Promise<SupportBundleResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SupportBundleResult>("export_support_bundle", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<SupportBundleResult>(`${endpoint}/api/app/support-bundle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function updatePermission(
  endpoint: string,
  executionMode: PermissionState["executionMode"],
  acknowledgeRoslynRisk = false,
): Promise<{ ok: boolean; permission: PermissionState }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok: boolean; permission: PermissionState }>("update_permission_mode", {
      request: { execution_mode: executionMode, acknowledge_roslyn_risk: acknowledgeRoslynRisk, timeoutMs: 30000 },
    });
  }
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
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok?: boolean; apiConfig: ApiConfig; visionConfig?: VisionConfig }>("update_api_config", {
      request: { ...config, timeoutMs: 30000 },
    });
  }
  return requestJson<{ ok?: boolean; apiConfig: ApiConfig; visionConfig?: VisionConfig }>(`${endpoint}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function updateVisionConfig(
  endpoint: string,
  config: { provider: string; api_key: string; base_url?: string; model?: string; enabled: boolean },
) {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok?: boolean; apiConfig: ApiConfig; visionConfig: VisionConfig }>("update_vision_config", {
      request: { ...config, timeoutMs: 30000 },
    });
  }
  return requestJson<{ ok?: boolean; apiConfig: ApiConfig; visionConfig: VisionConfig }>(`${endpoint}/api/config/vision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export type ProviderModelList = {
  provider: string;
  providerLabel?: string;
  baseUrl?: string;
  models: ProviderModelInfo[];
  modelCount: number;
  selectedModel?: string;
};

export type ProviderTestResult = {
  ok: boolean;
  status: "ok" | "warning" | "error" | "skipped" | string;
  capability: "text" | "structured" | "vision" | string;
  provider: string;
  providerLabel?: string;
  model?: string;
  message: string;
  responsePreview?: string;
  skipped?: boolean;
};

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
    "codexApp" | "codexCli" | "claudeCode" | "claudeCowork",
    {
      label?: string;
      scope?: "user" | "project" | string;
      configPath?: string;
      installed?: boolean;
      installable?: boolean;
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

export type ExternalAgentConnectorClient = "codexApp" | "codexCli" | "claudeCode" | "claudeCowork";

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

export type SkillPackageEntry = {
  id?: string;
  name?: string;
  title?: string;
  version?: string;
  source?: string;
  enabled?: boolean;
  available?: boolean;
  signature_status?: string;
  signatureStatus?: string;
  signer_fingerprint?: string;
  signerFingerprint?: string;
  permissions?: string[];
  permission_tiers?: Record<string, string[]>;
  permissionTiers?: Record<string, string[]>;
  risk_level?: string;
  riskLevel?: string;
  installed_path?: string;
  installedPath?: string;
  package_path?: string;
  packagePath?: string;
  package_sha256?: string;
  packageSha256?: string;
  lock_sha256?: string;
  lockSha256?: string;
  update_action?: string;
  updateAction?: string;
  manifest?: Record<string, unknown>;
  governance?: Record<string, unknown>;
  dryRun?: Record<string, unknown>;
  warnings?: string[];
  errors?: string[];
  changed?: boolean;
};

export type SkillPackageList = {
  ok: boolean;
  store?: string;
  governance?: Record<string, unknown>;
  audit?: Array<Record<string, unknown>>;
  registry?: unknown;
  installed: SkillPackageEntry[];
};

export type SkillPackagePreflight = SkillPackageEntry & {
  ok?: boolean;
  preview?: SkillPackageEntry;
};

export type SkillPackageImportResult = {
  ok?: boolean;
  dryRun?: boolean;
  preview?: SkillPackageEntry;
  imported?: { registry_entry?: SkillPackageEntry; registryEntry?: SkillPackageEntry; [key: string]: unknown };
  projectedSkill?: { name?: string; path?: string; [key: string]: unknown } | null;
  installed?: SkillPackageEntry;
  changed?: boolean;
};

export type SkillPackageExportResult = {
  ok?: boolean;
  exported?: SkillPackageEntry;
};

export type PathToSkillCaptureRequest = {
  summary: Record<string, unknown>;
  packageId?: string;
  skillName?: string;
  title?: string;
  version?: string;
  author?: string;
  minVrcforgeVersion?: string;
  outputPath?: string;
  writeSource?: boolean;
  useTempOutput?: boolean;
  exportVsk?: boolean;
  confirmExport?: boolean;
  packageOutputPath?: string;
};

export type PathToSkillCaptureResult = {
  ok: boolean;
  schema: string;
  dryRun: boolean;
  manifest: Record<string, unknown>;
  workflow: Record<string, unknown>;
  skillMarkdown: string;
  sourceFiles: Record<string, string>;
  files: Array<{ path: string; bytes: number }>;
  writeSuppressed?: boolean;
  writtenSource?: { path: string; files: Array<{ path: string; bytes: number }> };
  exported?: SkillPackageEntry;
};

export type SkillPackageStateResult = {
  ok?: boolean;
  state?: { registry_entry?: SkillPackageEntry; registryEntry?: SkillPackageEntry; [key: string]: unknown };
  projectedSkill?: { name?: string; missing?: boolean; skipped?: boolean; [key: string]: unknown } | null;
};

export type SkillPackageUninstallResult = {
  ok?: boolean;
  uninstalled?: { skill_id?: string; skillId?: string; removed_versions?: string[]; removedVersions?: string[]; [key: string]: unknown };
  projectedSkill?: { name?: string; deleted?: string; missing?: boolean; skipped?: boolean; [key: string]: unknown } | null;
};

export type SkillPackageGovernanceActionResult = {
  ok?: boolean;
  safeMode?: Record<string, unknown>;
  signer?: Record<string, unknown>;
  blocklist?: Record<string, unknown>;
  projectedSkills?: Array<Record<string, unknown>>;
};

export async function fetchProviderModels(
  endpoint: string,
  config: { provider: string; api_key?: string; base_url?: string; model?: string },
): Promise<ProviderModelList> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProviderModelList>("fetch_provider_models", {
      request: { ...config, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function testProviderCapability(
  endpoint: string,
  request: { provider: string; api_key?: string; base_url?: string; model?: string; capability: "text" | "structured" | "vision" },
): Promise<ProviderTestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProviderTestResult>("test_provider_capability", {
      request: { ...request, timeoutMs: 30000 },
    });
  }
  return requestJson<ProviderTestResult>(`${endpoint}/api/app/provider/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchExternalAgentConnectors(endpoint: string, projectPath?: string): Promise<ExternalAgentConnectorStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ExternalAgentConnectorStatus>("fetch_external_agent_connectors", {
      request: { projectPath: projectPath || undefined, timeoutMs: 30000 },
    });
  }
  const query = projectPath ? `?projectPath=${encodeURIComponent(projectPath)}` : "";
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
  request: { client: ExternalAgentConnectorClient; projectPath?: string },
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
  request: { client: ExternalAgentConnectorClient; projectPath?: string },
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

export async function fetchSkillPackages(endpoint: string): Promise<SkillPackageList> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageList>("fetch_skill_packages", {});
  }
  return requestJson<SkillPackageList>(`${endpoint}/api/app/skill-packages`);
}

export async function preflightSkillPackage(
  endpoint: string,
  request: { packagePath: string; allowDowngrade?: boolean; devMode?: boolean; projectToUserSkills?: boolean },
): Promise<SkillPackagePreflight> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackagePreflight>("preflight_skill_package", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackagePreflight>(`${endpoint}/api/app/skill-packages/preflight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function importSkillPackage(
  endpoint: string,
  request: { packagePath: string; allowDowngrade?: boolean; devMode?: boolean; projectToUserSkills?: boolean; dryRun?: boolean },
): Promise<SkillPackageImportResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageImportResult>("import_skill_package", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackageImportResult>(`${endpoint}/api/app/skill-packages/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function setSkillPackageSafeMode(
  endpoint: string,
  request: { enabled: boolean; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("set_skill_package_safe_mode", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/safe-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function trustSkillPackageSigner(
  endpoint: string,
  request: { signerFingerprint: string; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("trust_skill_package_signer", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/trust-signer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function revokeSkillPackageSigner(
  endpoint: string,
  request: { signerFingerprint: string; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("revoke_skill_package_signer", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/revoke-signer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function blockSkillPackage(
  endpoint: string,
  request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("block_skill_package", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/block-package`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function setSkillPackageEnabled(
  endpoint: string,
  skillPackageId: string,
  request: { enabled: boolean; syncProjectedSkill?: boolean },
): Promise<SkillPackageStateResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageStateResult>("set_skill_package_enabled", {
      request: { id: skillPackageId, body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageStateResult>(`${endpoint}/api/app/skill-packages/${encodeURIComponent(skillPackageId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function uninstallSkillPackage(
  endpoint: string,
  skillPackageId: string,
  request: { removeProjectedSkill?: boolean } = {},
): Promise<SkillPackageUninstallResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageUninstallResult>("uninstall_skill_package", {
      request: { id: skillPackageId, body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackageUninstallResult>(`${endpoint}/api/app/skill-packages/${encodeURIComponent(skillPackageId)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function exportSkillPackage(
  endpoint: string,
  request: { skillName: string; outputPath: string; release?: boolean; privateKeyPath?: string; privateKeyPem?: string },
): Promise<SkillPackageExportResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageExportResult>("export_skill_package", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackageExportResult>(`${endpoint}/api/app/skill-packages/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function previewPathToSkill(
  endpoint: string,
  request: PathToSkillCaptureRequest,
): Promise<PathToSkillCaptureResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<PathToSkillCaptureResult>("preview_path_to_skill", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<PathToSkillCaptureResult>(`${endpoint}/api/app/path-to-skill/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function writePathToSkill(
  endpoint: string,
  request: PathToSkillCaptureRequest,
): Promise<PathToSkillCaptureResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<PathToSkillCaptureResult>("write_path_to_skill", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<PathToSkillCaptureResult>(`${endpoint}/api/app/path-to-skill/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchAgentNotes(endpoint: string): Promise<AgentNotes> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentNotes>("fetch_agent_notes", {});
  }
  return requestJson<AgentNotes>(`${endpoint}/api/app/agent-notes`);
}

export async function saveAgentNotes(endpoint: string, content: string): Promise<{ ok: boolean; path: string; bytes: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("save_agent_notes", {
      request: { body: { content }, timeoutMs: 60000 },
    });
  }
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
  sources?: Array<Record<string, unknown>>;
};

export async function fetchChats<T>(endpoint: string, projectPaths: string[] = []): Promise<StoredChats<T>> {
  const params = new URLSearchParams();
  for (const projectPath of projectPaths) {
    if (projectPath.trim()) {
      params.append("projectPath", projectPath);
    }
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<StoredChats<T>>("fetch_chats", {
      request: { projectPaths, timeoutMs: 60000 },
    });
  }
  return requestJson<StoredChats<T>>(`${endpoint}/api/app/chats${suffix}`);
}

export async function saveChats<T>(
  endpoint: string,
  chats: T[],
): Promise<{ ok: boolean; path: string; count: number; appCount?: number; projectPaths?: Array<Record<string, unknown>> }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("save_chats", {
      request: { body: { chats }, timeoutMs: 60000 },
    });
  }
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

export type ProjectIndexPathEntry = {
  path: string;
  category?: string;
  size?: number;
  sha256?: string;
};

export type ProjectIndexScanResult = {
  ok: boolean;
  schema: string;
  projectId?: string;
  projectName?: string;
  indexPath?: string;
  error?: string;
  summary?: {
    firstScan?: boolean;
    totalFiles?: number;
    unchangedFiles?: number;
    addedFiles?: number;
    modifiedFiles?: number;
    deletedFiles?: number;
    guidChangeCount?: number;
    hashesComputed?: number;
    hashesReused?: number;
    truncated?: boolean;
    changed?: boolean;
    scannerFamilies?: string[];
  };
  changes?: {
    added?: ProjectIndexPathEntry[];
    modified?: ProjectIndexPathEntry[];
    deleted?: ProjectIndexPathEntry[];
    guidChanges?: Array<{ path?: string; oldGuid?: string; newGuid?: string }>;
  };
  packageFingerprints?: Record<string, unknown>;
  metaGuidCount?: number;
  staleDataPolicy?: string;
  privacy?: Record<string, unknown>;
};

export type OptimizationTargetProfile = {
  id?: string;
  label?: string;
  platform?: string;
  riskTolerance?: string;
  weights?: Record<string, number>;
};

export type OptimizationDependencyCard = {
  id?: string;
  label?: string;
  status?: "installed" | "missing" | "unknown" | string;
  installed?: boolean;
  packageIds?: string[];
  version?: string | null;
  matchedPackageId?: string;
  recommendedRole?: string;
  riskLevel?: string;
  docsLink?: string;
  installMethod?: { kind?: string; repository?: string; automatic?: boolean; supervisedRequestSupported?: boolean };
};

export type OptimizationActionCard = {
  id: string;
  title: string;
  description?: string;
  riskLevel?: string;
  dependency?: string;
  recommendedVersionStage?: string;
  level?: string;
  enabled?: boolean;
  blockedReason?: string | null;
  expectedBenefit?: string;
  whyRecommended?: string;
  nextSafeAction?: string;
  requestTool?: string;
  requestOnly?: boolean;
  affectedAssetsOrRenderers?: unknown[];
  directApplyExposed?: boolean;
};

export type OptimizationPlannerReport = {
  ok: boolean;
  schema: string;
  versionStage?: string;
  generatedAt?: string;
  readOnly?: boolean;
  planOnly?: boolean;
  noProjectWrites?: boolean;
  directApplyExposed?: boolean;
  targetProfile?: OptimizationTargetProfile;
  baseline?: {
    performanceHeadline?: Record<string, { rank?: string; triangleCount?: number; materialSlots?: number; textureMemoryBytes?: number }>;
    metrics?: Record<string, number | null | undefined>;
    validationSummary?: Record<string, unknown>;
  };
  dependencyDoctor?: {
    dependencies?: OptimizationDependencyCard[];
    summary?: Record<string, number>;
    installPolicy?: Record<string, unknown>;
  };
  audits?: Record<string, unknown>;
  plans?: Record<string, unknown>;
  topOffenders?: Array<{ id?: string; label?: string; severity?: string; count?: number }>;
  actionCards?: OptimizationActionCard[];
  recommendedOrder?: string[];
  nextSafeAction?: OptimizationActionCard | null;
  tools?: Array<{ externalName?: string; gatewayName?: string; level?: string; directApplyExposed?: boolean }>;
  futureWriteRequestTools?: Array<{ externalName?: string; versionStage?: string; directApplyExposed?: boolean }>;
  rules?: Record<string, unknown>;
};

export type AvatarListItem = {
  avatarName?: string;
  avatarPath?: string;
  sceneName?: string;
  rendererCount?: number;
  blendshapeCount?: number;
  isVrChatAvatar?: boolean;
};

export type AvatarListResult = {
  ok: boolean;
  executed?: boolean;
  exportSource?: string;
  executionMode?: string;
  summary?: Record<string, unknown>;
  avatars?: AvatarListItem[];
  avatarCount?: number;
};

export type AvatarEncryptionProfileCard = {
  id: "lite" | "standard" | "paranoid" | string;
  icon?: string;
  title?: string;
  label?: string;
  description?: string;
  recommended?: boolean;
  cost?: string;
  deviceFit?: string;
  protection?: string;
  applyStatus?: string;
};

export type AvatarEncryptionBenchmarkRow = {
  profile?: string;
  label?: string;
  triangles?: number;
  avatarScale?: string;
  baselineFps?: number;
  estimatedFps?: number;
  estimatedFpsLoss?: number;
  estimatedFrameTimeAddedMs?: number;
  estimatedImpactPercent?: number;
  gpuCost?: string;
};

export type AvatarEncryptionPlanResult = {
  ok: boolean;
  schema?: string;
  scan?: {
    summary?: Record<string, unknown>;
    targets?: Array<Record<string, unknown>>;
  };
  plan?: {
    status?: string;
    writeStatus?: string;
    writeBlockReason?: string;
    avatarPath?: string;
    selectedCandidateCount?: number;
    selectedCandidates?: Array<Record<string, unknown>>;
    targetShaderFamilies?: string[];
    profile?: AvatarEncryptionProfileCard & Record<string, unknown>;
    recommendedProfile?: string;
    profileCards?: AvatarEncryptionProfileCard[];
    benchmarkTable?: AvatarEncryptionBenchmarkRow[];
    benchmarkAssumptions?: Record<string, unknown>;
    hardGate?: { status?: string; blockingIds?: string[]; warnings?: string[] };
    futureRequestTools?: Record<string, unknown>;
    externalAddon?: Record<string, unknown>;
    platform?: Record<string, unknown>;
    layers?: Array<Record<string, unknown>>;
  };
  error?: string;
};

export type PackageInstallRequestResult = {
  ok: boolean;
  status?: string;
  approval?: AgentApproval;
  error?: string;
  installPlan?: Record<string, unknown>;
};

export type OptimizationApplyRequestResult = {
  ok: boolean;
  status?: string;
  approval?: AgentApproval;
  error?: string;
  preview?: Record<string, unknown>;
  installPlan?: Record<string, unknown>;
};

export type AvatarEncryptionApplyRequestResult = OptimizationApplyRequestResult;

export type OutfitDependencyPreflight = {
  schema?: string;
  readyForImport?: boolean;
  blockingMissingCount?: number;
  blockingIssueCount?: number;
  detectedCount?: number;
  packageOrder?: {
    importQueue?: Array<{
      order?: number;
      path?: string;
      sourceType?: string;
      role?: string;
      reason?: string;
      actualPackagePath?: string;
      containerPath?: string;
      selected?: boolean;
    }>;
    skippedInstalledSupportPackages?: Array<{
      order?: number;
      path?: string;
      sourceType?: string;
      role?: string;
      reason?: string;
      actualPackagePath?: string;
      containerPath?: string;
      selected?: boolean;
      skipReason?: string;
      dependencyId?: string;
      dependencyLabel?: string;
      message?: string;
    }>;
    skippedInstalledSupportCount?: number;
    importCount?: number;
    supportPackageCount?: number;
    requiresManualExtract?: boolean;
    blockingBeforeImport?: boolean;
    warnings?: string[];
  };
  compatibility?: {
    status?: string;
    baseAvatarName?: string;
    detectedAvatarNames?: string[];
    blockingBeforeImport?: boolean;
    message?: string;
    evidence?: Record<string, string[]>;
    warnings?: string[];
  };
  entries?: Array<{
    id?: string;
    label?: string;
    kind?: string;
    status?: string;
    message?: string;
    blockingBeforeImport?: boolean;
    stage?: string;
    packageIds?: string[];
    evidence?: {
      packagePathnames?: string[];
      hints?: string[];
      project?: string[];
    };
  }>;
  warnings?: string[];
  recommendedOrder?: string[];
};

export type OutfitImportPlanResult = {
  ok: boolean;
  schema?: string;
  preview?: boolean;
  plannedAt?: string;
  error?: string;
  dependencyPreflight?: OutfitDependencyPreflight;
  inspection?: {
    ok?: boolean;
    summary?: {
      unityPackageCount?: number;
      prefabCandidateCount?: number;
      textureCount?: number;
      materialCount?: number;
      modelCount?: number;
      unsafeEntryCount?: number;
      duplicateEntryCount?: number;
      importPlanKind?: string;
    };
    unityPackages?: Array<{ path?: string; size?: number; pathnameCount?: number }>;
    prefabCandidates?: Array<{ path?: string; source?: string }>;
    textures?: Array<{ path?: string; source?: string }>;
    materials?: Array<{ path?: string; source?: string }>;
    models?: Array<{ path?: string; source?: string }>;
    warnings?: string[];
  };
  plan?: {
    id?: string;
    kind?: string;
    ok?: boolean;
    readyToApply?: boolean;
    requiresApproval?: boolean;
    requiresCheckpoint?: boolean;
    validationAfterApply?: boolean;
    rollbackProofRequired?: boolean;
    projectPath?: string;
    targetFolder?: string;
    source?: {
      type?: string;
      path?: string;
      selectedUnityPackage?: string;
      actualPackagePath?: string;
      importQueue?: Array<{
        order?: number;
        path?: string;
        sourceType?: string;
        role?: string;
        reason?: string;
        actualPackagePath?: string;
        containerPath?: string;
        selected?: boolean;
      }>;
    };
    selectedPrefab?: string;
    expectedAssetPaths?: string[];
    dependencyPreflight?: OutfitDependencyPreflight;
    writeTarget?: string;
    steps?: Array<{ id?: string; category?: string; tool?: string; description?: string; enabled?: boolean }>;
    warnings?: string[];
    error?: string;
  };
  warnings?: string[];
  privacy?: Record<string, unknown>;
};

export type SubAgentRole = {
  id: string;
  title: string;
  description?: string;
  toolProfile?: string;
  readOnly?: boolean;
};

export type SubAgentTask = {
  schema?: string;
  id: string;
  role: string;
  displayName: string;
  task: string;
  parentSessionId?: string;
  projectPath?: string;
  toolProfile?: string;
  status: "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled" | string;
  createdAt?: string;
  startedAt?: string;
  stoppedAt?: string;
  updatedAt?: string;
  cancelRequested?: boolean;
  summary?: string;
  error?: string;
  eventCount?: number;
  result?: Record<string, unknown> | null;
  paramsSummary?: Record<string, unknown>;
  events?: Array<{ timestamp?: string; event?: string; data?: Record<string, unknown> }>;
};

export type SubAgentTaskList = {
  ok: boolean;
  schema: string;
  tasks: SubAgentTask[];
  count: number;
  roles?: SubAgentRole[];
  maxConcurrent?: number;
  runningCount?: number;
};

export async function fetchProjectPrefs(endpoint: string): Promise<ProjectPrefs> {
  if (hasTauriInternals()) {
    const payload = await invokeTauriWithAbort<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>("fetch_project_prefs", {
      request: { timeoutMs: 30000 },
    });
    return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
  }
  const payload = await requestJson<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>(
    `${endpoint}/api/app/projects/prefs`,
  );
  return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
}

export async function saveProjectPrefs(endpoint: string, prefs: ProjectPrefs): Promise<ProjectPrefs> {
  if (hasTauriInternals()) {
    const payload = await invokeTauriWithAbort<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>("save_project_prefs", {
      request: { customPaths: prefs.customPaths, hiddenPaths: prefs.hiddenPaths, timeoutMs: 30000 },
    });
    return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
  }
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

export async function scanProjectIndex(
  endpoint: string,
  request: { projectPath: string; maxFiles?: number },
): Promise<ProjectIndexScanResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProjectIndexScanResult>("scan_project_index", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<ProjectIndexScanResult>(`${endpoint}/api/app/project-index/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchOptimizationPlan(
  endpoint: string,
  request: {
    projectPath?: string;
    avatarPath?: string;
    targetProfile?: string;
    customProfile?: Record<string, unknown>;
    includeQuest?: boolean;
    maxErrors?: number;
  },
): Promise<OptimizationPlannerReport> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationPlannerReport>("fetch_optimization_plan", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<OptimizationPlannerReport>(`${endpoint}/api/app/optimization/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export type OptimizationProofSummary = {
  runId: string;
  schema?: string;
  ok?: boolean;
  status?: string;
  tool?: string;
  checkpointId?: string;
  rollbackDone?: boolean;
  changedFileCount?: number;
  failedSteps?: string[];
  startedAt?: string;
  finishedAt?: string;
  modifiedAt?: string;
  visualRegression?: Record<string, unknown>;
  rollbackProof?: Record<string, unknown>;
  profileDiff?: Record<string, unknown>;
  profileDiffUnavailable?: boolean;
  parameterBudgetDelta?: Record<string, unknown>;
  reportPath?: string;
  error?: string;
};

export type OptimizationProofList = {
  ok: boolean;
  schema: string;
  readOnly: boolean;
  artifactRoot?: string;
  count: number;
  proofs: OptimizationProofSummary[];
};

export type OptimizationProofDetail = {
  ok: boolean;
  schema: string;
  readOnly: boolean;
  proof: OptimizationProofSummary;
  report: Record<string, unknown>;
};

export async function fetchOptimizationProofs(endpoint: string, limit = 8): Promise<OptimizationProofList> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationProofList>("fetch_optimization_proofs", {
      request: { limit, timeoutMs: 30000 },
    });
  }
  return requestJson<OptimizationProofList>(`${endpoint}/api/app/optimization/proofs?limit=${encodeURIComponent(String(limit))}`);
}

export async function fetchOptimizationProof(endpoint: string, runId: string): Promise<OptimizationProofDetail> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationProofDetail>("fetch_optimization_proof", {
      request: { id: runId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson<OptimizationProofDetail>(`${endpoint}/api/app/optimization/proofs/${encodeURIComponent(runId)}`);
}

export async function fetchAvatars(
  endpoint: string,
  request: { projectPath?: string } = {},
): Promise<AvatarListResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AvatarListResult>("fetch_avatars", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<AvatarListResult>(`${endpoint}/api/app/avatars`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function planAvatarEncryption(
  endpoint: string,
  request: {
    projectPath?: string;
    avatarPath?: string;
    profile?: string;
    protectionProfile?: string;
    platform?: string;
    targetShaderFamilies?: string[];
    materialIds?: string[];
    rendererPaths?: string[];
    targets?: Array<Record<string, unknown>>;
    confirmCreatorOwnedAssets?: boolean;
  },
): Promise<AvatarEncryptionPlanResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AvatarEncryptionPlanResult>("plan_avatar_encryption", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<AvatarEncryptionPlanResult>(`${endpoint}/api/avatar-encryption/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestAvatarEncryptionApply(
  endpoint: string,
  request: {
    projectPath?: string;
    avatarPath?: string;
    profile?: string;
    protectionProfile?: string;
    targetShaderFamily: string;
    targetShaderFamilies?: string[];
    materialIds?: string[];
    rendererPaths?: string[];
    targets?: Array<Record<string, unknown>>;
    outputFolder?: string;
    confirmCreatorOwnedAssets: boolean;
    saveAssets?: boolean;
  },
): Promise<AvatarEncryptionApplyRequestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AvatarEncryptionApplyRequestResult>("request_avatar_encryption_apply", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<AvatarEncryptionApplyRequestResult>(`${endpoint}/api/avatar-encryption/apply-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestPackageInstall(
  endpoint: string,
  request: {
    projectPath?: string;
    packageId: string;
    repository?: string;
    preferredManager?: string;
    allowAgentManagedDownload?: boolean;
    includePrerelease?: boolean;
  },
): Promise<PackageInstallRequestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<PackageInstallRequestResult>("request_package_install", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<PackageInstallRequestResult>(`${endpoint}/api/app/package-install/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestOptimizationApply(
  endpoint: string,
  request: {
    tool: string;
    projectPath?: string;
    avatarPath?: string;
    targetProfile?: string;
    profile?: string;
    options?: Record<string, unknown>;
    installMissingDependencies?: boolean;
    allowExperimental?: boolean;
    includePrerelease?: boolean;
  },
): Promise<OptimizationApplyRequestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationApplyRequestResult>("request_optimization_apply", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<OptimizationApplyRequestResult>(`${endpoint}/api/app/optimization/apply-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function planOutfitImport(
  endpoint: string,
  request: {
    packagePath: string;
    projectPath?: string;
    targetFolder?: string;
    selectedUnityPackage?: string;
    selectedPrefab?: string;
    baseAvatarName?: string;
    maxEntries?: number;
  },
): Promise<OutfitImportPlanResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OutfitImportPlanResult>("plan_outfit_import", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<OutfitImportPlanResult>(`${endpoint}/api/app/outfit-imports/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestOutfitImport(
  endpoint: string,
  request: {
    packagePath: string;
    projectPath?: string;
    targetFolder?: string;
    selectedUnityPackage?: string;
    selectedPrefab?: string;
    baseAvatarName?: string;
    maxEntries?: number;
  },
): Promise<{ ok: boolean; approval?: AgentApproval; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_outfit_import", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/outfit-imports/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchSubAgents(endpoint: string, includeEvents = false): Promise<SubAgentTaskList> {
  const suffix = includeEvents ? "?includeEvents=true" : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SubAgentTaskList>("fetch_sub_agents", {
      request: { includeEvents, timeoutMs: 30000 },
    });
  }
  return requestJson<SubAgentTaskList>(`${endpoint}/api/app/sub-agents${suffix}`);
}

export async function createSubAgent(
  endpoint: string,
  request: {
    role: string;
    task?: string;
    displayName?: string;
    parentSessionId?: string;
    projectPath?: string;
    params?: Record<string, unknown>;
  },
): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_sub_agent", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchSubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_sub_agent", {
      request: { id: taskId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}`);
}

export async function cancelSubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("cancel_sub_agent", {
      request: { id: taskId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}

export async function retrySubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("retry_sub_agent", {
      request: { id: taskId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
}

export async function fetchSkills(endpoint: string): Promise<AgentSkillRegistry> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry>("fetch_skills", {});
  }
  return requestJson<AgentSkillRegistry>(`${endpoint}/api/app/skills`);
}

export async function checkSkills(endpoint: string): Promise<AgentSkillCheck> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillCheck>("check_skills", {});
  }
  return requestJson<AgentSkillCheck>(`${endpoint}/api/app/skills/check`);
}

export async function createSkill(endpoint: string, skill: Partial<AgentSkill>): Promise<AgentSkillRegistry & { skill: AgentSkill }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry & { skill: AgentSkill }>("create_skill", {
      request: { body: skill, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
}

export async function updateSkill(endpoint: string, skillId: string, skill: Partial<AgentSkill>): Promise<AgentSkillRegistry & { skill: AgentSkill }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry & { skill: AgentSkill }>("update_skill", {
      request: { id: skillId, body: skill, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
}

export async function deleteSkill(endpoint: string, skillId: string): Promise<AgentSkillRegistry> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentSkillRegistry>("delete_skill", {
      request: { id: skillId, body: {}, timeoutMs: 60000 },
    });
  }
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
  agentName?: string,
  options: { signal?: AbortSignal; attachments?: AgentMessageAttachment[]; projectPath?: string; provider?: string; providerLabel?: string; model?: string; clientTurnId?: string } = {},
): Promise<AgentRuntimeResponse> {
  const request = {
    agentName: agentName || "desktop-agent",
    sessionId: sessionId || undefined,
    clientTurnId: options.clientTurnId || undefined,
    message,
    history: history ?? [],
    attachments: options.attachments ?? [],
    projectPath: options.projectPath || undefined,
    provider: options.provider || undefined,
    providerLabel: options.providerLabel || undefined,
    model: options.model || undefined,
  };
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentRuntimeResponse>("send_agent_message", { request }, options.signal);
  }
  return requestJson(`${endpoint}/api/app/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: options.signal,
    body: JSON.stringify({
      agent_name: request.agentName,
      session_id: request.sessionId || null,
      clientTurnId: request.clientTurnId,
      message: request.message,
      history: request.history,
      attachments: request.attachments,
      projectPath: request.projectPath,
      provider: request.provider,
      providerLabel: request.providerLabel,
      model: request.model,
    }),
  });
}

export async function fetchAgentRuns(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string; clientTurnId?: string } = {},
): Promise<AgentRuntimeRunLedger> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.clientTurnId) {
    query.set("clientTurnId", params.clientTurnId);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentRuntimeRunLedger>("fetch_agent_runs", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson<AgentRuntimeRunLedger>(`${endpoint}/api/app/agent/runs${suffix}`, { preferTauriIpc: true });
}

export async function requestAgentRunCancel(
  endpoint: string,
  payload: { sessionId?: string; turnId?: string; clientTurnId?: string; reason?: string },
): Promise<{ ok: boolean; status?: string; event?: AgentRuntimeRun }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_agent_run_cancel", { request: { ...payload, timeoutMs: 30000 } });
  }
  return requestJson(`${endpoint}/api/app/agent/runs/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function recordAgentRunQueued(
  endpoint: string,
  payload: {
    sessionId?: string;
    clientTurnId: string;
    message?: string;
    attachments?: AgentMessageAttachment[];
    provider?: string;
    providerLabel?: string;
    model?: string;
    projectPath?: string;
    projectRoot?: string;
  },
): Promise<{ ok: boolean; status?: string; event?: AgentRuntimeRun }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("record_agent_run_queued", { request: { ...payload, timeoutMs: 30000 } });
  }
  return requestJson(`${endpoint}/api/app/agent/runs/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function fetchAgentDesktopActions(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; schema?: string; actions: AgentDesktopAction[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_desktop_actions", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/desktop-actions${suffix}`, { preferTauriIpc: true });
}

export async function requestAgentDesktopAction(
  endpoint: string,
  payload: { action: string; prompt?: string; sessionId?: string; clientTurnId?: string; projectPath?: string; projectRoot?: string; params?: Record<string, unknown> },
): Promise<{ ok: boolean; schema?: string; status?: string; action?: string; event?: AgentDesktopAction; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_agent_desktop_action", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/desktop-actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 60000,
  });
}

export async function fetchAgentGoals(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; schema?: string; goals: AgentGoal[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_goals", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals${suffix}`, { preferTauriIpc: true });
}

export async function createAgentGoal(
  endpoint: string,
  payload: { title?: string; goal?: string; summary?: string; sessionId?: string; projectPath?: string; projectRoot?: string },
): Promise<{ ok: boolean; goal: AgentGoal }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_agent_goal", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateAgentGoal(
  endpoint: string,
  goalId: string,
  payload: { status: string; summary?: string; note?: string; sessionId?: string; projectRoot?: string },
): Promise<{ ok: boolean; goal: AgentGoal }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("update_agent_goal", {
      request: { id: goalId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals/${encodeURIComponent(goalId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchAgentMemory(
  endpoint: string,
  params: { limit?: number; projectRoot?: string; scope?: string } = {},
): Promise<{ ok: boolean; schema?: string; memories: AgentMemory[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.scope) {
    query.set("scope", params.scope);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_memory", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory${suffix}`, { preferTauriIpc: true });
}

export async function createAgentMemory(
  endpoint: string,
  payload: { text?: string; content?: string; scope?: string; kind?: string; source?: string; projectPath?: string; projectRoot?: string },
): Promise<{ ok: boolean; memory: AgentMemory }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_agent_memory", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteAgentMemory(
  endpoint: string,
  memoryId: string,
  payload: { reason?: string } = {},
): Promise<{ ok: boolean; memory: AgentMemory }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("delete_agent_memory", {
      request: { id: memoryId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function clearAgentMemory(
  endpoint: string,
  payload: { scope?: string; reason?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; cleared: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("clear_agent_memory", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function compactAgentHistory(
  endpoint: string,
  history: ChatHistoryEntry[],
): Promise<{ ok: boolean; summary: string; provider?: string; model?: string; entryCount?: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("compact_agent_history", {
      request: { body: { history }, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ history }),
  });
}

export async function approveAgentApproval(
  endpoint: string,
  approvalId: string,
  scope: { expectedProjectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approval?: AgentApproval; execution?: AgentApprovalExecution }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("approve_agent_approval", {
      request: { approvalId, ...scope, timeoutMs: 180000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
    timeoutMs: 180000,
  });
}

export async function fetchDesktopRuntimeSnapshot(
  endpoint: string,
  params: { sessionId?: string; projectRoot?: string; includePatch?: boolean; globalOnly?: boolean } = {},
): Promise<DesktopRuntimeSnapshot> {
  if (hasTauriInternals()) {
    return invoke<DesktopRuntimeSnapshot>("desktop_runtime_snapshot", {
      request: {
        sessionId: params.sessionId,
        projectRoot: params.projectRoot,
        includePatch: params.includePatch,
        globalOnly: params.globalOnly,
        timeoutMs: 30000,
      },
    });
  }
  const query = new URLSearchParams();
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.includePatch) {
    query.set("includePatch", "true");
  }
  if (params.globalOnly) {
    query.set("globalOnly", "true");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<DesktopRuntimeSnapshot>(`${endpoint}/api/app/runtime/snapshot${suffix}`);
}

export async function fetchAgentApprovals(
  endpoint: string,
  params: { projectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approvals: AgentApproval[]; count: number }> {
  const query = new URLSearchParams();
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.globalOnly) {
    query.set("globalOnly", "1");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_approvals", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals${suffix}`, { preferTauriIpc: true });
}

export async function rejectAgentApproval(
  endpoint: string,
  approvalId: string,
  scope: { expectedProjectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approval?: AgentApproval; message?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("reject_agent_approval", {
      request: { approvalId, ...scope, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
    timeoutMs: 60000,
  });
}

export async function requestApprovalRevision(
  endpoint: string,
  approvalId: string,
  payload: { reason?: string; note?: string; expectedProjectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approval?: AgentApproval; message?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_approval_revision", {
      request: { approvalId, ...payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/revision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 60000,
  });
}

export async function fetchCheckpoints(
  endpoint: string,
  projectRoot?: string,
): Promise<{ ok: boolean; checkpoints: AgentCheckpoint[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_checkpoints", {
      request: { projectRoot: projectRoot || undefined, timeoutMs: 30000 },
    });
  }
  const params = new URLSearchParams();
  if (projectRoot) {
    params.set("projectRoot", projectRoot);
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/checkpoints${suffix}`, { preferTauriIpc: true });
}

export async function previewRestoreCheckpoint(endpoint: string, checkpointId: string): Promise<AgentCheckpointPreview> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("preview_restore_checkpoint", {
      request: { checkpointId, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/checkpoints/${encodeURIComponent(checkpointId)}/preview`, {
    method: "POST",
  });
}

export async function requestRestoreCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_restore_checkpoint", {
      request: { checkpointId, timeoutMs: 180000 },
    });
  }
  return requestJson(`${endpoint}/api/app/checkpoints/${encodeURIComponent(checkpointId)}/restore`, {
    method: "POST",
  });
}

export async function fetchInterruptedApplyRecoveries(
  endpoint: string,
  options: { projectRoot?: string; includeResolved?: boolean } = {},
): Promise<{ ok: boolean; recoveries: InterruptedApplyRecovery[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_interrupted_apply_recoveries", {
      request: {
        projectRoot: options.projectRoot || undefined,
        includeResolved: options.includeResolved || undefined,
        timeoutMs: 30000,
      },
    });
  }
  const params = new URLSearchParams();
  if (options.projectRoot) params.set("projectRoot", options.projectRoot);
  if (options.includeResolved) params.set("includeResolved", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/recoveries${suffix}`, { preferTauriIpc: true });
}

export async function previewInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
): Promise<InterruptedApplyRecoveryPreview> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("preview_interrupted_apply_recovery", {
      request: { recoveryId, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/preview`, {
    method: "POST",
  });
}

export async function requestRestoreInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_restore_interrupted_apply_recovery", {
      request: { recoveryId, timeoutMs: 180000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/restore`, {
    method: "POST",
  });
}

export async function resolveInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
  body: { confirmResolved: boolean; note?: string },
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("resolve_interrupted_apply_recovery", {
      request: { recoveryId, ...body, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function exportInterruptedApplyIncidentBundle(
  endpoint: string,
  recoveryId: string,
): Promise<{ ok: boolean; bundlePath?: string; path?: string; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("export_interrupted_apply_incident_bundle", {
      request: { recoveryId, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/incident-bundle`, {
    method: "POST",
  });
}

export async function fetchAdjustmentCheckpoints(
  endpoint: string,
  options: { kind?: "face" | "shader"; projectRoot?: string; avatarPath?: string; includeDeleted?: boolean } = {},
): Promise<{ ok: boolean; checkpoints: AdjustmentCheckpoint[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_adjustment_checkpoints", {
      request: { ...options, timeoutMs: 30000 },
    });
  }
  const params = new URLSearchParams();
  if (options.kind) params.set("kind", options.kind);
  if (options.projectRoot) params.set("projectRoot", options.projectRoot);
  if (options.avatarPath) params.set("avatarPath", options.avatarPath);
  if (options.includeDeleted) params.set("includeDeleted", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints${suffix}`);
}

export async function createAdjustmentCheckpoint(
  endpoint: string,
  body: Partial<AdjustmentCheckpoint> & { kind: "face" | "shader"; overwrite?: boolean },
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; baseCheckpoint?: AgentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_adjustment_checkpoint", {
      request: { body, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  body: Partial<AdjustmentCheckpoint>,
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("update_adjustment_checkpoint", {
      request: { checkpointId, body, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  hardDelete = false,
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; hardDelete: boolean }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("delete_adjustment_checkpoint", {
      request: { checkpointId, hardDelete, timeoutMs: 60000 },
    });
  }
  const suffix = hardDelete ? "?hardDelete=true" : "";
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}${suffix}`, {
    method: "DELETE",
  });
}

export async function overwriteAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  body: Partial<AdjustmentCheckpoint> = {},
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; baseCheckpoint?: AgentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("overwrite_adjustment_checkpoint", {
      request: { checkpointId, body, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/overwrite`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function selectAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  body: { slot?: "A" | "B" | "current"; compareGroup?: string } = {},
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; selection: Record<string, unknown> }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("select_adjustment_checkpoint", {
      request: { checkpointId, body, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function applyAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("apply_adjustment_checkpoint", {
      request: { checkpointId, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/apply`, {
    method: "POST",
  });
}

export async function previewAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<AgentCheckpointPreview & { adjustmentCheckpoint?: AdjustmentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("preview_adjustment_checkpoint", {
      request: { checkpointId, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/preview`, {
    method: "POST",
  });
}

type JsonRequestInit = RequestInit & { timeoutMs?: number; preferTauriIpc?: boolean };

async function requestJson<T>(url: string, init: JsonRequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = init.timeoutMs ?? 30000;
  const timeout = timeoutMs > 0 ? window.setTimeout(() => controller.abort(), timeoutMs) : undefined;
  const headers = new Headers(init.headers);
  if (appSessionToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${appSessionToken}`);
  }
  let response: Response;
  try {
    const { timeoutMs: _timeoutMs, preferTauriIpc: _preferTauriIpc, ...fetchInit } = init;
    const tauriLocalApiRequest = isTauriLocalApiUrl(url);
    if (tauriLocalApiRequest) {
      throw new ApiError("This desktop route has not been migrated to a typed IPC command.", 0);
    }
    response = await fetch(url, { ...fetchInit, headers, signal: init.signal ?? controller.signal });
  } catch (cause) {
    if (cause instanceof ApiError) {
      throw cause;
    }
    if (cause instanceof DOMException && cause.name === "AbortError") {
      if (init.signal?.aborted) {
        throw new ApiError("Request cancelled.", 0);
      }
      throw new ApiError(`Request timed out after ${timeoutMs / 1000}s`, 0);
    }
    throw new ApiError(
      `VRCForge runtime is not reachable at ${runtimeOriginFromUrl(url)}. Use Retry to start the local backend, or open Doctor for logs and repair steps.`,
      0,
      cause instanceof Error ? cause.message : String(cause),
    );
  } finally {
    if (timeout !== undefined) {
      window.clearTimeout(timeout);
    }
  }
  const text = await response.text();
  let payload: unknown = {};
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      const excerpt = text.slice(0, 300);
      throw new ApiError(`HTTP ${response.status}: response was not JSON`, response.status, excerpt);
    }
  }
  if (!response.ok) {
    const detail = typeof payload === "object" && payload ? (payload as { detail?: unknown }).detail : payload;
    throw new ApiError(typeof detail === "string" ? detail : `HTTP ${response.status}`, response.status, detail);
  }
  return payload as T;
}

function isTauriLocalApiUrl(url: string): boolean {
  return hasTauriInternals() && isDesktopLoopbackApiUrl(url);
}

function hasTauriInternals(): boolean {
  return typeof window !== "undefined" && Boolean((window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
}

function invokeTauriWithAbort<T>(command: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<T> {
  if (!signal) {
    return invoke<T>(command, args);
  }
  if (signal.aborted) {
    return Promise.reject(new ApiError("Request cancelled.", 0));
  }
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    const onAbort = () => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      reject(new ApiError("Request cancelled.", 0));
    };
    signal.addEventListener("abort", onAbort, { once: true });
    invoke<T>(command, args)
      .then((value) => {
        if (!settled) {
          settled = true;
          cleanup();
          resolve(value);
        }
      })
      .catch((error) => {
        if (!settled) {
          settled = true;
          cleanup();
          reject(error);
        }
      });
  });
}

function runtimeOriginFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return "the configured endpoint";
  }
}
