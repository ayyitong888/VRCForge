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
  roslynFullAutoEverEnabled?: boolean;
  allowWriteRequests: boolean;
  allowRoslynAdvanced: boolean;
  roslynEnvEnabled: boolean;
};

export type AdvancedSettingsState = {
  developerOptionsEnabled: boolean;
  developerOptionsEverEnabled: boolean;
  computerUseEnabled: boolean;
  computerUseEverEnabled: boolean;
  roslynFullAutoEverEnabled: boolean;
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
    choices?: Array<{
      id?: string;
      label: string;
      description?: string;
      value?: string;
    }>;
  };
  choicePrompt?: {
    id?: string;
    question: string;
    choices: Array<{
      id?: string;
      label: string;
      description?: string;
      value?: string;
    }>;
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
  activeDesktopActions?: { actions?: AgentDesktopAction[]; count?: number };
  desktopBridge?: DesktopBridgeStatus;
  goals?: { goals?: AgentGoal[]; count?: number };
  progress?: { items?: AgentProgress[]; count?: number };
  questions?: { questions?: AgentQuestion[]; count?: number };
  memory?: { memories?: AgentMemory[]; count?: number };
};

export type AgentDesktopAction = {
  schema?: string;
  id?: string;
  actionId?: string;
  action?: string;
  status?: string;
  sessionId?: string;
  clientTurnId?: string;
  projectRoot?: string;
  promptSummary?: string;
  params?: Record<string, unknown>;
  paramsSummary?: Record<string, unknown>;
  result?: Record<string, unknown>;
  resultSummary?: Record<string, unknown>;
  error?: string;
  cancelReason?: string;
  bridgeId?: string;
  bridgeName?: string;
  provider?: string;
  bridgeCandidates?: DesktopBridgeInfo[];
  claimRequestId?: string;
  claimedAt?: string;
  createdAt?: string;
  updatedAt?: string;
};

export type DesktopBridgeInfo = {
  id?: string;
  bridgeId?: string;
  name?: string;
  provider?: string;
  capabilities?: string[];
  operations?: string[];
  status?: string;
  registeredAt?: string;
  lastHeartbeatAt?: string;
};

export type DesktopBridgeStatus = {
  ok?: boolean;
  schema?: string;
  connected?: boolean;
  bridges?: DesktopBridgeInfo[];
  count?: number;
  pendingActionCount?: number;
  heartbeatTtlSeconds?: number;
  supportedActions?: string[];
  supportedOperations?: string[];
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
  chatId?: string;
  approvalPolicy?: string;
  createdAt?: string;
  updatedAt?: string;
  wakeAt?: string;
  wakeEveryMinutes?: number;
  lastWokenAt?: string;
  wakeCount?: number;
};

export type AgentProgress = {
  schema?: string;
  id?: string;
  progressId: string;
  title?: string;
  summary?: string;
  status?: "pending" | "in_progress" | "running" | "completed" | "cancelled" | "blocked" | "deleted" | string;
  projectRoot?: string;
  sessionId?: string;
  owner?: string;
  order?: number;
  createdAt?: string;
  updatedAt?: string;
};

export type AgentQuestionOption = {
  id: string;
  label: string;
  value?: string;
  description?: string;
};

export type AgentQuestion = {
  schema?: string;
  id?: string;
  questionId: string;
  header?: string;
  question?: string;
  options?: AgentQuestionOption[];
  status?: "pending" | "answered" | "cancelled" | string;
  answer?: string;
  selectedOptionId?: string;
  projectRoot?: string;
  sessionId?: string;
  owner?: string;
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
  advancedSettings?: AdvancedSettingsState;
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
