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
  reasoning?: AgentReasoningTrace;
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

let appSessionToken = "";

export function setAppSessionToken(token: string) {
  appSessionToken = token.trim();
}

export async function fetchBootstrap(endpoint: string): Promise<AppBootstrap> {
  return requestJson<AppBootstrap>(`${endpoint}/api/app/bootstrap`);
}

export async function fetchDoctor(endpoint: string): Promise<DoctorReport> {
  return requestJson<DoctorReport>(`${endpoint}/api/app/doctor`);
}

export async function repairUnityMcpBridge(
  endpoint: string,
  request: { projectPath?: string; allowUnityRelaunch?: boolean; waitSeconds?: number; closeTimeoutSeconds?: number } = {},
): Promise<UnityMcpRepairResult> {
  return requestJson<UnityMcpRepairResult>(`${endpoint}/api/app/doctor/unity-mcp/repair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchDiagnostics(endpoint: string): Promise<DiagnosticsStatus> {
  return requestJson<DiagnosticsStatus>(`${endpoint}/api/app/diagnostics`);
}

export async function updateDiagnostics(endpoint: string, request: { debugLogging: boolean }): Promise<DiagnosticsStatus> {
  return requestJson<DiagnosticsStatus>(`${endpoint}/api/app/diagnostics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function exportSupportBundle(endpoint: string, request: { includeFullPaths?: boolean; logLimit?: number } = {}): Promise<SupportBundleResult> {
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
  return requestJson<ProviderTestResult>(`${endpoint}/api/app/provider/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchExternalAgentConnectors(endpoint: string, projectPath?: string): Promise<ExternalAgentConnectorStatus> {
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
  return requestJson<ExternalAgentConnectorStatus>(`${endpoint}/api/app/external-agent/connectors/uninstall`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchSkillPackages(endpoint: string): Promise<SkillPackageList> {
  return requestJson<SkillPackageList>(`${endpoint}/api/app/skill-packages`);
}

export async function preflightSkillPackage(
  endpoint: string,
  request: { packagePath: string; allowDowngrade?: boolean; devMode?: boolean; projectToUserSkills?: boolean },
): Promise<SkillPackagePreflight> {
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
  return requestJson<PathToSkillCaptureResult>(`${endpoint}/api/app/path-to-skill/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
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

export async function scanProjectIndex(
  endpoint: string,
  request: { projectPath: string; maxFiles?: number },
): Promise<ProjectIndexScanResult> {
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
  return requestJson<OptimizationProofList>(`${endpoint}/api/app/optimization/proofs?limit=${encodeURIComponent(String(limit))}`);
}

export async function fetchOptimizationProof(endpoint: string, runId: string): Promise<OptimizationProofDetail> {
  return requestJson<OptimizationProofDetail>(`${endpoint}/api/app/optimization/proofs/${encodeURIComponent(runId)}`);
}

export async function fetchAvatars(
  endpoint: string,
  request: { projectPath?: string } = {},
): Promise<AvatarListResult> {
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
  return requestJson(`${endpoint}/api/app/outfit-imports/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchSubAgents(endpoint: string, includeEvents = false): Promise<SubAgentTaskList> {
  const suffix = includeEvents ? "?includeEvents=true" : "";
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
  return requestJson(`${endpoint}/api/app/sub-agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchSubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}`);
}

export async function cancelSubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}

export async function retrySubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
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
  agentName?: string,
): Promise<AgentRuntimeResponse> {
  return requestJson(`${endpoint}/api/app/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agent_name: agentName || "desktop-agent",
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
): Promise<{ ok: boolean; approval?: AgentApproval; execution?: AgentApprovalExecution }> {
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

export async function fetchCheckpoints(
  endpoint: string,
  projectRoot?: string,
): Promise<{ ok: boolean; checkpoints: AgentCheckpoint[]; count: number }> {
  const params = new URLSearchParams();
  if (projectRoot) {
    params.set("projectRoot", projectRoot);
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/checkpoints${suffix}`);
}

export async function previewRestoreCheckpoint(endpoint: string, checkpointId: string): Promise<AgentCheckpointPreview> {
  return requestJson(`${endpoint}/api/app/checkpoints/${encodeURIComponent(checkpointId)}/preview`, {
    method: "POST",
  });
}

export async function requestRestoreCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  return requestJson(`${endpoint}/api/app/checkpoints/${encodeURIComponent(checkpointId)}/restore`, {
    method: "POST",
  });
}

export async function fetchInterruptedApplyRecoveries(
  endpoint: string,
  options: { projectRoot?: string; includeResolved?: boolean } = {},
): Promise<{ ok: boolean; recoveries: InterruptedApplyRecovery[]; count: number }> {
  const params = new URLSearchParams();
  if (options.projectRoot) params.set("projectRoot", options.projectRoot);
  if (options.includeResolved) params.set("includeResolved", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/recoveries${suffix}`);
}

export async function previewInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
): Promise<InterruptedApplyRecoveryPreview> {
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/preview`, {
    method: "POST",
  });
}

export async function requestRestoreInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/restore`, {
    method: "POST",
  });
}

export async function resolveInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
  body: { confirmResolved: boolean; note?: string },
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
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
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/incident-bundle`, {
    method: "POST",
  });
}

export async function fetchAdjustmentCheckpoints(
  endpoint: string,
  options: { kind?: "face" | "shader"; projectRoot?: string; avatarPath?: string; includeDeleted?: boolean } = {},
): Promise<{ ok: boolean; checkpoints: AdjustmentCheckpoint[]; count: number }> {
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
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/apply`, {
    method: "POST",
  });
}

export async function previewAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<AgentCheckpointPreview & { adjustmentCheckpoint?: AdjustmentCheckpoint }> {
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/preview`, {
    method: "POST",
  });
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController();
  const timeoutMs = 30000;
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  const headers = new Headers(init.headers);
  if (appSessionToken && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${appSessionToken}`);
  }
  let response: Response;
  try {
    response = await fetch(url, { ...init, headers, signal: init.signal ?? controller.signal });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw new ApiError(`Request timed out after ${timeoutMs / 1000}s`, 0);
    }
    throw new ApiError(
      `VRCForge runtime is not reachable at ${runtimeOriginFromUrl(url)}. Use Retry to start the local backend, or open Doctor for logs and repair steps.`,
      0,
      cause instanceof Error ? cause.message : String(cause),
    );
  } finally {
    window.clearTimeout(timeout);
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

function runtimeOriginFromUrl(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.host}`;
  } catch {
    return "the configured endpoint";
  }
}
