import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Archive,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  Eye,
  EyeOff,
  Folder,
  FolderOpen,
  FolderPlus,
  Gauge,
  History,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Moon,
  Pencil,
  Pin,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Send,
  Settings,
  Shield,
  Sparkles,
  Sun,
  TerminalSquare,
  Trash2,
  Globe,
  Wrench,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n, { SUPPORTED_LOCALES, setLocale } from "./i18n";
import {
  FormEvent,
  MouseEvent as ReactMouseEvent,
  ReactNode,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import {
  AgentApproval,
  AdjustmentCheckpoint,
  AgentCheckpoint,
  AgentCheckpointPreview,
  AgentRuntimeResponse,
  AgentReasoningTrace,
  AgentSkill,
  AgentSkillRegistry,
  AgentSkillResult,
  AgentShellResult,
  AvatarListItem,
  AvatarEncryptionBenchmarkRow,
  AvatarEncryptionPlanResult,
  AvatarEncryptionProfileCard,
  InterruptedApplyRecovery,
  InterruptedApplyRecoveryPreview,
  SubAgentTask,
  SubAgentTaskList,
  ApiError,
  AppBootstrap,
  ChatHistoryEntry,
  DoctorCheck,
  DoctorReport,
  DiagnosticsStatus,
  ExternalAgentConnectorClient,
  ExternalAgentConnectorStatus,
  OutfitImportPlanResult,
  OptimizationPlannerReport,
  OptimizationProofDetail,
  OptimizationProofSummary,
  ProjectIndexScanResult,
  SkillPackageEntry,
  SkillPackagePreflight,
  approveAgentApproval,
  checkSkills,
  compactAgentHistory,
  cancelSubAgent,
  createSubAgent,
  createSkill,
  deleteSkill,
  exportSupportBundle,
  exportSkillPackage,
  fetchAdjustmentCheckpoints,
  fetchCheckpoints,
  applyAdjustmentCheckpoint,
  createAdjustmentCheckpoint,
  deleteAdjustmentCheckpoint,
  fetchBootstrap,
  fetchDiagnostics,
  fetchDoctor,
  fetchExternalAgentConnectors,
  fetchInterruptedApplyRecoveries,
  fetchOptimizationPlan,
  fetchOptimizationProof,
  fetchOptimizationProofs,
  fetchSkillPackages,
  fetchSkills,
  AgentSkillCheck,
  ExecutionMode,
  PermissionState,
  blockSkillPackage,
  fetchAgentNotes,
  fetchAvatars,
  fetchChats,
  fetchProjectPrefs,
  fetchProviderModels,
  fetchSubAgent,
  fetchSubAgents,
  installExternalAgentConnector,
  importSkillPackage,
  planOutfitImport,
  planAvatarEncryption,
  preflightSkillPackage,
  ProjectPrefs,
  previewRestoreCheckpoint,
  previewInterruptedApplyRecovery,
  previewAdjustmentCheckpoint,
  rejectAgentApproval,
  requestOptimizationApply,
  requestAvatarEncryptionApply,
  requestRestoreInterruptedApplyRecovery,
  requestOutfitImport,
  requestPackageInstall,
  requestRestoreCheckpoint,
  resolveInterruptedApplyRecovery,
  selectAdjustmentCheckpoint,
  repairUnityMcpBridge,
  revokeSkillPackageSigner,
  retrySubAgent,
  saveChats,
  saveProjectPrefs,
  saveAgentNotes,
  scanProjectIndex,
  sendAgentMessage,
  setSkillPackageSafeMode,
  setSkillPackageEnabled,
  setAppSessionToken,
  testProviderCapability,
  trustSkillPackageSigner,
  updateAdjustmentCheckpoint,
  updateApiConfig,
  updateDiagnostics,
  updateExternalAgentGateway,
  updatePermission,
  updateSkill,
  overwriteAdjustmentCheckpoint,
  exportInterruptedApplyIncidentBundle,
  uninstallExternalAgentConnector,
  uninstallSkillPackage,
} from "./lib/api";
import { cn, formatCount } from "./lib/utils";

type BackendStartResult = {
  endpoint: string;
  app_session_token?: string;
  appSessionToken?: string;
  started: boolean;
  already_running: boolean;
  mode: string;
  message: string;
};

const CONNECTOR_CLIENT_LABELS: Record<ExternalAgentConnectorClient, string> = {
  codexApp: "Codex App",
  codexCli: "Codex CLI",
  claudeCode: "Claude Code CLI",
  claudeCowork: "Claude Cowork App",
};

const OPTIMIZATION_TARGET_PROFILES = [
  { id: "pc_conservative", label: "PC Conservative" },
  { id: "pc_medium", label: "PC Medium" },
  { id: "quest_medium", label: "Quest Medium" },
  { id: "event_light", label: "Event Light" },
  { id: "custom", label: "Custom" },
];

const PROTECTION_PROFILE_FALLBACKS: AvatarEncryptionProfileCard[] = [
  {
    id: "lite",
    label: "Lite",
    title: i18n.t("encryption.profiles.liteTitle"),
    description: i18n.t("encryption.profiles.liteDesc"),
    protection: "Low-overhead encryption.",
    cost: "lowest",
    deviceFit: "Windows / low-end PC",
    applyStatus: "available",
  },
  {
    id: "standard",
    label: i18n.t("package.standard"),
    title: i18n.t("encryption.profiles.standardTitle"),
    description: i18n.t("encryption.profiles.standardDesc"),
    protection: "Recommended encryption.",
    cost: "balanced",
    deviceFit: "PC default",
    recommended: true,
    applyStatus: "available",
  },
  {
    id: "paranoid",
    label: "Paranoid",
    title: i18n.t("encryption.profiles.paranoidTitle"),
    description: i18n.t("encryption.profiles.paranoidDesc"),
    protection: "Highest preview mode.",
    cost: "highest",
    deviceFit: "high-end PC",
    applyStatus: "blocked_until_blendshape_proof",
  },
];

type OptimizationActionOptions = {
  atlasTargetMaterials?: string;
  rendererPath?: string;
  relativeVertexCount?: string;
};

type OptimizationActionCardItem = NonNullable<OptimizationPlannerReport["actionCards"]>[number];
type AdjustmentCheckpointPreview = AgentCheckpointPreview & { adjustmentCheckpoint?: AdjustmentCheckpoint };

function normalizeConnectorClient(client?: string): ExternalAgentConnectorClient | "" {
  if (client === "codex") {
    return "codexApp";
  }
  return client === "codexApp" || client === "codexCli" || client === "claudeCode" || client === "claudeCowork" ? client : "";
}

function formatConnectorActionMessage(client: ExternalAgentConnectorClient, action?: ExternalAgentConnectorStatus["lastConnectorAction"]) {
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

function formatStorageSize(bytes?: number) {
  const value = typeof bytes === "number" && Number.isFinite(bytes) ? Math.max(0, bytes) : 0;
  if (value >= 1024 * 1024 * 1024) {
    return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

type ConversationItem =
  | { id: string; type: "user"; text: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse; elapsedSeconds?: number }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string }
  | { id: string; type: "error"; text: string }
  | { id: string; type: "compact"; text: string }
  | { id: string; type: "subagent"; task: SubAgentTask };

type ActiveView = "chat" | "doctor" | "optimization" | "protection" | "skills" | "checkpoints" | "settings";

type ChatThread = {
  id: string;
  sessionId: string;
  title: string;
  projectPath: string;
  agentName?: string;
  pinned?: boolean;
  archived?: boolean;
  items: ConversationItem[];
};

type ProjectUiPrefs = {
  pinnedPaths: string[];
  aliases: Record<string, string>;
};

type ThemeMode = "light" | "dark";

const ONBOARDING_FLAG_KEY = "vrcforge_onboarded";
const COLLAPSED_PROJECTS_KEY = "vrcforge_collapsed_projects";
const PROJECT_UI_PREFS_KEY = "vrcforge_project_ui_prefs";
const THEME_STORAGE_KEY = "vrcforge_theme";
// 临时对话区折叠状态复用 collapsedProjects 存储；保留 key 不会与真实项目路径冲突。
const TEMP_CHATS_COLLAPSE_KEY = "__temp_chats__";

const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";
const VRCHAT_AVATAR_AGENT_NAMES = [
  "Manuka",
  "Shinano",
  "Kikyo",
  "Moe",
  "Selestia",
  "Milltina",
  "Kipfel",
  "Rurune",
  "Mamehinata",
  "Usasaki",
  "Airi",
  "Maya",
  "Rindo",
  "Karin",
  "Lasyusha",
  "Lime",
  "Chiffon",
  "Chocolat",
  "Mizuki",
  "Sio",
  "Milfy",
  "Mao",
  "Lumina",
  "Leefa",
  "Lunalitt",
  "Rusk",
  "Clonka",
  "Uzuruha",
  "Mitsumame",
  "Ulthara",
  "IsanaiNuku",
  "Yilnel",
  "NoraFirika",
  "IODragonewt",
  "Ortwa",
  "Ricorine",
  "Siska",
  "NoraMiaree",
  "Clara",
  "Korone",
  "Azuki",
  "Miminoko",
  "Nemesis",
  "Elusion",
];

const EXECUTION_MODES: Array<{ value: ExecutionMode; label: string; description: string }> = [
  { value: "approval", label: i18n.t("executionMode.approval"), description: i18n.t("executionMode.approvalDesc") },
  { value: "auto", label: i18n.t("header.autoApproval"), description: i18n.t("executionMode.autoDesc") },
  { value: "roslyn_full_auto", label: i18n.t("header.fullPermission"), description: i18n.t("executionMode.roslynFullAutoDesc") },
];

function executionModeLabel(mode?: string): string {
  return EXECUTION_MODES.find((item) => item.value === mode)?.label || i18n.t("executionMode.approval");
}

function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

function normalizeProjectPathKey(path?: string): string {
  return (path || "").replace(/\//g, "\\").trim().toLowerCase();
}

function isAbsoluteLocalPath(path?: string): boolean {
  const value = (path || "").trim();
  return /^[a-zA-Z]:[\\/]/.test(value) || value.startsWith("\\\\") || value.startsWith("/");
}

function pickSubAgentName(): string {
  const index = Math.floor(Math.random() * VRCHAT_AVATAR_AGENT_NAMES.length);
  return VRCHAT_AVATAR_AGENT_NAMES[index] || "Manuka";
}

function updateSubAgentList(current: SubAgentTaskList | null, task: SubAgentTask): SubAgentTaskList {
  const existing = current?.tasks || [];
  const tasks = [task, ...existing.filter((item) => item.id !== task.id)];
  return {
    ok: true,
    schema: current?.schema || "vrcforge.sub_agent_tasks.v1",
    tasks,
    count: tasks.length,
    roles: current?.roles,
    maxConcurrent: current?.maxConcurrent,
    runningCount: tasks.filter((item) => ["queued", "running", "cancelling"].includes(item.status)).length,
  };
}

function loadProjectUiPrefs(): ProjectUiPrefs {
  try {
    const raw = window.localStorage.getItem(PROJECT_UI_PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || typeof parsed !== "object") {
      return { pinnedPaths: [], aliases: {} };
    }
    const pinnedPaths = Array.isArray(parsed.pinnedPaths)
      ? parsed.pinnedPaths.filter((item: unknown): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const aliases =
      parsed.aliases && typeof parsed.aliases === "object"
        ? Object.fromEntries(
            Object.entries(parsed.aliases).filter(
              (entry): entry is [string, string] => typeof entry[0] === "string" && typeof entry[1] === "string" && entry[1].trim().length > 0,
            ),
          )
        : {};
    return { pinnedPaths, aliases };
  } catch {
    return { pinnedPaths: [], aliases: {} };
  }
}

function loadThemePreference(): ThemeMode {
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    return raw === "dark" || raw === "light" ? raw : "light";
  } catch {
    return "light";
  }
}


export default function App() {
  const { t } = useTranslation();
  const [endpoint, setEndpoint] = useState(FALLBACK_ENDPOINT);
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(null);
  const [backendMessage, setBackendMessage] = useState("starting");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<ThemeMode>(() => loadThemePreference());
  const [showRoslynWarning, setShowRoslynWarning] = useState(false);
  const [pendingMode, setPendingMode] = useState<PermissionState["executionMode"] | null>(null);
  const [input, setInput] = useState("");
  const [chats, setChats] = useState<ChatThread[]>([]);
  const [activeChatId, setActiveChatId] = useState("");
  const [activeProjectPath, setActiveProjectPath] = useState("");
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [chatMenu, setChatMenu] = useState<{ chatId: string; x: number; y: number } | null>(null);
  const [renamingChatId, setRenamingChatId] = useState("");
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTargetId, setDeleteTargetId] = useState("");
  const [showOnboarding, setShowOnboarding] = useState(() => {
    try {
      return window.localStorage.getItem(ONBOARDING_FLAG_KEY) !== "true";
    } catch {
      return false;
    }
  });
  const [onboardingStep, setOnboardingStep] = useState(0);
  const [onboardingMinimized, setOnboardingMinimized] = useState(false);
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [newProjectPath, setNewProjectPath] = useState("");
  const [savingProjectPrefs, setSavingProjectPrefs] = useState(false);
  const [projectModalError, setProjectModalError] = useState("");
  const [projectPrefs, setProjectPrefs] = useState<ProjectPrefs>({ customPaths: [], hiddenPaths: [] });
  const [projectPrefsReady, setProjectPrefsReady] = useState(false);
  const [projectMenu, setProjectMenu] = useState<{ projectPath: string; x: number; y: number } | null>(null);
  const [projectUiPrefs, setProjectUiPrefs] = useState<ProjectUiPrefs>(() => loadProjectUiPrefs());
  const [renamingProjectPath, setRenamingProjectPath] = useState("");
  const [projectRenameDraft, setProjectRenameDraft] = useState("");
  const [projectIndex, setProjectIndex] = useState<ProjectIndexScanResult | null>(null);
  const [projectIndexProject, setProjectIndexProject] = useState("");
  const [loadingProjectIndex, setLoadingProjectIndex] = useState(false);
  const [projectIndexError, setProjectIndexError] = useState("");
  const [optimizationReport, setOptimizationReport] = useState<OptimizationPlannerReport | null>(null);
  const [optimizationTargetProfile, setOptimizationTargetProfile] = useState("pc_conservative");
  const [optimizationAvatarPath, setOptimizationAvatarPath] = useState("");
  const [optimizationAvatars, setOptimizationAvatars] = useState<AvatarListItem[]>([]);
  const [loadingOptimizationAvatars, setLoadingOptimizationAvatars] = useState(false);
  const [optimizationAvatarMessage, setOptimizationAvatarMessage] = useState("");
  const [loadingOptimization, setLoadingOptimization] = useState(false);
  const [optimizationMessage, setOptimizationMessage] = useState("");
  const [requestingOptimizationAction, setRequestingOptimizationAction] = useState("");
  const [requestingOptimizationDependency, setRequestingOptimizationDependency] = useState("");
  const [optimizationActionOptions, setOptimizationActionOptions] = useState<Record<string, OptimizationActionOptions>>({});
  const [optimizationProofs, setOptimizationProofs] = useState<OptimizationProofSummary[]>([]);
  const [selectedOptimizationProof, setSelectedOptimizationProof] = useState<OptimizationProofDetail | null>(null);
  const [loadingOptimizationProofs, setLoadingOptimizationProofs] = useState(false);
  const [optimizationProofMessage, setOptimizationProofMessage] = useState("");
  const [protectionPlan, setProtectionPlan] = useState<AvatarEncryptionPlanResult | null>(null);
  const [protectionProfile, setProtectionProfile] = useState("standard");
  const [protectionAvatarPath, setProtectionAvatarPath] = useState("");
  const [protectionAvatars, setProtectionAvatars] = useState<AvatarListItem[]>([]);
  const [protectionOwnsAssets, setProtectionOwnsAssets] = useState(false);
  const [loadingProtection, setLoadingProtection] = useState(false);
  const [loadingProtectionAvatars, setLoadingProtectionAvatars] = useState(false);
  const [protectionMessage, setProtectionMessage] = useState("");
  const [protectionAvatarMessage, setProtectionAvatarMessage] = useState("");
  const [requestingProtectionFamily, setRequestingProtectionFamily] = useState("");
  const [subAgentList, setSubAgentList] = useState<SubAgentTaskList | null>(null);
  const [loadingSubAgents, setLoadingSubAgents] = useState(false);
  const [subAgentError, setSubAgentError] = useState("");
  const [selectedSubAgent, setSelectedSubAgent] = useState<SubAgentTask | null>(null);
  const [outfitPackagePath, setOutfitPackagePath] = useState("");
  const [outfitImportPlan, setOutfitImportPlan] = useState<OutfitImportPlanResult | null>(null);
  const [outfitImportStatus, setOutfitImportStatus] = useState("");
  const [loadingOutfitImportPlan, setLoadingOutfitImportPlan] = useState(false);
  const [requestingOutfitImport, setRequestingOutfitImport] = useState(false);
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>(() => {
    try {
      const raw = window.localStorage.getItem(COLLAPSED_PROJECTS_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? (parsed as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  });
  const [queued, setQueued] = useState<string[]>([]);
  const [currentTurn, setCurrentTurn] = useState<{ text: string; startedAt: number } | null>(null);
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const [apiProvider, setApiProvider] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiModel, setApiModel] = useState("gemini-2.5-flash");
  const [savingApiConfig, setSavingApiConfig] = useState(false);
  const [modelOptions, setModelOptions] = useState<Array<{ id: string; label: string }>>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelsError, setModelsError] = useState("");
  const [testingProvider, setTestingProvider] = useState("");
  const [providerTestMessage, setProviderTestMessage] = useState("");
  const [skillRegistry, setSkillRegistry] = useState<AgentSkillRegistry | null>(null);
  const [skillCheck, setSkillCheck] = useState<AgentSkillCheck | null>(null);
  const [selectedSkillName, setSelectedSkillName] = useState("");
  const [skillDraft, setSkillDraft] = useState<Partial<AgentSkill>>(emptySkillDraft());
  const [savingSkill, setSavingSkill] = useState(false);
  const [skillPackages, setSkillPackages] = useState<SkillPackageEntry[]>([]);
  const [skillPackageStore, setSkillPackageStore] = useState("");
  const [skillPackageGovernance, setSkillPackageGovernance] = useState<Record<string, unknown>>({});
  const [skillPackageAudit, setSkillPackageAudit] = useState<Array<Record<string, unknown>>>([]);
  const [loadingSkillPackages, setLoadingSkillPackages] = useState(false);
  const [skillPackageMessage, setSkillPackageMessage] = useState("");
  const [skillPackageError, setSkillPackageError] = useState("");
  const [doctorReport, setDoctorReport] = useState<DoctorReport | null>(null);
  const [loadingDoctor, setLoadingDoctor] = useState(false);
  const [doctorMessage, setDoctorMessage] = useState("");
  const [repairingUnityBridge, setRepairingUnityBridge] = useState(false);
  const [startupIssue, setStartupIssue] = useState("");
  const [dismissedDoctorPromptSignature, setDismissedDoctorPromptSignature] = useState("");
  const [diagnosticsStatus, setDiagnosticsStatus] = useState<DiagnosticsStatus | null>(null);
  const [loadingDiagnostics, setLoadingDiagnostics] = useState(false);
  const [exportingSupportBundle, setExportingSupportBundle] = useState(false);
  const [diagnosticsMessage, setDiagnosticsMessage] = useState("");
  const [checkpoints, setCheckpoints] = useState<AgentCheckpoint[]>([]);
  const [interruptedRecoveries, setInterruptedRecoveries] = useState<InterruptedApplyRecovery[]>([]);
  const [adjustmentCheckpoints, setAdjustmentCheckpoints] = useState<AdjustmentCheckpoint[]>([]);
  const [checkpointPreview, setCheckpointPreview] = useState<AgentCheckpointPreview | null>(null);
  const [recoveryPreview, setRecoveryPreview] = useState<InterruptedApplyRecoveryPreview | null>(null);
  const [adjustmentPreview, setAdjustmentPreview] = useState<AdjustmentCheckpointPreview | null>(null);
  const [loadingCheckpoints, setLoadingCheckpoints] = useState(false);
  const [restoringCheckpointId, setRestoringCheckpointId] = useState("");
  const [recoveryBusyId, setRecoveryBusyId] = useState("");
  const [adjustmentBusyId, setAdjustmentBusyId] = useState("");
  const [checkpointMessage, setCheckpointMessage] = useState("");
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const [adjustmentMessage, setAdjustmentMessage] = useState("");
  const [agentNotes, setAgentNotes] = useState("");
  const [agentNotesPath, setAgentNotesPath] = useState("");
  const [agentNotesLoaded, setAgentNotesLoaded] = useState(false);
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const [connectorStatus, setConnectorStatus] = useState<ExternalAgentConnectorStatus | null>(null);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [connectorMessage, setConnectorMessage] = useState("");
  const [checkpointArchiveLimitInput, setCheckpointArchiveLimitInput] = useState("0");
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const projectInitRef = useRef(false);
  const chatsLoadedRef = useRef(false);
  const projectPrefsLoadedRef = useRef(false);
  const chatsRef = useRef<ChatThread[]>([]);
  const queueRef = useRef<string[]>([]);
  const sendingRef = useRef(false);
  const runtimeStartingRef = useRef(false);
  const selectionMenuRef = useRef<HTMLDivElement | null>(null);

  const permission = bootstrap?.permission;
  const apiConfig = bootstrap?.apiConfig;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const hasStartupIssue = startupIssue.trim().length > 0;
  const hasEnvironmentAttention = runtimeConnected && (healthErrors > 0 || healthWarnings > 0);
  const doctorPromptSignature = hasStartupIssue
    ? `startup:${startupIssue.trim()}`
    : `health:${Object.entries(healthComponents)
        .map(([id, component]) => `${id}:${component.status}:${component.message}`)
        .join("|")}`;
  const showDoctorStartupPrompt =
    activeView !== "doctor" && dismissedDoctorPromptSignature !== doctorPromptSignature && (hasStartupIssue || hasEnvironmentAttention);
  const pendingApprovalItems = (bootstrap?.approvals ?? []).filter((item) => item.status === "pending");
  const pendingApprovals = Math.max(bootstrap?.agentHealth.pendingApprovalCount ?? 0, pendingApprovalItems.length);
  const toolCount = bootstrap?.agentManifest.toolCount ?? 0;
  const skills = skillRegistry?.skills ?? bootstrap?.agentManifest.skills ?? [];
  const skillCount = skillRegistry?.count ?? skills.length;
  const slashCommands = useMemo(() => {
    const list: Array<{ name: string; title: string }> = [{ name: "compact", title: t("chat.slashCompact") }];
    for (const skill of skills) {
      if (!skill.name || skill.enabled === false || skill.available === false || skill.userInvocable === false) {
        continue;
      }
      list.push({ name: skill.name, title: skill.title || skill.description || "" });
    }
    return list;
  }, [skills]);
  const projects = bootstrap?.health.projects?.projects ?? [];
  const vrcForgeToolsCount = getHealthDetailNumber(healthComponents.vrcForgeUnityTools?.detail, "vrcForgeToolsCount");
  const vrcForgeSkillsReady = runtimeConnected && healthComponents.vrcForgeUnityTools?.status === "ok" && vrcForgeToolsCount > 0;
  const agentModeLabel = !runtimeConnected
    ? t("agent.modeLabel.notConnected")
    : vrcForgeSkillsReady
      ? t("agent.modeLabel.skillsReady", { count: vrcForgeToolsCount })
      : t("agent.modeLabel.basicMode");
  const apiKeySaved = Boolean(apiConfig?.apiKeyPresent && (apiConfig?.provider || "") === apiProvider);

  const hiddenPathSet = useMemo(
    () => new Set(projectPrefs.hiddenPaths.map(normalizeProjectPathKey)),
    [projectPrefs.hiddenPaths],
  );
  const customPathSet = useMemo(
    () => new Set(projectPrefs.customPaths.map(normalizeProjectPathKey)),
    [projectPrefs.customPaths],
  );
  const pinnedProjectSet = useMemo(
    () => new Set(projectUiPrefs.pinnedPaths.map(normalizeProjectPathKey)),
    [projectUiPrefs.pinnedPaths],
  );
  const projectItems = useMemo(
    () =>
      projects
        .filter((project) => !hiddenPathSet.has(normalizeProjectPathKey(project.path || "")))
        .sort((a, b) => Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(b)))) - Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(a))))),
    [projects, hiddenPathSet, pinnedProjectSet],
  );
  const hiddenProjects = useMemo(
    () => projects.filter((project) => hiddenPathSet.has(normalizeProjectPathKey(project.path || ""))),
    [projects, hiddenPathSet],
  );
  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const conversation = activeChat?.items ?? [];
  const sessionId = activeChat?.sessionId ?? "";
  const subAgentTasks = subAgentList?.tasks ?? [];
  const activeSubAgentTasks = useMemo(() => {
    const parentSession = activeChat?.sessionId || "";
    const projectKeyValue = normalizeProjectPathKey(activeChat?.projectPath || activeProjectPath);
    return subAgentTasks.filter((task) => {
      const sameSession = parentSession && task.parentSessionId === parentSession;
      const sameProject = projectKeyValue && normalizeProjectPathKey(task.projectPath || "") === projectKeyValue;
      return sameSession || sameProject || (!parentSession && !projectKeyValue);
    });
  }, [activeChat?.projectPath, activeChat?.sessionId, activeProjectPath, subAgentTasks]);
  const hasRunningSubAgents = subAgentTasks.some((task) => ["queued", "running", "cancelling"].includes(task.status));
  const activeProjectName =
    projectDisplayName(projectItems.find((project) => normalizeProjectPathKey(projectKey(project)) === normalizeProjectPathKey(activeProjectPath))) ||
    (activeProjectPath ? shortPath(activeProjectPath) : "");
  const temporaryChats = sortChatsByPin(chats.filter((chat) => !chat.projectPath && !chat.archived));
  const projectPromptTitle = activeProjectPath && activeProjectName ? `想在 ${activeProjectName} 里改什么？` : t("chat.promptTitleDefault");
  const emptyProjectState = useMemo(() => {
    if (projectItems.length > 0) {
      return null;
    }
    if (loading && !error) {
      return { name: t("agent.emptyProjectState.scanning"), meta: "wait" };
    }
    if (hasStartupIssue || !runtimeConnected) {
      return { name: t("agent.modeLabel.notConnected"), meta: "retry" };
    }
    if (error) {
      return { name: t("agent.emptyProjectState.refreshFailed"), meta: "retry" };
    }
    return { name: t("agent.emptyProjectState.noUnityProject"), meta: "empty" };
  }, [error, hasStartupIssue, loading, projectItems.length, runtimeConnected]);

  useLayoutEffect(() => {
    const isDark = theme === "dark";
    document.documentElement.classList.toggle("dark", isDark);
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.body.style.colorScheme = theme;
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // Ignore blocked storage; the in-memory theme still works for this run.
    }
  }, [theme]);

  useLayoutEffect(() => {
    const menu = selectionMenuRef.current;
    if (!selectionMenu || !menu) {
      return;
    }

    const positionMenu = () => {
      const margin = 8;
      const gap = 8;
      const rect = menu.getBoundingClientRect();
      const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
      const left = Math.min(Math.max(margin, selectionMenu.x - rect.width / 2), maxLeft);
      const preferredTop = selectionMenu.y - rect.height - gap;
      const fallbackTop = selectionMenu.y + gap;
      const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
      const top = Math.min(Math.max(margin, preferredTop >= margin ? preferredTop : fallbackTop), maxTop);

      menu.style.left = `${left}px`;
      menu.style.top = `${top}px`;
    };

    positionMenu();
    window.addEventListener("resize", positionMenu);
    return () => window.removeEventListener("resize", positionMenu);
  }, [selectionMenu]);

  useEffect(() => {
    // 屏蔽 WebView 默认右键菜单（返回/刷新/另存为等）；输入框保留原生菜单以便粘贴。
    const handler = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && target.closest("input, textarea, [contenteditable='true']")) {
        return;
      }
      event.preventDefault();
    };
    window.addEventListener("contextmenu", handler);
    return () => window.removeEventListener("contextmenu", handler);
  }, []);

  useEffect(() => {
    chatsRef.current = chats;
  }, [chats]);

  useEffect(() => {
    const configuredLimit = connectorStatus?.gateway?.checkpointArchiveMaxSizeMb;
    setCheckpointArchiveLimitInput(typeof configuredLimit === "number" ? String(configuredLimit) : "0");
  }, [connectorStatus?.gateway?.checkpointArchiveMaxSizeMb]);

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSED_PROJECTS_KEY, JSON.stringify(collapsedProjects));
    } catch {
      // 忽略持久化失败
    }
  }, [collapsedProjects]);

  useEffect(() => {
    try {
      window.localStorage.setItem(PROJECT_UI_PREFS_KEY, JSON.stringify(projectUiPrefs));
    } catch {
      // Project display preferences are best-effort local UI state.
    }
  }, [projectUiPrefs]);

  useEffect(() => {
    if (!runtimeConnected || projectPrefsLoadedRef.current) {
      return;
    }
    projectPrefsLoadedRef.current = true;
    void fetchProjectPrefs(endpoint)
      .then((prefs) => {
        setProjectPrefs(prefs);
        setProjectPrefsReady(true);
      })
      .catch(() => {
        setProjectPrefsReady(true);
      });
  }, [runtimeConnected, endpoint]);

  useEffect(() => {
    // 引导最小化期间，当前步骤完成后自动弹回向导。
    if (!showOnboarding || !onboardingMinimized) {
      return;
    }
    const stepStates = [runtimeConnected, Boolean(apiConfig?.apiKeyPresent), projectItems.length > 0];
    if (stepStates[Math.min(onboardingStep, stepStates.length - 1)]) {
      setOnboardingMinimized(false);
    }
  }, [showOnboarding, onboardingMinimized, onboardingStep, runtimeConnected, apiConfig?.apiKeyPresent, projectItems.length]);

  useEffect(() => {
    void startRuntime();
  }, []);

  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [conversation.length]);

  useEffect(() => {
    if (!projectInitRef.current && projectItems.length > 0) {
      projectInitRef.current = true;
      if (!activeProjectPath) {
        setActiveProjectPath(projectKey(projectItems[0]));
      }
    }
  }, [projectItems]);

  useEffect(() => {
    if (!runtimeConnected || chatsLoadedRef.current || !projectPrefsReady) {
      return;
    }
    chatsLoadedRef.current = true;
    void (async () => {
      try {
        const projectPaths = Array.from(
          new Set([
            ...projectItems.map((project) => projectKey(project)).filter(Boolean),
            ...projectPrefs.customPaths.filter(Boolean),
          ]),
        );
        const payload = await fetchChats<unknown>(endpoint, projectPaths);
        const restored = (payload.chats || []).filter(isStoredChat).map((chat) => ({
          id: chat.id,
          sessionId: typeof chat.sessionId === "string" ? chat.sessionId : "",
          title: typeof chat.title === "string" ? chat.title : "",
          projectPath: typeof chat.projectPath === "string" ? chat.projectPath : "",
          agentName: typeof chat.agentName === "string" ? chat.agentName : "",
          pinned: chat.pinned === true,
          archived: chat.archived === true,
          items: chat.items,
        }));
        if (restored.length > 0) {
          setChats((current) => (current.length === 0 ? restored : current));
        }
      } catch {
        // 读取失败时保持空列表，不打断使用；下次启动会重试。
        chatsLoadedRef.current = false;
      }
    })();
  }, [runtimeConnected, endpoint, projectItems, projectPrefs.customPaths, projectPrefsReady]);

  useEffect(() => {
    if (!chatsLoadedRef.current || !runtimeConnected) {
      return;
    }
    const timer = window.setTimeout(() => {
      void saveChats(endpoint, chats).catch(() => undefined);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [chats, runtimeConnected, endpoint]);

  useEffect(() => {
    if (!apiConfig) {
      return;
    }
    setApiProvider(apiConfig.provider || "gemini");
    setApiBaseUrl(apiConfig.base_url || "");
    setApiModel(apiConfig.model || defaultModelForProvider(apiConfig.provider || "gemini"));
  }, [apiConfig?.provider, apiConfig?.base_url, apiConfig?.model]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshSilently();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [endpoint]);

  useEffect(() => {
    if (!runtimeConnected) {
      setSubAgentList(null);
      return;
    }
    void loadSubAgents(false);
  }, [runtimeConnected, endpoint]);

  useEffect(() => {
    if (!runtimeConnected || !hasRunningSubAgents) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadSubAgents(false);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [runtimeConnected, endpoint, hasRunningSubAgents]);

  useEffect(() => {
    if (activeView === "checkpoints" && runtimeConnected) {
      void loadCheckpoints();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (activeView === "doctor" && runtimeConnected) {
      void loadDoctor();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (activeView === "optimization" && runtimeConnected) {
      void loadOptimizationPlan();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath, optimizationTargetProfile]);

  useEffect(() => {
    if (activeView === "optimization" && runtimeConnected) {
      void loadOptimizationAvatars();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (activeView === "protection" && runtimeConnected) {
      void loadProtectionPlan();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath, protectionProfile, protectionOwnsAssets]);

  useEffect(() => {
    if (activeView === "protection" && runtimeConnected) {
      void loadProtectionAvatars();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected || !activeProjectPath) {
      setProjectIndex(null);
      setProjectIndexProject("");
      setProjectIndexError("");
      return;
    }
    const timer = window.setTimeout(() => {
      void scanActiveProjectIndex(activeProjectPath, true);
    }, 650);
    return () => window.clearTimeout(timer);
  }, [runtimeConnected, endpoint, activeProjectPath]);

  async function startRuntime(): Promise<string | null> {
    if (runtimeStartingRef.current) {
      return endpoint;
    }
    runtimeStartingRef.current = true;
    setLoading(true);
    setError("");
    let targetEndpoint = endpoint;
    try {
      if (isTauriRuntime()) {
        await invoke("ensure_agent_notes_file");
        const result = await invoke<BackendStartResult>("start_backend");
        targetEndpoint = result.endpoint;
        setAppSessionToken(result.appSessionToken || result.app_session_token || "");
        setEndpoint(targetEndpoint);
        setBackendMessage(result.message);
        await refreshWithRetry(targetEndpoint);
      } else {
        setBackendMessage("dev");
        await refreshWithRetry(targetEndpoint);
      }
      return targetEndpoint;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      setStartupIssue(message);
      return null;
    } finally {
      runtimeStartingRef.current = false;
      setLoading(false);
    }
  }

  async function refresh(target = endpoint) {
    setError("");
    const payload = await fetchBootstrap(target);
    setBootstrap(payload);
    setStartupIssue("");
  }

  async function refreshSilently(target = endpoint) {
    try {
      const payload = await fetchBootstrap(target);
      setBootstrap(payload);
      setStartupIssue("");
      setError((current) => (current.toLowerCase().includes("fetch") ? "" : current));
    } catch {
      // Keep the current UI usable; explicit retry remains available.
    }
  }

  async function refreshWithRetry(target = endpoint) {
    let lastError: unknown = null;
    for (let attempt = 0; attempt < 16; attempt += 1) {
      try {
        await refresh(target);
        return;
      } catch (cause) {
        lastError = cause;
        await new Promise((resolve) => window.setTimeout(resolve, 450));
      }
    }
    throw lastError instanceof Error ? lastError : new Error(String(lastError || "Failed to fetch runtime bootstrap."));
  }

  async function switchMode(mode: PermissionState["executionMode"], acknowledge = false) {
    if (!permission) {
      return;
    }
    if (mode === "roslyn_full_auto" && !permission.roslynRiskAcknowledged && !acknowledge) {
      setPendingMode(mode);
      setShowRoslynWarning(true);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const payload = await updatePermission(endpoint, mode, acknowledge);
      setBootstrap((current) => (current ? { ...current, permission: payload.permission } : current));
      setShowRoslynWarning(false);
      setPendingMode(null);
      void refreshSilently();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        setPendingMode("roslyn_full_auto");
        setShowRoslynWarning(true);
      } else {
        setError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      setLoading(false);
    }
  }

  async function confirmRoslynWarning() {
    if (!pendingMode) {
      return;
    }
    await switchMode(pendingMode, true);
  }

  function updateChat(chatId: string, updater: (chat: ChatThread) => ChatThread) {
    setChats((list) => list.map((chat) => (chat.id === chatId ? updater(chat) : chat)));
  }

  function appendToChat(chatId: string, item: ConversationItem) {
    updateChat(chatId, (chat) => ({ ...chat, items: [...chat.items, item] }));
  }

  function ensureActiveChat(): string {
    if (activeChat) {
      return activeChat.id;
    }
    const id = `chat-${Date.now()}`;
    setChats((list) => [{ id, sessionId: "", title: "", projectPath: activeProjectPath, items: [] }, ...list]);
    setActiveChatId(id);
    return id;
  }

  async function compactChat() {
    if (!activeChat || activeChat.items.length === 0) {
      setError(t("compact.noContent"));
      return;
    }
    const chatId = activeChat.id;
    const items = activeChat.items;
    setSending(true);
    let summary = "";
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (readyEndpoint) {
          targetEndpoint = readyEndpoint;
        }
      }
      const payload = await compactAgentHistory(targetEndpoint, buildChatHistory(items));
      summary = (payload.summary || "").trim();
      if (summary) {
        summary = `${t("compact.modelSummary", { count: payload.entryCount ?? items.length })}\n${summary}`;
      }
    } catch {
      summary = "";
    } finally {
      setSending(false);
    }
    if (!summary) {
      summary = buildCompactSummary(items);
    }
    updateChat(chatId, (chat) => ({
      ...chat,
      sessionId: "",
      items: [{ id: `compact-${Date.now()}`, type: "compact", text: summary }],
    }));
  }

  async function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    const message = input.trim();
    if (!message) {
      return;
    }
    setError("");
    if (message === "/compact" || message.startsWith("/compact ")) {
      void compactChat();
      setInput("");
      return;
    }
    setInput("");
    if (sendingRef.current) {
      // 正在执行时继续输入：进入队列，当前任务结束后按顺序自动发送（引导对话）。
      queueRef.current.push(message);
      setQueued([...queueRef.current]);
      return;
    }
    const chatId = ensureActiveChat();
    sendingRef.current = true;
    setSending(true);
    try {
      let next: string | undefined = message;
      while (next !== undefined) {
        await runSingleTurn(chatId, next);
        next = queueRef.current.shift();
        setQueued([...queueRef.current]);
      }
    } finally {
      queueRef.current = [];
      setQueued([]);
      sendingRef.current = false;
      setSending(false);
    }
  }

  async function runSingleTurn(chatId: string, message: string) {
    const chat = chatsRef.current.find((item) => item.id === chatId);
    const chatSessionId = chat?.sessionId || "";
    const chatAgentName = chat?.agentName || "desktop-agent";
    const history = chat && chat.items.length > 0 ? buildChatHistory(chat.items) : [];
    const startedAt = Date.now();
    setCurrentTurn({ text: message, startedAt });
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          throw new Error(t("agent.coreDisconnectedSend"));
        }
        targetEndpoint = readyEndpoint;
      }
      const userItem: ConversationItem = { id: `user-${Date.now()}`, type: "user", text: message };
      updateChat(chatId, (current) => ({
        ...current,
        title: current.title || (message.length > 24 ? `${message.slice(0, 24)}…` : message),
        items: [...current.items, userItem],
      }));
      const response = await sendAgentMessage(targetEndpoint, message, chatSessionId || undefined, history, chatAgentName);
      const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      updateChat(chatId, (current) => ({
        ...current,
        sessionId: response.sessionId || response.session_id || current.sessionId,
        items: [...current.items, { id: response.turnId || response.turn_id, type: "agent", response, elapsedSeconds }],
      }));
      await refresh(targetEndpoint);
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      appendToChat(chatId, { id: `error-${Date.now()}`, type: "error", text });
      setError(text);
    } finally {
      setCurrentTurn(null);
    }
  }

  async function approveShell(approvalId: string) {
    setLoading(true);
    setError("");
    try {
      const payload = await approveAgentApproval(endpoint, approvalId);
      const executionResult = payload.execution?.result;
      const shellResult = isAgentShellResult(executionResult) ? executionResult : undefined;
      if (activeChatId && (shellResult || payload.execution?.error)) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          result: shellResult,
          error: payload.execution?.error,
        });
      }
      await refresh();
      const executionRecord = asRecord(payload.execution);
      const executionTargetTool = typeof executionRecord?.targetTool === "string" ? executionRecord.targetTool : "";
      const executionResultRecord = asRecord(executionResult);
      const resolvedRecoveries = executionResultRecord?.resolvedApplyRecoveries;
      const shouldRefreshCheckpoints =
        activeView === "checkpoints" ||
        executionTargetTool === "vrcforge_restore_checkpoint" ||
        executionTargetTool === "vrcforge_resolve_interrupted_apply_recovery" ||
        Array.isArray(resolvedRecoveries);
      if (shouldRefreshCheckpoints) {
        await loadCheckpoints();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function rejectShell(approvalId: string) {
    setLoading(true);
    setError("");
    try {
      await rejectAgentApproval(endpoint, approvalId);
      if (activeChatId) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          error: "rejected",
        });
      }
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  function newConversation(projectPath?: string) {
    setActiveView("chat");
    if (projectPath !== undefined) {
      setActiveProjectPath(projectPath);
    }
    setActiveChatId("");
    setError("");
  }

  function togglePinChat(chatId: string) {
    updateChat(chatId, (chat) => ({ ...chat, pinned: !chat.pinned }));
  }

  function startRenameChat(chat: ChatThread) {
    setRenamingChatId(chat.id);
    setRenameDraft(chat.title || "");
  }

  function commitRenameChat(cancel = false) {
    if (!cancel && renamingChatId) {
      const title = renameDraft.trim();
      if (title) {
        updateChat(renamingChatId, (chat) => ({ ...chat, title }));
      }
    }
    setRenamingChatId("");
    setRenameDraft("");
  }

  function deleteChatPermanently(chatId: string) {
    setChats((list) => list.filter((chat) => chat.id !== chatId));
    if (activeChatId === chatId) {
      setActiveChatId("");
    }
    setDeleteTargetId("");
    setChatMenu(null);
  }

  function bindProject(projectPath: string) {
    setActiveProjectPath(projectPath);
    if (activeChatId) {
      updateChat(activeChatId, (chat) => ({ ...chat, projectPath }));
    }
  }

  function newTemporaryChat() {
    setActiveView("chat");
    setActiveProjectPath("");
    setError("");
    // 折叠状态下新建临时对话自动展开，避免「点了没反应」的错觉。
    setCollapsedProjects((map) => (map[TEMP_CHATS_COLLAPSE_KEY] ? { ...map, [TEMP_CHATS_COLLAPSE_KEY]: false } : map));
    const existingEmpty = chats.find((chat) => !chat.projectPath && !chat.archived && chat.items.length === 0);
    if (existingEmpty) {
      setActiveChatId(existingEmpty.id);
      return;
    }
    const id = `chat-${Date.now()}`;
    setChats((list) => [{ id, sessionId: "", title: "", projectPath: "", items: [] }, ...list]);
    setActiveChatId(id);
  }

  async function loadSubAgents(includeEvents = false) {
    if (!runtimeConnected && !includeEvents) {
      return;
    }
    setLoadingSubAgents(true);
    try {
      const payload = await fetchSubAgents(endpoint, includeEvents);
      setSubAgentList(payload);
      setSubAgentError("");
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingSubAgents(false);
    }
  }

  async function startSubAgentTask(roleOverride?: string) {
    const agentName = pickSubAgentName();
    const projectPath = activeChat?.projectPath || activeProjectPath;
    const hasPackage = outfitPackagePath.trim().length > 0;
    const role = roleOverride || (hasPackage ? "outfit_import_plan_review" : "project_index_review");
    const task =
      role === "outfit_import_plan_review"
        ? "Inspect the selected outfit package and return a supervised import plan summary."
        : role === "validation_triage"
          ? "Run read-only validation triage and summarize findings."
          : "Review the local Unity project index and summarize changed scanner families.";
    setActiveView("chat");
    setError("");
    setSubAgentError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setSubAgentError("Runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await createSubAgent(targetEndpoint, {
        role,
        task,
        displayName: agentName,
        parentSessionId: activeChat?.sessionId || "",
        projectPath,
        params: {
          projectPath,
          packagePath: outfitPackagePath.trim(),
        },
      });
      setSubAgentList((current) => ({
        ok: true,
        schema: current?.schema || "vrcforge.sub_agent_tasks.v1",
        tasks: [payload.task, ...(current?.tasks || []).filter((taskItem) => taskItem.id !== payload.task.id)],
        count: (current?.count || 0) + 1,
        roles: current?.roles,
        maxConcurrent: current?.maxConcurrent,
        runningCount: (current?.runningCount || 0) + 1,
      }));
      void loadSubAgents(false);
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function cancelSubAgentTask(taskId: string) {
    try {
      const payload = await cancelSubAgent(endpoint, taskId);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function retrySubAgentTask(taskId: string) {
    try {
      const payload = await retrySubAgent(endpoint, taskId);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function inspectSubAgentTask(taskId: string) {
    try {
      const payload = await fetchSubAgent(endpoint, taskId);
      setSelectedSubAgent(payload.task);
      setSubAgentList((current) => updateSubAgentList(current, payload.task));
    } catch (cause) {
      setSubAgentError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function acceptSubAgentSummary(task: SubAgentTask) {
    const chatId = ensureActiveChat();
    setActiveView("chat");
    appendToChat(chatId, {
      id: `subagent-${task.id}-${Date.now()}`,
      type: "subagent",
      task,
    });
  }

  function handleConversationMouseUp() {
    window.setTimeout(() => {
      const selection = window.getSelection();
      const text = selection?.toString().trim() ?? "";
      if (!text || !selection || selection.rangeCount === 0) {
        setSelectionMenu(null);
        return;
      }
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      setSelectionMenu({ x: rect.left + rect.width / 2, y: rect.top, text });
    }, 0);
  }

  function clearSelectionMenu() {
    setSelectionMenu(null);
    window.getSelection()?.removeAllRanges();
  }

  function copySelection(text: string) {
    void navigator.clipboard?.writeText(text).catch(() => undefined);
    clearSelectionMenu();
  }

  function addSelectionToComposer(text: string) {
    const quoted = quoteLines(text);
    setInput((current) => (current.trim() ? `${current.trimEnd()}\n\n${quoted}\n` : `${quoted}\n`));
    clearSelectionMenu();
  }

  function askInNewSession(text: string) {
    // 基于选中内容开新会话提问——后续多 agent 的入口。
    const projectPath = activeChat?.projectPath ?? activeProjectPath;
    const id = `chat-${Date.now()}`;
    const agentName = pickSubAgentName();
    setChats((list) => [{ id, sessionId: "", title: agentName, projectPath, agentName, items: [] }, ...list]);
    setActiveChatId(id);
    setActiveView("chat");
    setInput(`${quoteLines(text)}\n`);
    clearSelectionMenu();
  }

  async function persistProjectPrefs(next: ProjectPrefs): Promise<ProjectPrefs | null> {
    setSavingProjectPrefs(true);
    try {
      const saved = await saveProjectPrefs(endpoint, next);
      setProjectPrefs(saved);
      await refreshSilently();
      return saved;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setSavingProjectPrefs(false);
    }
  }

  async function addProjectPath() {
    const path = newProjectPath.trim();
    if (!path || savingProjectPrefs) {
      return;
    }
    setProjectModalError("");
    const saved = await persistProjectPrefs({
      ...projectPrefs,
      customPaths: [...projectPrefs.customPaths, path],
    });
    if (!saved) {
      return;
    }
    const accepted = saved.customPaths.some((item) => item.replace(/\//g, "\\").toLowerCase() === path.replace(/\//g, "\\").toLowerCase());
    if (!accepted) {
      setProjectModalError(t("project.invalidProjectRoot"));
      return;
    }
    setNewProjectPath("");
    setShowProjectModal(false);
    try {
      await refresh();
    } catch {
      // 列表会随下一次轮询更新
    }
    selectProject(path);
  }

  function removeCustomProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      customPaths: projectPrefs.customPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function hideProject(path: string) {
    if (!path) {
      return;
    }
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)), path],
    });
    if (normalizeProjectPathKey(activeProjectPath) === normalizeProjectPathKey(path)) {
      newConversation("");
    }
  }

  function unhideProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function projectDisplayName(project?: { path?: string; name?: string }): string {
    if (!project) {
      return "";
    }
    const key = projectKey(project);
    return projectUiPrefs.aliases[normalizeProjectPathKey(key)] || project.name || project.path || "Unity Project";
  }

  function togglePinProject(path: string) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    setProjectUiPrefs((current) => {
      const pinned = new Set(current.pinnedPaths.map(normalizeProjectPathKey));
      if (pinned.has(key)) {
        pinned.delete(key);
      } else {
        pinned.add(key);
      }
      return { ...current, pinnedPaths: Array.from(pinned) };
    });
  }

  function startRenameProject(path: string) {
    setRenamingProjectPath(path);
    const project = projectItems.find((item) => normalizeProjectPathKey(projectKey(item)) === normalizeProjectPathKey(path));
    setProjectRenameDraft(projectDisplayName(project) || shortPath(path));
  }

  function commitRenameProject(cancel = false) {
    if (!cancel && renamingProjectPath) {
      const key = normalizeProjectPathKey(renamingProjectPath);
      const title = projectRenameDraft.trim();
      setProjectUiPrefs((current) => {
        const aliases = { ...current.aliases };
        if (title) {
          aliases[key] = title;
        } else {
          delete aliases[key];
        }
        return { ...current, aliases };
      });
    }
    setRenamingProjectPath("");
    setProjectRenameDraft("");
  }

  function resolveOpenableProjectPath(path: string): string {
    const raw = path.trim();
    const normalized = normalizeProjectPathKey(raw);
    const candidates: string[] = [raw];
    const pushCandidate = (candidate?: string) => {
      const value = (candidate || "").trim();
      if (value && !candidates.some((item) => normalizeProjectPathKey(item) === normalizeProjectPathKey(value))) {
        candidates.push(value);
      }
    };
    const matchingProject = projectItems.find((project) => {
      const identifiers = [project.path, project.name, projectKey(project), projectDisplayName(project)];
      return identifiers.some((identifier) => normalizeProjectPathKey(identifier || "") === normalized);
    });
    pushCandidate(matchingProject?.path);
    if (normalized && normalized === normalizeProjectPathKey(activeProjectPath)) {
      pushCandidate(activeProjectPath);
    }
    if (!isAbsoluteLocalPath(raw)) {
      const activeMcpProjects = projectItems.filter((project) => Boolean((project as { activeMcp?: boolean }).activeMcp && project.path));
      if (activeMcpProjects.length === 1) {
        pushCandidate(activeMcpProjects[0].path);
      }
    }
    return candidates.find(isAbsoluteLocalPath) || raw;
  }

  async function openProjectFolder(path: string) {
    const targetPath = resolveOpenableProjectPath(path);
    if (!targetPath) {
      return;
    }
    if (!isAbsoluteLocalPath(targetPath)) {
      setError("Cannot open this project because it does not have an absolute Unity project path.");
      return;
    }
    try {
      if (!isTauriRuntime()) {
        throw new Error("Open folder is available in the desktop app.");
      }
      await invoke("open_folder", { path: targetPath });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function archiveProjectChats(path: string, archived: boolean) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    setChats((list) => list.map((chat) => (normalizeProjectPathKey(chat.projectPath) === key ? { ...chat, archived } : chat)));
    if (archived && activeProjectPath && normalizeProjectPathKey(activeProjectPath) === key) {
      setActiveChatId("");
    }
  }

  async function scanActiveProjectIndex(projectPath = activeProjectPath, silent = false) {
    if (!projectPath) {
      return;
    }
    setLoadingProjectIndex(true);
    if (!silent) {
      setProjectIndexError("");
    }
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await scanProjectIndex(targetEndpoint, { projectPath });
      if (normalizeProjectPathKey(projectPath) === normalizeProjectPathKey(activeProjectPath)) {
        setProjectIndex(payload);
        setProjectIndexProject(projectPath);
        setProjectIndexError(payload.ok ? "" : payload.error || "Project index scan failed.");
      }
    } catch (cause) {
      if (normalizeProjectPathKey(projectPath) === normalizeProjectPathKey(activeProjectPath)) {
        setProjectIndexError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      setLoadingProjectIndex(false);
    }
  }

  async function planActiveOutfitImport() {
    const packagePath = outfitPackagePath.trim();
    if (!packagePath) {
      setOutfitImportStatus("Package path is required.");
      return;
    }
    setLoadingOutfitImportPlan(true);
    setOutfitImportStatus("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setOutfitImportStatus("VRCForge runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await planOutfitImport(targetEndpoint, {
        packagePath,
        projectPath: activeProjectPath,
      });
      setOutfitImportPlan(payload);
      setOutfitImportStatus(payload.ok ? "Import plan ready." : payload.error || payload.plan?.error || "Import plan needs review.");
    } catch (cause) {
      setOutfitImportStatus(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOutfitImportPlan(false);
    }
  }

  async function requestActiveOutfitImport() {
    const packagePath = outfitPackagePath.trim();
    if (!packagePath) {
      setOutfitImportStatus("Package path is required.");
      return;
    }
    setRequestingOutfitImport(true);
    setOutfitImportStatus("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setOutfitImportStatus("VRCForge runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestOutfitImport(targetEndpoint, {
        packagePath,
        projectPath: activeProjectPath,
      });
      setOutfitImportStatus(payload.approval ? `Approval queued: ${payload.approval.id}` : "Approval queued.");
      await refresh(targetEndpoint);
    } catch (cause) {
      setOutfitImportStatus(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOutfitImport(false);
    }
  }

  function toggleProjectCollapse(key: string) {
    setCollapsedProjects((current) => ({ ...current, [key]: !current[key] }));
  }

  function finishOnboarding() {
    try {
      window.localStorage.setItem(ONBOARDING_FLAG_KEY, "true");
    } catch {
      // 忽略持久化失败，仅本次会话关闭引导。
    }
    setShowOnboarding(false);
    setOnboardingMinimized(false);
  }

  function restartOnboarding() {
    try {
      window.localStorage.removeItem(ONBOARDING_FLAG_KEY);
    } catch {
      // 忽略
    }
    setActiveView("chat");
    setOnboardingStep(0);
    setOnboardingMinimized(false);
    setShowOnboarding(true);
  }

  function openChat(chat: ChatThread) {
    setActiveView("chat");
    setActiveChatId(chat.id);
    setActiveProjectPath(chat.projectPath);
    setError("");
  }

  function selectProject(projectPath: string) {
    setActiveView("chat");
    setActiveProjectPath(projectPath);
    const latest = chats.find((chat) => normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(projectPath) && !chat.archived);
    setActiveChatId(latest ? latest.id : "");
    setError("");
  }

  async function openDoctor() {
    setActiveView("doctor");
    setError("");
    await loadDoctor();
  }

  async function retryStartupOrHealth() {
    if (hasStartupIssue || !runtimeConnected) {
      await startRuntime();
      return;
    }
    try {
      await refresh();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      setStartupIssue(message);
    }
  }

  async function loadDoctor(target = endpoint) {
    setLoadingDoctor(true);
    setDoctorMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchDoctor(targetEndpoint);
      setDoctorReport(payload);
      setStartupIssue("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingDoctor(false);
    }
  }

  async function openOptimization() {
    setActiveView("optimization");
    setError("");
    await loadOptimizationPlan();
    await loadOptimizationAvatars();
    await loadOptimizationProofs();
  }

  async function loadOptimizationPlan(target = endpoint, profile = optimizationTargetProfile) {
    setLoadingOptimization(true);
    setOptimizationMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchOptimizationPlan(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath: optimizationAvatarPath.trim() || undefined,
        targetProfile: profile,
        includeQuest: true,
      });
      setOptimizationReport(payload);
      setOptimizationMessage(payload.ok ? "Plan refreshed" : "Planner returned warnings");
      void loadOptimizationProofs(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimization(false);
    }
  }

  async function loadOptimizationProofs(target = endpoint, runId?: string) {
    setLoadingOptimizationProofs(true);
    setOptimizationProofMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchOptimizationProofs(targetEndpoint, 8);
      const proofs = payload.proofs || [];
      setOptimizationProofs(proofs);
      const selectedRunId = runId || selectedOptimizationProof?.proof?.runId || proofs[0]?.runId || "";
      if (selectedRunId) {
        const detail = await fetchOptimizationProof(targetEndpoint, selectedRunId);
        setSelectedOptimizationProof(detail);
      } else {
        setSelectedOptimizationProof(null);
      }
      setOptimizationProofMessage(proofs.length ? `${proofs.length} proof run${proofs.length === 1 ? "" : "s"}` : "No optimizer proof runs");
    } catch (cause) {
      setOptimizationProofMessage(cause instanceof Error ? cause.message : String(cause));
      setSelectedOptimizationProof(null);
    } finally {
      setLoadingOptimizationProofs(false);
    }
  }

  async function selectOptimizationProof(runId: string) {
    await loadOptimizationProofs(endpoint, runId);
  }

  async function loadOptimizationAvatars(target = endpoint) {
    setLoadingOptimizationAvatars(true);
    setOptimizationAvatarMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchAvatars(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
      });
      const avatars = (payload.avatars ?? []).filter((item) => Boolean(item.avatarPath));
      setOptimizationAvatars(avatars);
      if (!optimizationAvatarPath.trim() && avatars.length === 1 && avatars[0].avatarPath) {
        setOptimizationAvatarPath(avatars[0].avatarPath);
      }
      if (payload.ok) {
        setOptimizationAvatarMessage(
          avatars.length ? `${avatars.length} avatar${avatars.length === 1 ? "" : "s"} found` : "No scene avatars found",
        );
      } else {
        setOptimizationAvatarMessage("Avatar scan returned warnings");
      }
    } catch (cause) {
      setOptimizationAvatarMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimizationAvatars(false);
    }
  }

  function updateOptimizationActionOption(actionId: string, key: keyof OptimizationActionOptions, value: string) {
    setOptimizationActionOptions((current) => ({
      ...current,
      [actionId]: {
        ...(current[actionId] ?? {}),
        [key]: value,
      },
    }));
  }

  async function requestOptimizationAction(card: NonNullable<OptimizationPlannerReport["actionCards"]>[number]) {
    if (!card.requestTool) {
      return;
    }
    const avatarPath = optimizationAvatarPath.trim();
    if (!avatarPath) {
      setOptimizationMessage("Set avatar path before requesting an optimizer step.");
      return;
    }
    setRequestingOptimizationAction(card.id);
    setOptimizationMessage("");
    try {
      const requestOptions = buildOptimizationRequestOptions(card, optimizationActionOptions[card.id] ?? {});
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestOptimizationApply(targetEndpoint, {
        tool: card.requestTool,
        projectPath: activeProjectPath || undefined,
        avatarPath,
        targetProfile: optimizationTargetProfile,
        options: requestOptions,
        installMissingDependencies: true,
      });
      setOptimizationMessage(payload.approval ? `Approval queued: ${payload.approval.id}` : payload.error || "Request queued.");
      await refreshSilently(targetEndpoint);
      await loadOptimizationPlan(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOptimizationAction("");
    }
  }

  async function requestOptimizationDependencyInstall(dependency: NonNullable<NonNullable<OptimizationPlannerReport["dependencyDoctor"]>["dependencies"]>[number]) {
    const packageId = dependency.packageIds?.find((item) => item);
    if (!packageId) {
      return;
    }
    setRequestingOptimizationDependency(dependency.id || packageId);
    setOptimizationMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestPackageInstall(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        packageId,
        repository: dependency.installMethod?.repository || undefined,
        allowAgentManagedDownload: true,
      });
      setOptimizationMessage(payload.approval ? `Install approval queued: ${payload.approval.id}` : payload.error || "Install request queued.");
      await refreshSilently(targetEndpoint);
      await loadOptimizationPlan(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOptimizationDependency("");
    }
  }

  async function openProtection() {
    setActiveView("protection");
    setError("");
    await loadProtectionPlan();
    await loadProtectionAvatars();
  }

  async function loadProtectionPlan(target = endpoint, profile = protectionProfile) {
    setLoadingProtection(true);
    setProtectionMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await planAvatarEncryption(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath: protectionAvatarPath.trim() || undefined,
        profile,
        protectionProfile: profile,
        confirmCreatorOwnedAssets: protectionOwnsAssets,
      });
      setProtectionPlan(payload);
      const plan = protectionPlanPayload(payload);
      const candidateCount = Number(plan.selectedCandidateCount ?? 0);
      const connector = (plan.externalAddon || {}) as Record<string, unknown>;
      const connectorConfigured = Boolean(connector.configured);
      const writeStatus = String(plan.writeStatus || "");
      setProtectionMessage(
        payload.ok
          ? connectorConfigured && writeStatus !== "blocked"
            ? `${candidateCount} target${candidateCount === 1 ? "" : "s"} ready for private addon request`
            : `${candidateCount} target${candidateCount === 1 ? "" : "s"} found; private addon required`
          : payload.error || "Protection plan returned warnings",
      );
    } catch (cause) {
      setProtectionMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProtection(false);
    }
  }

  async function loadProtectionAvatars(target = endpoint) {
    setLoadingProtectionAvatars(true);
    setProtectionAvatarMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchAvatars(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
      });
      const avatars = (payload.avatars ?? []).filter((item) => Boolean(item.avatarPath));
      setProtectionAvatars(avatars);
      if (!protectionAvatarPath.trim() && avatars.length === 1 && avatars[0].avatarPath) {
        setProtectionAvatarPath(avatars[0].avatarPath);
      }
      setProtectionAvatarMessage(
        payload.ok
          ? avatars.length
            ? `${avatars.length} avatar${avatars.length === 1 ? "" : "s"} found`
            : "No scene avatars found"
          : "Avatar scan returned warnings",
      );
    } catch (cause) {
      setProtectionAvatarMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProtectionAvatars(false);
    }
  }

  async function requestProtectionApply(targetFamily: "liltoon" | "poiyomi") {
    const avatarPath = protectionAvatarPath.trim();
    if (!avatarPath) {
      setProtectionMessage("Set avatar path before requesting protection.");
      return;
    }
    if (!protectionOwnsAssets) {
      setProtectionMessage("Confirm asset ownership before requesting protection.");
      return;
    }
    setRequestingProtectionFamily(targetFamily);
    setProtectionMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestAvatarEncryptionApply(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath,
        profile: protectionProfile,
        protectionProfile,
        targetShaderFamily: targetFamily,
        targetShaderFamilies: [targetFamily],
        confirmCreatorOwnedAssets: true,
      });
      setProtectionMessage(payload.approval ? `Approval queued: ${payload.approval.id}` : payload.error || "Request queued.");
      await refreshSilently(targetEndpoint);
      await loadProtectionPlan(targetEndpoint);
    } catch (cause) {
      setProtectionMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingProtectionFamily("");
    }
  }

  async function repairUnityBridgeFromDoctor(target = endpoint) {
    setRepairingUnityBridge(true);
    setDoctorMessage("");
    setError("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await repairUnityMcpBridge(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        allowUnityRelaunch: true,
        waitSeconds: 120,
        closeTimeoutSeconds: 60,
      });
      const failedPhase = payload.phases.find((phase) => phase.status === "error" || phase.status === "warning");
      const suffix = failedPhase && !payload.ok ? `: ${failedPhase.message}` : "";
      await loadDoctor(targetEndpoint);
      await refreshWithRetry(targetEndpoint);
      setDoctorMessage(payload.ok ? `Unity bridge ${payload.status}` : `Unity bridge needs action${suffix}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRepairingUnityBridge(false);
    }
  }

  async function openSkills() {
    setActiveView("skills");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const [payload] = await Promise.all([fetchSkills(targetEndpoint), loadSkillPackages(targetEndpoint)]);
      setSkillRegistry(payload);
      setSkillCheck(await checkSkills(targetEndpoint));
      if (!selectedSkillName && payload.skills.length > 0) {
        const firstUserSkill = payload.skills.find((skill) => skill.source === "user") || payload.skills[0];
        selectSkill(firstUserSkill);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function loadSkillPackages(target = endpoint) {
    setLoadingSkillPackages(true);
    setSkillPackageError("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return null;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchSkillPackages(targetEndpoint);
      setSkillPackages(payload.installed || []);
      setSkillPackageStore(payload.store || "");
      setSkillPackageGovernance((payload.governance || {}) as Record<string, unknown>);
      setSkillPackageAudit(payload.audit || []);
      return payload;
    } catch (cause) {
      setSkillPackageError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function preflightVskPackage(packagePath: string) {
    setSkillPackageMessage("");
    setSkillPackageError("");
    const payload = await preflightSkillPackage(endpoint, { packagePath });
    setSkillPackageMessage("Package preflight completed");
    return payload;
  }

  async function importVskPackage(packagePath: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await importSkillPackage(endpoint, { packagePath });
      setSkillPackageMessage(payload.changed === false ? "Package already installed" : t("package.messages.packageImported"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      setSkillRegistry(skillsPayload);
      setSkillCheck(await checkSkills(endpoint));
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function exportVskPackage(skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await exportSkillPackage(endpoint, { skillName, outputPath, release, privateKeyPath: privateKeyPath || undefined });
      setSkillPackageMessage(release ? t("package.messages.releaseExported") : t("package.messages.devExported"));
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function setVskPackageEnabled(skillPackageId: string, enabled: boolean) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await setSkillPackageEnabled(endpoint, skillPackageId, { enabled, syncProjectedSkill: true });
      setSkillPackageMessage(enabled ? t("package.messages.packageEnabled") : t("package.messages.packageDisabled"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      setSkillRegistry(skillsPayload);
      setSkillCheck(await checkSkills(endpoint));
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function uninstallVskPackage(skillPackageId: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await uninstallSkillPackage(endpoint, skillPackageId, { removeProjectedSkill: true });
      setSkillPackageMessage(t("package.messages.packageUninstalled"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      setSkillRegistry(skillsPayload);
      setSkillCheck(await checkSkills(endpoint));
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function refreshSkillWorkspaceState() {
    const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
    setSkillRegistry(skillsPayload);
    setSkillCheck(await checkSkills(endpoint));
    await refresh(endpoint);
  }

  async function setVskPackageSafeMode(enabled: boolean, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await setSkillPackageSafeMode(endpoint, { enabled, reason: reason || undefined });
      setSkillPackageMessage(enabled ? t("package.messages.safeModeEnabled") : t("package.labels.safeModeDisabled"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function trustVskPackageSigner(signerFingerprint: string, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await trustSkillPackageSigner(endpoint, { signerFingerprint, reason: reason || undefined });
      setSkillPackageMessage(t("package.messages.signerTrusted"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function revokeVskPackageSigner(signerFingerprint: string, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await revokeSkillPackageSigner(endpoint, { signerFingerprint, reason: reason || undefined });
      setSkillPackageMessage(t("package.messages.signerRevoked"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function blockVskPackage(request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await blockSkillPackage(endpoint, request);
      setSkillPackageMessage(t("package.messages.packageBlocked"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function openSettings() {
    setActiveView("settings");
    setError("");
    setNotesMessage("");
    setDiagnosticsMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const notes = await fetchAgentNotes(targetEndpoint);
      setAgentNotes(notes.content);
      setAgentNotesPath(notes.path);
      setAgentNotesLoaded(true);
      void loadConnectors(targetEndpoint);
      void loadDiagnostics(targetEndpoint);
    } catch (cause) {
      setAgentNotesLoaded(false);
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function loadDiagnostics(target = endpoint) {
    setLoadingDiagnostics(true);
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setDiagnosticsStatus(await fetchDiagnostics(targetEndpoint));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingDiagnostics(false);
    }
  }

  async function setDebugLogging(enabled: boolean) {
    setLoadingDiagnostics(true);
    setDiagnosticsMessage("");
    setError("");
    try {
      const payload = await updateDiagnostics(endpoint, { debugLogging: enabled });
      setDiagnosticsStatus(payload);
      setDiagnosticsMessage(enabled ? "Debug logging enabled" : "Debug logging disabled");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingDiagnostics(false);
    }
  }

  async function createSupportBundle() {
    setExportingSupportBundle(true);
    setDoctorMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await exportSupportBundle(targetEndpoint, { logLimit: 200 });
      setDoctorMessage(`Support bundle exported: ${payload.bundlePath}`);
      setDiagnosticsMessage(`Support bundle exported: ${payload.bundlePath}`);
      void loadDiagnostics(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setExportingSupportBundle(false);
    }
  }

  async function loadConnectors(target = endpoint) {
    setLoadingConnectors(true);
    setConnectorMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setConnectorStatus(await fetchExternalAgentConnectors(targetEndpoint, activeProjectPath || undefined));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
  }

  async function updateGatewaySettings(request: {
    enabled?: boolean;
    allowWriteRequests?: boolean;
    revokeToken?: boolean;
    checkpointArchiveMaxSizeMb?: number;
    deleteCheckpointArchiveIds?: string[];
    checkpointArchiveDirectory?: string;
  }) {
    setLoadingConnectors(true);
    setConnectorMessage("");
    setError("");
    try {
      const payload = await updateExternalAgentGateway(endpoint, request);
      setConnectorStatus(payload);
      const relocate = payload.gateway?.checkpointArchiveRelocate;
      const del = payload.gateway?.checkpointArchiveDelete;
      let message = "Gateway updated";
      if (request.revokeToken) {
        message = "Token revoked";
      } else if (request.checkpointArchiveDirectory !== undefined) {
        message = relocate?.ok
          ? t("settings.checkpointArchiveRelocated", { count: relocate.copiedCount ?? 0 })
          : t("settings.checkpointArchiveRelocateFailed", { reason: relocate?.error || relocate?.code || "" });
      } else if (request.deleteCheckpointArchiveIds !== undefined) {
        message = del?.ok
          ? t("settings.checkpointArchiveDeleted", { count: del.deletedCount ?? 0 })
          : t("settings.checkpointArchiveDeleteFailed", { reason: del?.error || "" });
      } else if (request.checkpointArchiveMaxSizeMb !== undefined) {
        message = t("settings.checkpointArchiveUpdated");
      }
      setConnectorMessage(message);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
  }

  async function saveCheckpointArchiveLimit() {
    const trimmed = checkpointArchiveLimitInput.trim();
    const parsed = Number(trimmed || "0");
    if (!Number.isFinite(parsed) || parsed < 0) {
      setError(t("settings.checkpointArchiveLimitInvalid"));
      return;
    }
    await updateGatewaySettings({ checkpointArchiveMaxSizeMb: Math.round(parsed) });
  }

  async function openCheckpointArchiveFolder(targetPath: string) {
    if (!targetPath) {
      return;
    }
    try {
      if (!isTauriRuntime()) {
        throw new Error("Open folder is available in the desktop app.");
      }
      await invoke("open_local_folder", { path: targetPath });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function pickCheckpointArchiveDirectory(currentPath: string) {
    try {
      if (!isTauriRuntime()) {
        throw new Error("Folder picker is available in the desktop app.");
      }
      const selected = await invoke<string | null>("select_folder", {
        initialPath: currentPath || undefined,
      });
      return selected || "";
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return "";
    }
  }

  async function deleteCheckpointArchives(ids: string[]) {
    if (!ids.length) {
      return;
    }
    await updateGatewaySettings({ deleteCheckpointArchiveIds: ids });
  }

  async function relocateCheckpointArchives(directory: string) {
    const trimmed = directory.trim();
    if (!trimmed) {
      setError(t("settings.checkpointArchiveDirInvalid"));
      return;
    }
    await updateGatewaySettings({ checkpointArchiveDirectory: trimmed });
  }

  async function runConnectorAction(client: ExternalAgentConnectorClient, action: "install" | "uninstall") {
    setLoadingConnectors(true);
    setConnectorMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const request = { client, projectPath: activeProjectPath || undefined };
      const payload =
        action === "install"
          ? await installExternalAgentConnector(targetEndpoint, request)
          : await uninstallExternalAgentConnector(targetEndpoint, request);
      setConnectorStatus(payload);
      setConnectorMessage(formatConnectorActionMessage(client, payload.lastConnectorAction));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
  }

  async function saveNotes(event?: FormEvent) {
    event?.preventDefault();
    if (savingNotes) {
      return;
    }
    setSavingNotes(true);
    setNotesMessage("");
    setError("");
    try {
      const payload = await saveAgentNotes(endpoint, agentNotes);
      setAgentNotesPath(payload.path);
      setNotesMessage(t("settings.saved"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingNotes(false);
    }
  }

  function selectSkill(skill: AgentSkill) {
    setSelectedSkillName(skill.name);
    setSkillDraft({ ...skill });
  }

  function newSkill() {
    setSelectedSkillName("");
    setSkillDraft(emptySkillDraft());
  }

  async function runSkillCheck() {
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setSkillCheck(await checkSkills(targetEndpoint));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function openCheckpoints() {
    setActiveView("checkpoints");
    await loadCheckpoints();
  }

  async function loadCheckpoints(target = endpoint) {
    setLoadingCheckpoints(true);
    try {
      let targetEndpoint = target;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const [payload, recoveryPayload, adjustmentPayload] = await Promise.all([
        fetchCheckpoints(targetEndpoint, activeProjectPath || undefined),
        fetchInterruptedApplyRecoveries(targetEndpoint, { projectRoot: activeProjectPath || undefined }),
        fetchAdjustmentCheckpoints(targetEndpoint, { projectRoot: activeProjectPath || undefined }),
      ]);
      const nextCheckpoints = payload.checkpoints || [];
      const nextRecoveries = recoveryPayload.recoveries || [];
      const nextAdjustmentCheckpoints = adjustmentPayload.checkpoints || [];
      setCheckpoints(nextCheckpoints);
      setInterruptedRecoveries(nextRecoveries);
      setAdjustmentCheckpoints(nextAdjustmentCheckpoints);
      if (checkpointPreview?.checkpoint?.id && !nextCheckpoints.some((item) => item.id === checkpointPreview.checkpoint?.id)) {
        setCheckpointPreview(null);
      }
      const recoveryId = recoveryPreview?.recovery?.id;
      if (recoveryId && !nextRecoveries.some((item) => item.id === recoveryId)) {
        setRecoveryPreview(null);
      }
      const adjustmentId = adjustmentPreview?.adjustmentCheckpoint?.id;
      if (adjustmentId && !nextAdjustmentCheckpoints.some((item) => item.id === adjustmentId)) {
        setAdjustmentPreview(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingCheckpoints(false);
    }
  }

  async function previewCheckpoint(checkpointId: string) {
    setLoadingCheckpoints(true);
    setCheckpointMessage("");
    try {
      const payload = await previewRestoreCheckpoint(endpoint, checkpointId);
      setCheckpointPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Checkpoint preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingCheckpoints(false);
    }
  }

  async function restoreCheckpoint(checkpointId: string) {
    setRestoringCheckpointId(checkpointId);
    setCheckpointMessage("");
    setError("");
    try {
      const payload = await requestRestoreCheckpoint(endpoint, checkpointId);
      if (payload.status === "pending") {
        setCheckpointMessage("Restore approval is pending.");
      } else if (payload.ok) {
        setCheckpointMessage("Checkpoint restored.");
      } else {
        setCheckpointMessage(String(payload.error || "Restore request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRestoringCheckpointId("");
    }
  }

  async function previewRecovery(recoveryId: string) {
    setRecoveryBusyId(recoveryId);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await previewInterruptedApplyRecovery(endpoint, recoveryId);
      setRecoveryPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Recovery preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function restoreRecovery(recoveryId: string) {
    setRecoveryBusyId(`restore:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await requestRestoreInterruptedApplyRecovery(endpoint, recoveryId);
      if (payload.status === "pending") {
        setRecoveryMessage("Restore approval is pending.");
      } else if (payload.ok) {
        setRecoveryMessage("Interrupted write restored.");
      } else {
        setRecoveryMessage(String(payload.error || "Restore request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function exportRecoveryBundle(recoveryId: string) {
    setRecoveryBusyId(`bundle:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await exportInterruptedApplyIncidentBundle(endpoint, recoveryId);
      if (payload.ok) {
        setRecoveryMessage(`Incident bundle exported: ${payload.bundlePath || payload.path || "-"}`);
      } else {
        setRecoveryMessage(String(payload.error || "Incident bundle export failed."));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function resolveRecovery(recoveryId: string) {
    if (!window.confirm("Mark this interrupted write as resolved without restoring its checkpoint?")) {
      return;
    }
    setRecoveryBusyId(`resolve:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await resolveInterruptedApplyRecovery(endpoint, recoveryId, {
        confirmResolved: true,
        note: "Resolved from the desktop Checkpoints view.",
      });
      if (payload.status === "pending") {
        setRecoveryMessage("Resolve approval is pending.");
      } else if (payload.ok) {
        setRecoveryMessage("Interrupted write resolved.");
      } else {
        setRecoveryMessage(String(payload.error || "Resolve request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function createAdjustment(kind: "face" | "shader") {
    setAdjustmentBusyId(`create:${kind}`);
    setAdjustmentMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await createAdjustmentCheckpoint(targetEndpoint, {
        kind,
        projectRoot: activeProjectPath || undefined,
        label: kind === "face" ? "Face adjustment" : "Shader adjustment",
      });
      setAdjustmentMessage(`${kind === "face" ? t("checkpoint.face") : t("checkpoint.shader")} checkpoint created.`);
      setAdjustmentPreview(payload.baseCheckpoint ? { ok: true, checkpoint: payload.baseCheckpoint, adjustmentCheckpoint: payload.checkpoint } : null);
      await loadCheckpoints(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function renameAdjustment(checkpoint: AdjustmentCheckpoint) {
    const currentLabel = checkpoint.label || checkpoint.id;
    const nextLabel = window.prompt("Checkpoint label", currentLabel);
    if (nextLabel === null || nextLabel.trim() === currentLabel) {
      return;
    }
    setAdjustmentBusyId(checkpoint.id);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await updateAdjustmentCheckpoint(endpoint, checkpoint.id, { label: nextLabel.trim() });
      setAdjustmentMessage("Adjustment checkpoint updated.");
      setAdjustmentPreview((previous) =>
        previous?.adjustmentCheckpoint?.id === checkpoint.id
          ? { ...previous, adjustmentCheckpoint: payload.checkpoint }
          : previous,
      );
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function previewAdjustment(checkpointId: string) {
    setAdjustmentBusyId(checkpointId);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await previewAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Adjustment preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function selectAdjustment(checkpointId: string, slot: "A" | "B") {
    setAdjustmentBusyId(`${checkpointId}:${slot}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await selectAdjustmentCheckpoint(endpoint, checkpointId, { slot });
      setAdjustmentMessage(`Selected ${slot}.`);
      setAdjustmentPreview((previous) =>
        previous?.adjustmentCheckpoint?.id === checkpointId
          ? { ...previous, adjustmentCheckpoint: payload.checkpoint }
          : previous,
      );
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function applyAdjustment(checkpointId: string) {
    setAdjustmentBusyId(`apply:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await applyAdjustmentCheckpoint(endpoint, checkpointId);
      if (payload.status === "pending") {
        setAdjustmentMessage("Apply approval is pending.");
      } else if (payload.ok) {
        setAdjustmentMessage("Adjustment checkpoint applied.");
      } else {
        setAdjustmentMessage(String(payload.error || "Apply request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function overwriteAdjustment(checkpointId: string) {
    if (!window.confirm("Overwrite this adjustment checkpoint with the current project state?")) {
      return;
    }
    setAdjustmentBusyId(`overwrite:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await overwriteAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentMessage("Adjustment checkpoint overwritten.");
      setAdjustmentPreview(payload.baseCheckpoint ? { ok: true, checkpoint: payload.baseCheckpoint, adjustmentCheckpoint: payload.checkpoint } : null);
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function removeAdjustment(checkpointId: string) {
    if (!window.confirm("Delete this adjustment checkpoint?")) {
      return;
    }
    setAdjustmentBusyId(`delete:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      await deleteAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentMessage("Adjustment checkpoint deleted.");
      if (adjustmentPreview?.adjustmentCheckpoint?.id === checkpointId) {
        setAdjustmentPreview(null);
      }
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function saveSkill(event?: FormEvent) {
    event?.preventDefault();
    if (!skillDraft.name || savingSkill) {
      return;
    }
    setSavingSkill(true);
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = selectedSkillName
        ? await updateSkill(targetEndpoint, selectedSkillName, skillDraft)
        : await createSkill(targetEndpoint, skillDraft);
      setSkillRegistry(payload);
      setSkillCheck(await checkSkills(targetEndpoint));
      setSelectedSkillName(payload.skill.name);
      setSkillDraft({ ...payload.skill });
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingSkill(false);
    }
  }

  async function removeSelectedSkill() {
    if (!selectedSkillName || savingSkill) {
      return;
    }
    setSavingSkill(true);
    setError("");
    try {
      const payload = await deleteSkill(endpoint, selectedSkillName);
      setSkillRegistry(payload);
      setSkillCheck(await checkSkills(endpoint));
      setSelectedSkillName("");
      setSkillDraft(emptySkillDraft());
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingSkill(false);
    }
  }

  async function saveApiProvider(event?: FormEvent) {
    event?.preventDefault();
    if (!apiProvider || !apiModel || (providerNeedsApiKey(apiProvider) && !apiKey.trim() && !apiKeySaved)) {
      return;
    }
    setSavingApiConfig(true);
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      await updateApiConfig(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
      });
      setApiKey("");
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingApiConfig(false);
    }
  }

  function handleProviderChange(provider: string) {
    setApiProvider(provider);
    setApiModel(defaultModelForProvider(provider));
    setApiBaseUrl(defaultBaseUrlForProvider(provider));
    setModelOptions([]);
    setModelsError("");
  }

  async function loadModels() {
    if (loadingModels) {
      return;
    }
    setLoadingModels(true);
    setModelsError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setModelsError(t("provider.coreDisconnectedModels"));
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchProviderModels(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
      });
      const models = payload.models || [];
      setModelOptions(models);
      if (models.length === 0) {
        setModelsError(t("provider.noModelsReturned"));
      } else if (!models.some((item) => item.id === apiModel)) {
        setApiModel(payload.selectedModel && models.some((item) => item.id === payload.selectedModel) ? payload.selectedModel : models[0].id);
      }
    } catch (cause) {
      setModelOptions([]);
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingModels(false);
    }
  }

  async function runProviderTest(capability: "text" | "structured" | "vision") {
    if (testingProvider) {
      return;
    }
    setTestingProvider(capability);
    setProviderTestMessage("");
    setModelsError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setModelsError("Runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await testProviderCapability(targetEndpoint, {
        provider: apiProvider,
        api_key: apiKey.trim(),
        base_url: apiBaseUrl.trim(),
        model: apiModel.trim(),
        capability,
      });
      setProviderTestMessage(`${payload.capability}: ${payload.status} - ${payload.message}`);
      if (!payload.ok && payload.status !== "skipped") {
        setModelsError(payload.message);
      }
    } catch (cause) {
      setModelsError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setTestingProvider("");
    }
  }

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground">
      <div className="grid h-screen grid-cols-[64px_minmax(0,1fr)] md:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="sidebar-scrollbar flex h-screen min-w-0 flex-col overflow-y-auto border-r border-border bg-sidebar px-2 py-4 md:px-4 max-md:[&_nav_button]:justify-center max-md:[&_nav_button]:px-0 max-md:[&_nav_span]:hidden">
          <div className="flex h-10 items-center justify-center gap-3 px-2 md:justify-start">
            <Bot className="h-5 w-5 shrink-0 text-primary" />
            <div className="hidden truncate text-base font-semibold md:block">VRCForge</div>
          </div>

          <nav className="mt-5 space-y-1">
            <button
              onClick={newTemporaryChat}
              aria-label={t("sidebar.tempChat")}
              title={t("sidebar.tempChat")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "chat" && !activeProjectPath && !activeChat
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("sidebar.tempChat")}</span>
            </button>
            <button
              aria-label={t("sidebar.newProject")}
              title={t("sidebar.newProject")}
              onClick={() => {
                setProjectModalError("");
                setShowProjectModal(true);
              }}
              className="flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <FolderPlus className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("sidebar.newProject")}</span>
            </button>
            <button
              onClick={() => void openDoctor()}
              aria-label={t("sidebar.doctor")}
              title={t("sidebar.doctor")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "doctor"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("sidebar.doctor")}</span>
            </button>
            <button
              onClick={() => void openOptimization()}
              aria-label={t("sidebar.optimization")}
              title={t("sidebar.optimization")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "optimization"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Gauge className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("sidebar.optimization")}</span>
            </button>
            <button
              onClick={() => void openProtection()}
              aria-label={t("encryption.protection")}
              title={t("encryption.protection")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "protection"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("encryption.protection")}</span>
            </button>
            <button
              onClick={() => void openSkills()}
              aria-label={t("sidebar.skills")}
              title={t("sidebar.skills")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "skills"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Wrench className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("sidebar.skills")}</span>
            </button>
            <button
              onClick={() => void openCheckpoints()}
              aria-label={t("checkpoint.checkpoints")}
              title={t("checkpoint.checkpoints")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "checkpoints"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <History className="h-4 w-4 shrink-0" />
              <span className="truncate">{t("checkpoint.checkpoints")}</span>
            </button>
          </nav>

          <SidebarSection title={t("sidebar.projects")}>
            {projectItems.length > 0 ? (
              projectItems.map((project, index) => {
                const key = projectKey(project) || `project-${index}`;
                const projectChats = sortChatsByPin(chats.filter((chat) => normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(key) && !chat.archived));
                const collapsed = Boolean(collapsedProjects[key]);
                return (
                  <div key={key} className="min-w-0">
                    <SidebarProject
                      name={projectDisplayName(project)}
                      meta={project.editorVersion || project.unityVersion || (project.sources ?? []).join("+")}
                      active={activeView === "chat" && normalizeProjectPathKey(key) === normalizeProjectPathKey(activeProjectPath)}
                      collapsed={collapsed}
                      hasChats={projectChats.length > 0}
                      pinned={pinnedProjectSet.has(normalizeProjectPathKey(key))}
                      renaming={renamingProjectPath === key}
                      renameDraft={projectRenameDraft}
                      onRenameChange={setProjectRenameDraft}
                      onRenameCommit={commitRenameProject}
                      onToggleCollapse={() => toggleProjectCollapse(key)}
                      onClick={() => selectProject(key)}
                      onOpenMenu={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                        setProjectMenu({ projectPath: key, x: event.clientX, y: event.clientY });
                      }}
                      onContextMenu={(event) => {
                        event.preventDefault();
                        setProjectMenu({ projectPath: key, x: event.clientX, y: event.clientY });
                      }}
                    />
                    {collapsed
                      ? null
                      : projectChats.map((chat) => (
                      <SidebarChat
                        key={chat.id}
                        title={chat.title || t("sidebar.newChat")}
                        active={activeView === "chat" && chat.id === activeChatId}
                        indent
                        pinned={chat.pinned}
                        renaming={renamingChatId === chat.id}
                        renameDraft={renameDraft}
                        onRenameChange={setRenameDraft}
                        onRenameCommit={commitRenameChat}
                        onClick={() => openChat(chat)}
                        onTogglePin={() => togglePinChat(chat.id)}
                        onDelete={() => setDeleteTargetId(chat.id)}
                        onContextMenu={(event) => {
                          event.preventDefault();
                          setChatMenu({ chatId: chat.id, x: event.clientX, y: event.clientY });
                        }}
                      />
                    ))}
                  </div>
                );
              })
            ) : (
              <SidebarProject name={emptyProjectState?.name || t("agent.emptyProjectState.noUnityProject")} meta={emptyProjectState?.meta} active />
            )}
          </SidebarSection>

          <SidebarSection
            title={t("sidebar.chats")}
            collapsed={Boolean(collapsedProjects[TEMP_CHATS_COLLAPSE_KEY])}
            onToggleCollapse={() => toggleProjectCollapse(TEMP_CHATS_COLLAPSE_KEY)}
          >
            {temporaryChats.length > 0 ? (
              temporaryChats.map((chat) => (
                <SidebarChat
                  key={chat.id}
                  title={chat.title || t("sidebar.newChat")}
                  active={activeView === "chat" && chat.id === activeChatId}
                  pinned={chat.pinned}
                  renaming={renamingChatId === chat.id}
                  renameDraft={renameDraft}
                  onRenameChange={setRenameDraft}
                  onRenameCommit={commitRenameChat}
                  onClick={() => openChat(chat)}
                  onTogglePin={() => togglePinChat(chat.id)}
                  onDelete={() => setDeleteTargetId(chat.id)}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setChatMenu({ chatId: chat.id, x: event.clientX, y: event.clientY });
                  }}
                />
              ))
            ) : (
              <div className="px-3 py-1 text-xs text-muted-foreground/70">{t("sidebar.noTempChats")}</div>
            )}
          </SidebarSection>

          <div className="mt-auto">
            <button
              onClick={() => void openSettings()}
              aria-label={t("sidebar.settings")}
              title={t("sidebar.settings")}
              className={cn(
                "flex h-10 w-full min-w-0 items-center justify-center gap-3 rounded-md px-0 text-left text-sm transition-colors md:justify-start md:px-3",
                activeView === "settings"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Settings className="h-4 w-4 shrink-0" />
              <span className="hidden truncate md:inline">{t("sidebar.settings")}</span>
            </button>
          </div>
        </aside>

        <section className="flex h-screen min-w-0 flex-col overflow-hidden bg-workspace">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-3 md:px-6">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="truncate text-muted-foreground">{activeProjectPath ? activeProjectName : t("sidebar.tempChat")}</span>
              <span className="text-muted-foreground">/</span>
              <span className="truncate font-medium">
                {activeView === "doctor"
                  ? t("sidebar.doctor")
                  : activeView === "optimization"
                    ? t("sidebar.optimization")
                    : activeView === "protection"
                      ? t("encryption.protection")
                  : activeView === "skills"
                    ? t("sidebar.skills")
                    : activeView === "settings"
                      ? t("sidebar.settings")
                      : activeChat
                        ? activeChat.title || t("header.currentSession")
                        : t("header.newTask")}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {permission?.roslynFullAuto ? (
                <Badge tone="danger">
                  <AlertTriangle className="mr-1 h-3.5 w-3.5 shrink-0" />
                  {t("header.fullPermission")}
                </Badge>
              ) : permission?.executionMode === "auto" ? (
                <Badge tone="warn">{t("header.autoApproval")}</Badge>
              ) : null}
              <StatusChip ok={runtimeConnected} label={runtimeConnected ? t("header.coreOnline") : t("header.coreOffline")} />
              <Badge tone={pendingApprovals > 0 ? "warn" : "muted"}>{formatCount(pendingApprovals)} {t("header.pendingApprovals")}</Badge>
              <Button variant="ghost" className="h-9 w-9 px-0" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>
          </header>

          {showDoctorStartupPrompt ? (
            <div className="mx-auto mt-3 w-full max-w-4xl px-4">
              <div className="flex min-w-0 items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-100">
                <AlertTriangle className="h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="font-medium">
                    {hasStartupIssue ? t("header.startupIssueDetected") : t("header.envNeedsAttention")}
                  </div>
                  <div className="break-words text-amber-900/80 dark:text-amber-100/80">
                    {hasStartupIssue
                      ? "Open Doctor to diagnose VRCForge startup, runtime, Unity bridge, provider, gateway, skills, and checkpoint checks."
                      : `Doctor can review ${healthErrors} error${healthErrors === 1 ? "" : "s"} and ${healthWarnings} warning${
                          healthWarnings === 1 ? "" : "s"
                        } across the VRCForge environment.`}
                  </div>
                  {hasStartupIssue ? <div className="break-words text-amber-900/70 dark:text-amber-100/70">{startupIssue}</div> : null}
                </div>
                <Button variant="outline" className="h-7 shrink-0 px-2 text-xs" onClick={() => void openDoctor()} disabled={loadingDoctor}>
                  Doctor
                </Button>
                <Button variant="ghost" className="h-7 shrink-0 px-2 text-xs" onClick={() => void retryStartupOrHealth()} disabled={loading}>
                  {loading ? "Retrying" : t("doctor.retry")}
                </Button>
                <Button
                  variant="ghost"
                  className="h-7 shrink-0 px-2 text-xs"
                  onClick={() => setDismissedDoctorPromptSignature(doctorPromptSignature)}
                >
                  Dismiss
                </Button>
              </div>
            </div>
          ) : null}

          {error && !showDoctorStartupPrompt ? (
            <div className="mx-auto mt-3 w-full max-w-4xl px-4">
              <div className="flex items-center gap-3 rounded-md border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive/75">
                <span className="break-words">{error}</span>
                <Button
                  variant="ghost"
                  className="ml-auto h-7 shrink-0 px-2 text-xs text-destructive/80 hover:bg-destructive/10"
                  onClick={() => void startRuntime()}
                  disabled={loading}
                >
                  {loading ? t("header.reconnecting") : t("header.reconnect")}
                </Button>
              </div>
            </div>
          ) : null}

          {activeView === "doctor" ? (
            <DoctorWorkspace
              report={doctorReport}
              loading={loadingDoctor}
              message={doctorMessage}
              repairingUnityBridge={repairingUnityBridge}
              exportingSupportBundle={exportingSupportBundle}
              onRefresh={() => void loadDoctor()}
              onRepairUnityBridge={() => void repairUnityBridgeFromDoctor()}
              onOpenSettings={() => void openSettings()}
              onExportSupportBundle={() => void createSupportBundle()}
              onCopy={() => {
                if (!doctorReport) {
                  return;
                }
                void navigator.clipboard
                  .writeText(JSON.stringify(doctorReport, null, 2))
                  .then(() => setDoctorMessage(t("doctor.copiedSummary")))
                  .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
              }}
            />
          ) : activeView === "skills" ? (
            <SkillsWorkspace
              skills={skills}
              skillCount={skillCount}
              skillCheck={skillCheck}
              selectedSkillName={selectedSkillName}
              draft={skillDraft}
              saving={savingSkill}
              onSelect={selectSkill}
              onNew={newSkill}
              onCheck={runSkillCheck}
              onDraftChange={setSkillDraft}
              onSave={saveSkill}
              onDelete={removeSelectedSkill}
              packages={skillPackages}
              packageStore={skillPackageStore}
              packagesLoading={loadingSkillPackages}
              packageMessage={skillPackageMessage}
              packageError={skillPackageError}
              packageGovernance={skillPackageGovernance}
              packageAudit={skillPackageAudit}
              onRefreshPackages={() => void loadSkillPackages()}
              onPreflightPackage={preflightVskPackage}
              onImportPackage={importVskPackage}
              onExportPackage={exportVskPackage}
              onSetPackageEnabled={setVskPackageEnabled}
              onUninstallPackage={uninstallVskPackage}
              onSetSafeMode={setVskPackageSafeMode}
              onTrustSigner={trustVskPackageSigner}
              onRevokeSigner={revokeVskPackageSigner}
              onBlockPackage={blockVskPackage}
            />
          ) : activeView === "checkpoints" ? (
            <CheckpointWorkspace
              checkpoints={checkpoints}
              interruptedRecoveries={interruptedRecoveries}
              adjustmentCheckpoints={adjustmentCheckpoints}
              selectedProjectPath={activeProjectPath}
              preview={checkpointPreview}
              recoveryPreview={recoveryPreview}
              adjustmentPreview={adjustmentPreview}
              loading={loadingCheckpoints}
              restoringId={restoringCheckpointId}
              recoveryBusyId={recoveryBusyId}
              adjustmentBusyId={adjustmentBusyId}
              message={checkpointMessage}
              recoveryMessage={recoveryMessage}
              adjustmentMessage={adjustmentMessage}
              onRefresh={() => void loadCheckpoints()}
              onPreview={previewCheckpoint}
              onRestore={restoreCheckpoint}
              onPreviewRecovery={previewRecovery}
              onRestoreRecovery={restoreRecovery}
              onExportRecoveryBundle={exportRecoveryBundle}
              onResolveRecovery={resolveRecovery}
              onCreateAdjustment={createAdjustment}
              onPreviewAdjustment={previewAdjustment}
              onSelectAdjustment={selectAdjustment}
              onApplyAdjustment={applyAdjustment}
              onOverwriteAdjustment={overwriteAdjustment}
              onRenameAdjustment={renameAdjustment}
              onDeleteAdjustment={removeAdjustment}
            />
          ) : activeView === "protection" ? (
            <ProtectionWorkspace
              plan={protectionPlan}
              selectedProjectPath={activeProjectPath}
              avatarPath={protectionAvatarPath}
              avatars={protectionAvatars}
              profile={protectionProfile}
              ownsAssets={protectionOwnsAssets}
              loading={loadingProtection}
              loadingAvatars={loadingProtectionAvatars}
              message={protectionMessage}
              avatarMessage={protectionAvatarMessage}
              requestingFamily={requestingProtectionFamily}
              onAvatarPathChange={setProtectionAvatarPath}
              onProfileChange={setProtectionProfile}
              onOwnsAssetsChange={setProtectionOwnsAssets}
              onRefresh={() => void loadProtectionPlan()}
              onRefreshAvatars={() => void loadProtectionAvatars()}
              onRequestApply={(family) => void requestProtectionApply(family)}
            />
          ) : activeView === "optimization" ? (
            <OptimizationWorkspace
              report={optimizationReport}
              proofs={optimizationProofs}
              selectedProof={selectedOptimizationProof}
              endpoint={endpoint}
              permission={permission}
              selectedProjectPath={activeProjectPath}
              avatarPath={optimizationAvatarPath}
              avatars={optimizationAvatars}
              targetProfile={optimizationTargetProfile}
              loading={loadingOptimization}
              loadingProofs={loadingOptimizationProofs}
              loadingAvatars={loadingOptimizationAvatars}
              message={optimizationMessage}
              proofMessage={optimizationProofMessage}
              avatarMessage={optimizationAvatarMessage}
              actionOptions={optimizationActionOptions}
              requestingActionId={requestingOptimizationAction}
              requestingDependencyId={requestingOptimizationDependency}
              onAvatarPathChange={setOptimizationAvatarPath}
              onTargetProfileChange={setOptimizationTargetProfile}
              onRefresh={() => void loadOptimizationPlan()}
              onRefreshProofs={() => void loadOptimizationProofs()}
              onSelectProof={(runId) => void selectOptimizationProof(runId)}
              onRefreshAvatars={() => void loadOptimizationAvatars()}
              onActionOptionChange={updateOptimizationActionOption}
              onRequestAction={(card) => void requestOptimizationAction(card)}
              onRequestDependency={(dependency) => void requestOptimizationDependencyInstall(dependency)}
            />
          ) : activeView === "settings" ? (
            <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-10">
              <div className="mx-auto w-full max-w-3xl">
                <h1 className="text-2xl font-semibold tracking-tight">{t("sidebar.settings")}</h1>
                <p className="mt-1 text-sm text-muted-foreground">{t("settings.subtitle")}</p>

                <section className="mt-10">
                  <div className="flex min-w-0 items-center gap-2">
                    <h2 className="truncate text-base font-semibold">{t("settings.permissionMode")}</h2>
                    <Badge tone={permission?.roslynFullAuto ? "danger" : permission?.autoApprove ? "warn" : "muted"} className="shrink-0">
                      {t("settings.currentMode", { mode: executionModeLabel(permission?.executionMode) })}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">{t("settings.permissionModeDescription")}</p>
                  <div className="mt-4 grid gap-3">
                    {EXECUTION_MODES.map((mode) => (
                      <button
                        key={mode.value}
                        type="button"
                        disabled={loading || !runtimeConnected}
                        onClick={() => void switchMode(mode.value)}
                        className={cn(
                          "grid min-w-0 gap-1 rounded-xl border px-4 py-3 text-left transition-colors disabled:opacity-60",
                          permission?.executionMode === mode.value
                            ? "border-primary bg-primary/5"
                            : "border-border hover:border-primary/40 hover:bg-muted/60",
                        )}
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="truncate text-sm font-medium">{mode.label}</span>
                          {mode.value === "roslyn_full_auto" ? (
                            <Badge tone="danger" className="shrink-0">
                              {t("settings.highRisk")}
                            </Badge>
                          ) : null}
                          {permission?.executionMode === mode.value ? (
                            <Check className="ml-auto h-4 w-4 shrink-0 text-primary" />
                          ) : null}
                        </div>
                        <div className="text-xs text-muted-foreground">{mode.description}</div>
                      </button>
                    ))}
                  </div>
                  {permission?.roslynRiskAcknowledged ? (
                    <div className="mt-3 text-xs text-muted-foreground">{t("settings.roslynConfirmed")}</div>
                  ) : null}
                </section>

                <section className="mt-12">
                  <h2 className="text-base font-semibold">{t("settings.onboarding")}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t("settings.onboardingDesc")}</p>
                  <div className="mt-4">
                    <Button type="button" variant="outline" onClick={restartOnboarding}>
                      <RefreshCw className="mr-1 h-4 w-4" />
                      {t("settings.restartOnboarding")}
                    </Button>
                  </div>
                </section>

                <section className="mt-12">
                  <h2 className="text-base font-semibold">
                    <Globe className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
                    {t("settings.language")}
                  </h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t("settings.languageDesc")}</p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {SUPPORTED_LOCALES.map((loc) => (
                      <button
                        key={loc.code}
                        type="button"
                        onClick={() => setLocale(loc.code)}
                        className={cn(
                          "rounded-md border px-3 py-1.5 text-sm font-medium transition-colors",
                          i18n.language === loc.code
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border bg-card text-foreground hover:bg-accent",
                        )}
                      >
                        {loc.label}
                      </button>
                    ))}
                  </div>
                </section>

                <section className="mt-12">
                  <h2 className="text-base font-semibold">{t("settings.modelProvider")}</h2>
                  <p className="mt-1 text-sm text-muted-foreground">{t("settings.providerDesc")}</p>
                  <div className="mt-4">
                    <ProviderSetup
                      provider={apiProvider}
                      apiKey={apiKey}
                      baseUrl={apiBaseUrl}
                      model={apiModel}
                      saving={savingApiConfig}
                      models={modelOptions}
                      loadingModels={loadingModels}
                      modelsError={modelsError}
                      testingProvider={testingProvider}
                      providerTestMessage={providerTestMessage}
                      keySaved={apiKeySaved}
                      onLoadModels={() => void loadModels()}
                      onTestProvider={(capability) => void runProviderTest(capability)}
                      onProviderChange={handleProviderChange}
                      onApiKeyChange={setApiKey}
                      onBaseUrlChange={setApiBaseUrl}
                      onModelChange={setApiModel}
                      onSubmit={saveApiProvider}
                    />
                  </div>
                </section>

                <section className="mt-12">
                  <div className="flex min-w-0 items-center gap-2">
                    <h2 className="truncate text-base font-semibold">{t("settings.diagnostics")}</h2>
                    {diagnosticsMessage ? (
                      <Badge tone="ok" className="shrink-0">
                        {diagnosticsMessage}
                      </Badge>
                    ) : null}
                  </div>
                  <div className="mt-4 rounded-lg border border-border bg-card p-4">
                    <div className="flex min-w-0 flex-wrap items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">Debug logging</div>
                        <div className="mt-1 truncate text-xs text-muted-foreground">
                          {diagnosticsStatus?.debugLogging ? "Recording local API, MCP, agent, checkpoint, and runtime interactions" : t("connector.off")}
                        </div>
                      </div>
                      <Badge tone={diagnosticsStatus?.debugLogging ? "warn" : "muted"} className="shrink-0">
                        {diagnosticsStatus?.debugLogging ? "Debug on" : "Debug off"}
                      </Badge>
                      <Button
                        type="button"
                        variant={diagnosticsStatus?.debugLogging ? "outline" : "primary"}
                        disabled={loadingDiagnostics}
                        onClick={() => void setDebugLogging(!diagnosticsStatus?.debugLogging)}
                      >
                        {loadingDiagnostics ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        {diagnosticsStatus?.debugLogging ? "Turn off" : "Turn on"}
                      </Button>
                      <Button type="button" variant="outline" disabled={exportingSupportBundle} onClick={() => void createSupportBundle()}>
                        {exportingSupportBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                        Export Bundle
                      </Button>
                    </div>
                    {diagnosticsStatus?.logsDir ? <div className="mt-3 truncate text-xs text-muted-foreground/70">{diagnosticsStatus.logsDir}</div> : null}
                  </div>
                </section>

                <section className="mt-12">
                  <CheckpointStoragePanel
                    status={connectorStatus}
                    loading={loadingConnectors}
                    isDesktop={isTauriRuntime()}
                    limitInput={checkpointArchiveLimitInput}
                    onLimitInputChange={setCheckpointArchiveLimitInput}
                    onSaveLimit={() => void saveCheckpointArchiveLimit()}
                    onOpenFolder={(targetPath) => void openCheckpointArchiveFolder(targetPath)}
                    onPickDirectory={pickCheckpointArchiveDirectory}
                    onDeleteSelected={(ids) => void deleteCheckpointArchives(ids)}
                    onRelocate={(directory) => void relocateCheckpointArchives(directory)}
                  />
                </section>

                <section className="mt-12">
                  <ExternalAgentConnectorsPanel
                    status={connectorStatus}
                    loading={loadingConnectors}
                    message={connectorMessage}
                    selectedProjectPath={activeProjectPath}
                    onRefresh={() => void loadConnectors()}
                    onToggleGateway={(enabled) => void updateGatewaySettings({ enabled })}
                    onToggleWriteRequests={(allowWriteRequests) => void updateGatewaySettings({ allowWriteRequests })}
                    onRevoke={() => void updateGatewaySettings({ revokeToken: true })}
                    onInstall={(client) => void runConnectorAction(client, "install")}
                    onUninstall={(client) => void runConnectorAction(client, "uninstall")}
                    onCopy={(text, label) => {
                      void navigator.clipboard
                        .writeText(text)
                        .then(() => setConnectorMessage(`${label} copied`))
                        .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
                    }}
                  />
                </section>

                <section className="mt-12 pb-6">
                  <div className="flex min-w-0 items-center gap-2">
                    <h2 className="truncate text-base font-semibold">{t("settings.customInstructions")}</h2>
                    {notesMessage ? (
                      <Badge tone="ok" className="shrink-0">
                        {notesMessage}
                      </Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t("settings.customInstructionsDesc")}
                  </p>
                  {agentNotesPath ? <p className="mt-1 truncate text-xs text-muted-foreground/70">{agentNotesPath}</p> : null}
                  <form onSubmit={saveNotes} className="mt-4">
                    <textarea
                      value={agentNotes}
                      onChange={(event) => {
                        setAgentNotes(event.target.value);
                        setNotesMessage("");
                      }}
                      disabled={!agentNotesLoaded}
                      placeholder={agentNotesLoaded ? t("settings.customInstructionsPlaceholder") : t("settings.customInstructionsDisabled")}
                      className="min-h-56 w-full resize-y rounded-xl border border-border bg-background px-4 py-3 text-sm leading-relaxed outline-none focus:border-primary disabled:bg-muted"
                    />
                    <div className="mt-3 flex justify-end">
                      <Button type="submit" disabled={savingNotes || !agentNotesLoaded}>
                        {savingNotes ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        {t("common.save")}
                      </Button>
                    </div>
                  </form>
                </section>
              </div>
            </div>
          ) : conversation.length === 0 ? (
            <div className="flex min-h-0 flex-1 items-center justify-center p-8">
              <div className="w-full max-w-4xl">
                {projectPromptTitle ? <h1 className="mb-5 text-center text-2xl font-semibold tracking-normal">{projectPromptTitle}</h1> : null}
                <ProjectIndexPanel
                  projectPath={activeProjectPath}
                  projectName={activeProjectName}
                  result={normalizeProjectPathKey(projectIndexProject) === normalizeProjectPathKey(activeProjectPath) ? projectIndex : null}
                  loading={loadingProjectIndex}
                  error={projectIndexError}
                  onScan={() => void scanActiveProjectIndex(activeProjectPath)}
                  onReview={() => void startSubAgentTask("project_index_review")}
                />
                <OutfitImportPanel
                  projectPath={activeProjectPath}
                  packagePath={outfitPackagePath}
                  result={outfitImportPlan}
                  status={outfitImportStatus}
                  loading={loadingOutfitImportPlan}
                  requesting={requestingOutfitImport}
                  onPackagePathChange={setOutfitPackagePath}
                  onPlan={() => void planActiveOutfitImport()}
                  onRequest={() => void requestActiveOutfitImport()}
                  onReview={() => void startSubAgentTask("outfit_import_plan_review")}
                />
                <SubAgentPanel
                  tasks={activeSubAgentTasks}
                  loading={loadingSubAgents}
                  error={subAgentError}
                  selected={selectedSubAgent}
                  onInspect={(taskId) => void inspectSubAgentTask(taskId)}
                  onCancel={(taskId) => void cancelSubAgentTask(taskId)}
                  onRetry={(taskId) => void retrySubAgentTask(taskId)}
                  onAccept={acceptSubAgentSummary}
                  onCloseInspect={() => setSelectedSubAgent(null)}
                />
                <Composer
                  input={input}
                  setInput={setInput}
                  sending={sending}
                  permission={permission}
                  statusLabel={agentModeLabel}
                  projectLabel={activeProjectPath ? activeProjectName : ""}
                  onSubmit={submitMessage}
                  onSwitchMode={switchMode}
                  commands={slashCommands}
                  projects={projectItems.map((project) => ({
                    key: projectKey(project),
                    name: project.name || shortPath(project.path || ""),
                  }))}
                  onBindProject={bindProject}
                  queuedCount={queued.length}
                />
              </div>
            </div>
          ) : (
            <>
              <div
                className="min-h-0 flex-1 overflow-auto px-6 py-8"
                onMouseUp={handleConversationMouseUp}
                onScroll={() => (selectionMenu ? setSelectionMenu(null) : undefined)}
              >
                <div className="mx-auto max-w-4xl space-y-5">
                  <ProjectIndexPanel
                    projectPath={activeProjectPath}
                    projectName={activeProjectName}
                    result={normalizeProjectPathKey(projectIndexProject) === normalizeProjectPathKey(activeProjectPath) ? projectIndex : null}
                    loading={loadingProjectIndex}
                    error={projectIndexError}
                    onScan={() => void scanActiveProjectIndex(activeProjectPath)}
                    onReview={() => void startSubAgentTask("project_index_review")}
                  />
                  <OutfitImportPanel
                    projectPath={activeProjectPath}
                    packagePath={outfitPackagePath}
                    result={outfitImportPlan}
                    status={outfitImportStatus}
                    loading={loadingOutfitImportPlan}
                    requesting={requestingOutfitImport}
                    onPackagePathChange={setOutfitPackagePath}
                    onPlan={() => void planActiveOutfitImport()}
                    onRequest={() => void requestActiveOutfitImport()}
                    onReview={() => void startSubAgentTask("outfit_import_plan_review")}
                  />
                  <SubAgentPanel
                    tasks={activeSubAgentTasks}
                    loading={loadingSubAgents}
                    error={subAgentError}
                    selected={selectedSubAgent}
                    onInspect={(taskId) => void inspectSubAgentTask(taskId)}
                    onCancel={(taskId) => void cancelSubAgentTask(taskId)}
                    onRetry={(taskId) => void retrySubAgentTask(taskId)}
                    onAccept={acceptSubAgentSummary}
                    onCloseInspect={() => setSelectedSubAgent(null)}
                  />
                  {conversation.map((item) => (
                    <ConversationCard key={item.id} item={item} onOpenSettings={() => void openSettings()} />
                  ))}
                  {sending && currentTurn ? (
                    <RunningIndicator startedAt={currentTurn.startedAt} text={currentTurn.text} provider={apiProvider} model={apiModel} />
                  ) : null}
                  {queued.map((text, index) => (
                    <div key={`queued-${index}`} className="flex justify-end opacity-60">
                      <div className="max-w-[78%] rounded-2xl bg-primary/80 px-4 py-3 text-sm text-primary-foreground">
                        <div className="mb-1 flex items-center gap-1 text-[10px] opacity-90">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          {t("chat.queued")}
                        </div>
                        <p className="whitespace-pre-wrap break-words">{text}</p>
                      </div>
                    </div>
                  ))}
                  <div ref={conversationEndRef} />
                </div>
              </div>
              {pendingApprovalItems.length > 0 ? (
                <div className="max-h-[40vh] shrink-0 overflow-auto border-t border-amber-500/20 bg-amber-500/5 px-6 py-3">
                  <div className="mx-auto max-w-4xl space-y-3">
                    {pendingApprovalItems.map((approval) => (
                      <ApprovalCard
                        key={approval.id}
                        approval={approval}
                        loading={loading}
                        onApprove={approveShell}
                        onReject={rejectShell}
                      />
                    ))}
                  </div>
                </div>
              ) : null}
              <div className="shrink-0 border-t border-border bg-workspace/95 px-6 py-4">
                <div className="mx-auto max-w-4xl">
                  <Composer
                    input={input}
                    setInput={setInput}
                    sending={sending}
                    permission={permission}
                    statusLabel={agentModeLabel}
                    projectLabel={activeProjectPath ? activeProjectName : ""}
                    onSubmit={submitMessage}
                    onSwitchMode={switchMode}
                    commands={slashCommands}
                    compact
                    projects={projectItems.map((project) => ({
                      key: projectKey(project),
                      name: project.name || shortPath(project.path || ""),
                    }))}
                    onBindProject={bindProject}
                    queuedCount={queued.length}
                  />
                </div>
              </div>
            </>
          )}
          {activeView !== "chat" && pendingApprovalItems.length > 0 ? (
            <div className="max-h-[40vh] shrink-0 overflow-auto border-t border-amber-500/20 bg-amber-500/5 px-6 py-3">
              <div className="mx-auto max-w-4xl space-y-3">
                {pendingApprovalItems.map((approval) => (
                  <ApprovalCard
                    key={approval.id}
                    approval={approval}
                    loading={loading}
                    onApprove={approveShell}
                    onReject={rejectShell}
                  />
                ))}
              </div>
            </div>
          ) : null}
        </section>
      </div>

      {showOnboarding && !onboardingMinimized
        ? (() => {
            const steps = [
              {
                title: t("onboarding.step1Title"),
                done: runtimeConnected,
                doneDesc: t("onboarding.step1DoneDesc"),
                todoDesc: t("onboarding.step1TodoDesc"),
                action: (
                  <Button variant="outline" disabled={loading} onClick={() => void startRuntime()}>
                    <RefreshCw className="mr-1 h-4 w-4" />
                    {loading ? t("onboarding.connecting") : t("onboarding.retryConnection")}
                  </Button>
                ),
              },
              {
                title: t("onboarding.step2Title"),
                done: Boolean(apiConfig?.apiKeyPresent),
                doneDesc: t("onboarding.step2DoneDesc"),
                todoDesc: t("onboarding.step2TodoDesc"),
                action: (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setOnboardingMinimized(true);
                      void openSettings();
                    }}
                  >
                    <Settings className="mr-1 h-4 w-4" />
                    {t("onboarding.goToSettings")}
                  </Button>
                ),
              },
              {
                title: t("onboarding.step3Title"),
                done: projectItems.length > 0,
                doneDesc: t("onboarding.step3DoneDesc"),
                todoDesc: t("onboarding.step3TodoDesc"),
                action: (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setOnboardingMinimized(true);
                      setProjectModalError("");
                      setShowProjectModal(true);
                    }}
                  >
                    <FolderPlus className="mr-1 h-4 w-4" />
                    {t("sidebar.newProject")}
                  </Button>
                ),
              },
            ];
            const step = steps[Math.min(onboardingStep, steps.length - 1)];
            const isLast = onboardingStep >= steps.length - 1;
            return (
              <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
                <section className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-panel">
                  <div className="flex min-w-0 items-center gap-3">
                    <Sparkles className="h-5 w-5 shrink-0 text-primary" />
                    <h2 className="truncate text-lg font-semibold">{t("onboarding.welcome")}</h2>
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      {t("onboarding.stepProgress", { current: onboardingStep + 1, total: steps.length })}
                    </span>
                  </div>
                  <div className="mt-4 flex items-center gap-2">
                    {steps.map((item, index) => (
                      <div
                        key={item.title}
                        className={cn(
                          "h-1.5 flex-1 rounded-full transition-colors",
                          index < onboardingStep || item.done
                            ? "bg-primary"
                            : index === onboardingStep
                              ? "bg-primary/40"
                              : "bg-muted",
                        )}
                      />
                    ))}
                  </div>
                  <div className="mt-5 rounded-xl border border-border px-5 py-4">
                    <div className="flex min-w-0 items-center gap-2">
                      {step.done ? (
                        <Check className="h-4 w-4 shrink-0 text-primary" />
                      ) : (
                        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />
                      )}
                      <div className="truncate text-sm font-medium">{step.title}</div>
                      <Badge tone={step.done ? "ok" : "muted"} className="ml-auto shrink-0">
                        {step.done ? t("onboarding.done") : t("onboarding.detecting")}
                      </Badge>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">{step.done ? step.doneDesc : step.todoDesc}</p>
                    {!step.done ? <div className="mt-4">{step.action}</div> : null}
                  </div>
                  <div className="mt-6 flex items-center gap-3">
                    <Button variant="ghost" className="text-muted-foreground" onClick={finishOnboarding}>
                      {t("onboarding.skipOnboarding")}
                    </Button>
                    <div className="ml-auto flex gap-3">
                      {onboardingStep > 0 ? (
                        <Button variant="outline" onClick={() => setOnboardingStep((value) => Math.max(0, value - 1))}>
                          {t("onboarding.prevStep")}
                        </Button>
                      ) : null}
                      <Button
                        disabled={!step.done}
                        onClick={() => {
                          if (isLast) {
                            finishOnboarding();
                          } else {
                            setOnboardingStep((value) => value + 1);
                          }
                        }}
                      >
                        {isLast ? t("onboarding.startUsing") : t("onboarding.nextStep")}
                      </Button>
                    </div>
                  </div>
                </section>
              </div>
            );
          })()
        : null}

      {showOnboarding && onboardingMinimized ? (
        <button
          type="button"
          onClick={() => setOnboardingMinimized(false)}
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2.5 text-sm shadow-panel transition-colors hover:bg-muted"
        >
          <Sparkles className="h-4 w-4 shrink-0 text-primary" />
          <span>{t("onboarding.continueOnboarding", { step: onboardingStep + 1 })}</span>
        </button>
      ) : null}

      {showProjectModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
          <section className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-border bg-card p-6 shadow-panel">
            <div className="flex min-w-0 items-center gap-2">
              <FolderPlus className="h-5 w-5 shrink-0 text-primary" />
              <h2 className="truncate text-lg font-semibold">{t("onboarding.step3Title")}</h2>
              <button
                type="button"
                className="ml-auto shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                onClick={() => {
                  setShowProjectModal(false);
                  setProjectModalError("");
                }}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="app-scrollbar mt-4 min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
              <div>
                <div className="mb-2 text-xs font-medium text-muted-foreground">{t("project.scannedProjects")}</div>
                {projectItems.length > 0 ? (
                  <div className="space-y-1">
                    {projectItems.map((project) => {
                      const key = projectKey(project);
                      const isCustom = customPathSet.has((project.path || "").toLowerCase());
                      return (
                        <div key={key} className="group flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-muted">
                          <button
                            type="button"
                            className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
                            onClick={() => {
                              selectProject(key);
                              setShowProjectModal(false);
                              setProjectModalError("");
                            }}
                          >
                            <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
                            <span className="min-w-0 truncate">{project.name || shortPath(project.path || "")}</span>
                            <span className="min-w-0 truncate text-xs text-muted-foreground">{project.path}</span>
                          </button>
                          {isCustom ? (
                            <button
                              type="button"
                              title={t("project.removeFromList")}
                              disabled={savingProjectPrefs}
                              className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-colors hover:bg-background hover:text-destructive group-hover:opacity-100"
                              onClick={() => removeCustomProject(project.path || "")}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
                    {t("project.noProjectsHint")}
                  </p>
                )}
              </div>
              {hiddenProjects.length > 0 ? (
                <div>
                  <div className="mb-2 text-xs font-medium text-muted-foreground">{t("project.hiddenProjects")}</div>
                  <div className="space-y-1">
                    {hiddenProjects.map((project) => (
                      <div key={project.path} className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground">
                        <EyeOff className="h-4 w-4 shrink-0" />
                        <span className="min-w-0 flex-1 truncate">{project.name || shortPath(project.path || "")}</span>
                        <Button
                          type="button"
                          variant="outline"
                          className="h-7 shrink-0 px-2 text-xs"
                          disabled={savingProjectPrefs}
                          onClick={() => unhideProject(project.path || "")}
                        >
                          <Eye className="mr-1 h-3.5 w-3.5" />
                          {t("project.restore")}
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              <div>
                <div className="mb-2 text-xs font-medium text-muted-foreground">{t("project.addProjectFolder")}</div>
                <div className="flex gap-2">
                  <input
                    value={newProjectPath}
                    onChange={(event) => {
                      setNewProjectPath(event.target.value);
                      setProjectModalError("");
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                        event.preventDefault();
                        void addProjectPath();
                      }
                    }}
                    placeholder={t("project.pathPlaceholder")}
                    className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                  />
                  <Button type="button" disabled={savingProjectPrefs || !newProjectPath.trim()} onClick={() => void addProjectPath()}>
                    {savingProjectPrefs ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    {t("common.add")}
                  </Button>
                </div>
                {projectModalError ? <p className="mt-2 text-xs text-destructive">{projectModalError}</p> : null}
                <p className="mt-2 text-xs text-muted-foreground">{t("project.pathHint")}</p>
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {projectMenu
        ? (() => {
            const menuPath = projectMenu.projectPath;
            const menuKey = normalizeProjectPathKey(menuPath);
            const isCustom = customPathSet.has(menuKey);
            const collapsed = Boolean(collapsedProjects[menuPath]);
            const pinned = pinnedProjectSet.has(normalizeProjectPathKey(menuPath));
            const projectChatCount = chats.filter((chat) => normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(menuPath) && !chat.archived).length;
            const archivedChatCount = chats.filter((chat) => normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(menuPath) && chat.archived).length;
            return (
              <>
                <div
                  className="fixed inset-0 z-40"
                  onClick={() => setProjectMenu(null)}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setProjectMenu(null);
                  }}
                />
                <div
                  className="fixed z-50 w-56 rounded-lg border border-border bg-card p-1.5 shadow-panel"
                  style={{
                    left: Math.min(projectMenu.x, window.innerWidth - 240),
                    top: Math.min(projectMenu.y, window.innerHeight - 260),
                  }}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      togglePinProject(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    <Pin className={cn("h-4 w-4 shrink-0", pinned ? "text-primary" : "")} />
                    {pinned ? t("project.unpinProject") : t("project.pinProject")}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      void openProjectFolder(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    <FolderOpen className="h-4 w-4 shrink-0" />
                    {t("project.openInExplorer")}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      newConversation(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    <Plus className="h-4 w-4 shrink-0" />
                    {t("project.newChatInProject")}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      startRenameProject(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    <Pencil className="h-4 w-4 shrink-0" />
                    {t("project.renameProject")}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      toggleProjectCollapse(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    {collapsed ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                    {collapsed ? t("project.expandChats") : t("project.collapseChats")}
                  </button>
                  {projectChatCount > 0 || archivedChatCount > 0 ? (
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                      onClick={() => {
                        archiveProjectChats(menuPath, projectChatCount > 0);
                        setProjectMenu(null);
                      }}
                    >
                      <Archive className="h-4 w-4 shrink-0" />
                      {projectChatCount > 0 ? t("project.archiveChats") : t("project.restoreArchived")}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      hideProject(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    <EyeOff className="h-4 w-4 shrink-0" />
                    {t("project.hideProject")}
                  </button>
                  {isCustom ? (
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive/10"
                      onClick={() => {
                        removeCustomProject(menuPath);
                        setProjectMenu(null);
                      }}
                    >
                      <Trash2 className="h-4 w-4 shrink-0" />
                      {t("project.removeProject")}
                    </button>
                  ) : null}
                </div>
              </>
            );
          })()
        : null}

      {selectionMenu ? (
        <div
          ref={selectionMenuRef}
          className="fixed z-50 flex w-max max-w-[calc(100vw-1rem)] flex-wrap items-center gap-0.5 rounded-lg border border-border bg-card p-1 shadow-panel"
          style={{ left: 0, top: 0 }}
          onMouseUp={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => copySelection(selectionMenu.text)}
          >
            <Copy className="h-3.5 w-3.5 shrink-0" />
            {t("contextMenu.copy")}
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => addSelectionToComposer(selectionMenu.text)}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            {t("contextMenu.addToChat")}
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => askInNewSession(selectionMenu.text)}
          >
            <Bot className="h-3.5 w-3.5 shrink-0" />
            {t("contextMenu.askInNewSession")}
          </button>
        </div>
      ) : null}

      {chatMenu
        ? (() => {
            const menuChat = chats.find((chat) => chat.id === chatMenu.chatId);
            if (!menuChat) {
              return null;
            }
            return (
              <>
                <div
                  className="fixed inset-0 z-40"
                  onClick={() => setChatMenu(null)}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setChatMenu(null);
                  }}
                />
                <div
                  className="fixed z-50 w-44 rounded-lg border border-border bg-card p-1.5 shadow-panel"
                  style={{
                    left: Math.min(chatMenu.x, window.innerWidth - 190),
                    top: Math.min(chatMenu.y, window.innerHeight - 140),
                  }}
                >
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      togglePinChat(menuChat.id);
                      setChatMenu(null);
                    }}
                  >
                    <Pin className="h-4 w-4 shrink-0" />
                    {menuChat.pinned ? t("contextMenu.unpinChat") : t("contextMenu.pinChat")}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => {
                      startRenameChat(menuChat);
                      setChatMenu(null);
                    }}
                  >
                    <Pencil className="h-4 w-4 shrink-0" />
                    {t("contextMenu.renameChat")}
                  </button>
                  <button
                    type="button"
                    className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-destructive transition-colors hover:bg-destructive/10"
                    onClick={() => {
                      setDeleteTargetId(menuChat.id);
                      setChatMenu(null);
                    }}
                  >
                    <Trash2 className="h-4 w-4 shrink-0" />
                    {t("contextMenu.permanentDelete")}
                  </button>
                </div>
              </>
            );
          })()
        : null}

      {deleteTargetId ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
          <section className="w-full max-w-sm rounded-lg border border-border bg-card p-5 shadow-panel">
            <div className="flex min-w-0 items-center gap-2 text-destructive">
              <Trash2 className="h-4 w-4 shrink-0" />
              <h2 className="truncate text-base font-semibold">{t("deleteModal.title")}</h2>
            </div>
            <p className="mt-3 text-sm text-muted-foreground">
              「{chats.find((chat) => chat.id === deleteTargetId)?.title || t("sidebar.newChat")}」将被永久删除，本地记录一并清除，无法恢复。
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setDeleteTargetId("")}>
                {t("deleteModal.cancel")}
              </Button>
              <Button variant="danger" onClick={() => deleteChatPermanently(deleteTargetId)}>
                {t("contextMenu.permanentDelete")}
              </Button>
            </div>
          </section>
        </div>
      ) : null}

      {showRoslynWarning ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
          <section className="w-full max-w-lg rounded-lg border border-destructive/40 bg-card p-6 shadow-panel">
            <div className="flex min-w-0 items-center gap-3 text-destructive">
              <AlertTriangle className="h-5 w-5 shrink-0" />
              <h2 className="truncate text-lg font-semibold">{t("roslynModal.title")}</h2>
            </div>
            <p className="mt-4 text-sm text-muted-foreground">
              {t("roslynModal.description")}
            </p>
            <div className="mt-5 grid gap-3 text-sm">
              <DataLine label={t("roslynModal.riskConfirmTitle")} value={permission?.roslynRiskAcknowledged ? t("roslynModal.confirmed") : t("roslynModal.notConfirmed")} />
              <DataLine label={t("roslynModal.targetMode")} value={t("roslynModal.targetModeValue")} />
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setShowRoslynWarning(false)}>
                {t("deleteModal.cancel")}
              </Button>
              <Button variant="danger" onClick={confirmRoslynWarning} disabled={loading}>
                {t("roslynModal.confirmButton")}
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function Composer({
  input,
  setInput,
  sending,
  permission,
  statusLabel,
  projectLabel,
  onSubmit,
  onSwitchMode,
  commands = [],
  compact = false,
  projects = [],
  onBindProject,
  queuedCount = 0,
}: {
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  permission?: PermissionState;
  statusLabel: string;
  projectLabel: string;
  onSubmit: (event?: FormEvent) => void;
  onSwitchMode: (mode: PermissionState["executionMode"]) => void;
  commands?: Array<{ name: string; title: string }>;
  compact?: boolean;
  projects?: Array<{ key: string; name: string }>;
  onBindProject?: (path: string) => void;
  queuedCount?: number;
}) {
  const { t } = useTranslation();
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const [bindMenuOpen, setBindMenuOpen] = useState(false);
  const currentMode = (permission?.executionMode || "approval") as ExecutionMode;
  const slashQuery = input.startsWith("/") && !input.includes(" ") && !input.includes("\n") ? input.slice(1).toLowerCase() : null;
  const slashMatches =
    slashQuery !== null
      ? commands.filter((command) => command.name.toLowerCase().includes(slashQuery)).slice(0, 8)
      : [];
  return (
    <form onSubmit={onSubmit} className="relative rounded-3xl bg-muted/70 shadow-composer">
      {slashMatches.length > 0 ? (
        <div className="absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
          {slashMatches.map((command) => (
            <button
              key={command.name}
              type="button"
              className="flex w-full min-w-0 items-center gap-3 px-3 py-2 text-left hover:bg-muted"
              onClick={() => setInput(`/${command.name} `)}
            >
              <span className="shrink-0 font-mono text-xs text-primary">/{command.name}</span>
              <span className="truncate text-xs text-muted-foreground">{command.title}</span>
            </button>
          ))}
        </div>
      ) : null}
      <div className={cn("rounded-3xl border border-border bg-card", compact ? "p-3" : "p-4")}>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          className="min-h-[76px] w-full resize-none bg-transparent px-1 text-base outline-none placeholder:text-muted-foreground"
          placeholder={t("chat.inputPlaceholder")}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmit();
            }
          }}
        />
        <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <div className="relative">
              <button
                type="button"
                className="flex h-8 min-w-0 max-w-full items-center gap-2 rounded-md px-2 text-sm text-amber-700 transition-colors hover:bg-amber-500/10"
                onClick={() => setModeMenuOpen((open) => !open)}
              >
                <Shield className="h-4 w-4 shrink-0" />
                <span className="truncate">{executionModeLabel(currentMode)}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0" />
              </button>
              {modeMenuOpen ? <div className="fixed inset-0 z-20" onClick={() => setModeMenuOpen(false)} /> : null}
              {modeMenuOpen ? (
                <div className="absolute bottom-10 left-0 z-30 w-72 rounded-lg border border-border bg-card p-1.5 shadow-panel">
                  {EXECUTION_MODES.map((mode) => (
                    <button
                      key={mode.value}
                      type="button"
                      className={cn(
                        "flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted",
                        currentMode === mode.value ? "bg-muted" : "",
                      )}
                      onClick={() => {
                        setModeMenuOpen(false);
                        if (mode.value !== currentMode) {
                          onSwitchMode(mode.value);
                        }
                      }}
                    >
                      <Check className={cn("mt-0.5 h-4 w-4 shrink-0", currentMode === mode.value ? "text-primary" : "opacity-0")} />
                      <span className="min-w-0">
                        <span className={cn("block font-medium", mode.value === "roslyn_full_auto" ? "text-destructive" : "")}>
                          {mode.label}
                        </span>
                        <span className="block text-xs text-muted-foreground">{mode.description}</span>
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            <Badge tone="muted" className="max-w-[220px] truncate">
              {statusLabel}
            </Badge>
            {sending ? (
              <Badge tone="warn" className="max-w-[240px] truncate">
                <Loader2 className="mr-1 h-3 w-3 shrink-0 animate-spin" />
                {t("chat.executingHint")}{queuedCount > 0 ? t("chat.executingHintCount", { count: queuedCount }) : ""}
              </Badge>
            ) : null}
          </div>
          <Button className="h-10 w-10 rounded-full px-0" disabled={!input.trim()} type="submit">
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <div className="relative flex h-12 min-w-0 items-center gap-2 px-5 text-sm text-muted-foreground">
        <button
          type="button"
          className="flex h-8 min-w-0 max-w-full items-center gap-2 rounded-md px-2 transition-colors hover:bg-muted hover:text-foreground"
          onClick={() => setBindMenuOpen((open) => !open)}
          title={t("chat.switchProject")}
        >
          {projectLabel ? <Folder className="h-4 w-4 shrink-0" /> : <MessageSquare className="h-4 w-4 shrink-0" />}
          <span className="truncate">{projectLabel ? `在 ${projectLabel} 中工作` : t("chat.tempChatHint")}</span>
          <ChevronDown className="h-3.5 w-3.5 shrink-0" />
        </button>
        {bindMenuOpen ? <div className="fixed inset-0 z-20" onClick={() => setBindMenuOpen(false)} /> : null}
        {bindMenuOpen ? (
          <div className="absolute bottom-11 left-4 z-30 w-72 rounded-lg border border-border bg-card p-1.5 shadow-panel">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
              onClick={() => {
                setBindMenuOpen(false);
                onBindProject?.("");
              }}
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
              <span className="min-w-0 flex-1 truncate">{t("chat.tempChatHint")}</span>
              <Check className={cn("h-4 w-4 shrink-0 text-primary", projectLabel ? "opacity-0" : "")} />
            </button>
            {projects.map((project) => (
              <button
                key={project.key}
                type="button"
                className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted"
                onClick={() => {
                  setBindMenuOpen(false);
                  onBindProject?.(project.key);
                }}
              >
                <Folder className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{project.name}</span>
                <Check className={cn("h-4 w-4 shrink-0 text-primary", projectLabel === project.name ? "" : "opacity-0")} />
              </button>
            ))}
            <div className="mt-1 border-t border-border px-2.5 py-1.5 text-xs text-muted-foreground/70">
              {t("chat.tempChatDesc")}
            </div>
          </div>
        ) : null}
      </div>
    </form>
  );
}

function ProjectIndexPanel({
  projectPath,
  projectName,
  result,
  loading,
  error,
  onScan,
  onReview,
}: {
  projectPath: string;
  projectName: string;
  result: ProjectIndexScanResult | null;
  loading: boolean;
  error: string;
  onScan: () => void;
  onReview: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!projectPath) {
    return null;
  }
  const summary = result?.summary || {};
  const firstScan = Boolean(summary.firstScan);
  const changed = Boolean(summary.changed);
  const added = Number(summary.addedFiles || 0);
  const modified = Number(summary.modifiedFiles || 0);
  const deleted = Number(summary.deletedFiles || 0);
  const guidChanges = Number(summary.guidChangeCount || 0);
  const total = Number(summary.totalFiles || 0);
  const scannerFamilies = summary.scannerFamilies || [];
  const statusTone: "ok" | "warn" | "danger" | "muted" = error ? "danger" : loading ? "muted" : changed && !firstScan ? "warn" : "ok";
  const statusLabel = error ? t("skillStatus.failed") : loading ? t("project.statusIndexing") : firstScan ? t("project.statusBaseline") : changed ? t("project.statusChanged") : t("project.statusClean");
  const changeText = firstScan
    ? `${formatCount(total)} indexed`
    : `+${added} ~${modified} -${deleted}${guidChanges ? ` guid ${guidChanges}` : ""}`;
  const addedPaths = result?.changes?.added || [];
  const modifiedPaths = result?.changes?.modified || [];
  const deletedPaths = result?.changes?.deleted || [];
  return (
    <section className="mb-4 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <Search className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("project.changes", { name: projectName || shortPath(projectPath) })}</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
          <span className="shrink-0 font-mono text-xs text-muted-foreground">{changeText}</span>
        </button>
        <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" disabled={loading} onClick={onScan}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
        <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" disabled={loading} onClick={onReview} title={t("project.reviewChanges")}>
          <Bot className="h-3.5 w-3.5" />
          {t("outfit.review")}
        </Button>
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <DataLine label={t("doctor.error")} value={error} /> : null}
          <DataLine label={t("subagent.roles.projectIndexReview")} value={projectPath} mono />
          <DataLine label="Files" value={`${formatCount(total)} total · ${formatCount(Number(summary.unchangedFiles || 0))} unchanged`} />
          <DataLine label="Hashing" value={`${formatCount(Number(summary.hashesComputed || 0))} computed · ${formatCount(Number(summary.hashesReused || 0))} reused`} />
          {scannerFamilies.length ? <DataLine label="Affected" value={scannerFamilies.join(", ")} /> : null}
          {addedPaths.length ? <OutputBlock label="Added" value={formatProjectIndexPaths(addedPaths)} /> : null}
          {modifiedPaths.length ? <OutputBlock label="Modified" value={formatProjectIndexPaths(modifiedPaths)} /> : null}
          {deletedPaths.length ? <OutputBlock label="Deleted" value={formatProjectIndexPaths(deletedPaths)} /> : null}
          {result?.staleDataPolicy ? <DataLine label="Policy" value={result.staleDataPolicy} /> : null}
        </div>
      ) : null}
    </section>
  );
}

function OutfitImportPanel({
  projectPath,
  packagePath,
  result,
  status,
  loading,
  requesting,
  onPackagePathChange,
  onPlan,
  onRequest,
  onReview,
}: {
  projectPath: string;
  packagePath: string;
  result: OutfitImportPlanResult | null;
  status: string;
  loading: boolean;
  requesting: boolean;
  onPackagePathChange: (value: string) => void;
  onPlan: () => void;
  onRequest: () => void;
  onReview: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!projectPath) {
    return null;
  }
  const plan = result?.plan;
  const summary = result?.inspection?.summary;
  const ready = Boolean(plan?.readyToApply);
  const hasResult = Boolean(result);
  const tone: "ok" | "warn" | "danger" | "muted" = !hasResult ? "muted" : result?.ok && ready ? "ok" : result?.ok ? "warn" : "danger";
  const label = !hasResult ? t("outfit.statusPending") : ready ? t("outfit.statusRequestable") : result?.ok ? t("outfit.statusNeedsConfirm") : t("outfit.statusBlocked");
  const expected = plan?.expectedAssetPaths || [];
  const dependencyPreflight = result?.dependencyPreflight || plan?.dependencyPreflight;
  const dependencyEntries = dependencyPreflight?.entries || [];
  const visibleDependencyEntries = dependencyEntries.filter((entry) => entry.status && entry.status !== "not_detected");
  const packageOrder = dependencyPreflight?.packageOrder;
  const importQueue = packageOrder?.importQueue || plan?.source?.importQueue || [];
  const skippedInstalledSupportPackages = packageOrder?.skippedInstalledSupportPackages || [];
  const compatibility = dependencyPreflight?.compatibility;
  const dependencySummary = dependencyPreflight
    ? `${dependencyPreflight.readyForImport ? t("connector.ready") : "blocked"} / ${dependencyPreflight.blockingIssueCount || dependencyPreflight.blockingMissingCount || 0} issue(s) / ${
        dependencyPreflight.detectedCount || visibleDependencyEntries.length
      } detected`
    : "";
  return (
    <section className="mb-4 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setOpen((value) => !value)}>
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <FolderPlus className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("outfit.title")}</span>
          <Badge tone={tone} className="shrink-0">
            {label}
          </Badge>
          {summary ? (
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {t("outfit.summary", { pkg: summary.unityPackageCount || 0, prefab: summary.prefabCandidateCount || 0 })}
            </span>
          ) : null}
        </button>
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <div className="flex min-w-0 gap-2">
            <input
              value={packagePath}
              onChange={(event) => onPackagePathChange(event.target.value)}
              placeholder=".unitypackage / Booth folder / prefab folder"
              className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
            <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading || !packagePath.trim()} onClick={onPlan}>
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
              Plan
            </Button>
            <Button type="button" className="h-9 shrink-0 px-3 text-xs" disabled={requesting || !ready} onClick={onRequest}>
              {requesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
              Request
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="h-9 shrink-0 px-3 text-xs"
              disabled={loading || (!packagePath.trim() && !result)}
              onClick={onReview}
              title={t("outfit.reviewPlan")}
            >
              <Bot className="h-3.5 w-3.5" />
              {t("outfit.review")}
            </Button>
          </div>
          {status ? <DataLine label={t("connector.status")} value={status} /> : null}
          {plan?.kind ? <DataLine label={t("encryption.plan")} value={plan.kind} /> : null}
          {dependencySummary ? <DataLine label="Dependency preflight" value={dependencySummary} /> : null}
          {compatibility ? (
            <DataLine
              label="Avatar compatibility"
              value={`${compatibility.status || t("optimization.unknown")}${compatibility.message ? ` - ${compatibility.message}` : ""}`}
            />
          ) : null}
          {importQueue.length ? (
            <OutputBlock
              label="Import order"
              value={importQueue
                .map((item, index) => `${item.order || index + 1}. ${item.role || "package"} ${item.path || item.actualPackagePath || ""}`)
                .join("\n")}
            />
          ) : null}
          {skippedInstalledSupportPackages.length ? (
            <OutputBlock
              label="Skipped packages"
              value={skippedInstalledSupportPackages
                .map(
                  (item) =>
                    `${item.dependencyLabel || item.dependencyId || "dependency"}: ${item.path || item.actualPackagePath || ""}${
                      item.message ? `\n  ${item.message}` : ""
                    }`,
                )
                .join("\n")}
            />
          ) : null}
          {visibleDependencyEntries.length ? (
            <OutputBlock
              label="Dependencies"
              value={visibleDependencyEntries
                .map((entry) => {
                  const evidence = [
                    ...(entry.evidence?.project || []),
                    ...(entry.evidence?.packagePathnames || []),
                    ...(entry.evidence?.hints || []),
                  ];
                  return `${entry.status || t("optimization.unknown")} ${entry.label || entry.id || "dependency"}${entry.blockingBeforeImport ? " [before import]" : ""}${
                    evidence.length ? `\n  ${evidence.slice(0, 3).join("\n  ")}` : ""
                  }`;
                })
                .join("\n")}
            />
          ) : null}
          {plan?.targetFolder ? <DataLine label={t("recovery.target")} value={plan.targetFolder} mono /> : null}
          {plan?.selectedPrefab ? <DataLine label="Prefab" value={plan.selectedPrefab} mono /> : null}
          {expected.length ? <OutputBlock label="Expected assets" value={expected.slice(0, 20).join("\n")} /> : null}
          {plan?.steps?.length ? (
            <OutputBlock
              label="Steps"
              value={plan.steps
                .map((step) => `${step.enabled === false ? "[off]" : "[on]"} ${step.category || ""} ${step.tool || step.id || ""}`.trim())
                .join("\n")}
            />
          ) : null}
          {result?.warnings?.length ? <OutputBlock label="Warnings" value={result.warnings.join("\n")} /> : null}
        </div>
      ) : null}
    </section>
  );
}

function subAgentRoleLabel(role: string): string {
  switch (role) {
    case "project_index_review":
      return "Project";
    case "outfit_package_inspection":
      return "Package";
    case "validation_triage":
      return "Validation";
    case "package_install_diagnosis":
      return "Install log";
    case "outfit_import_plan_review":
      return "Outfit plan";
    default:
      return role || i18n.t("subagent.roles.fallback");
  }
}

function subAgentStatusTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "completed") {
    return "ok";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "queued" || status === "running" || status === "cancelling") {
    return "warn";
  }
  return "muted";
}

function SubAgentPanel({
  tasks,
  loading,
  error,
  selected,
  onInspect,
  onCancel,
  onRetry,
  onAccept,
  onCloseInspect,
}: {
  tasks: SubAgentTask[];
  loading: boolean;
  error: string;
  selected: SubAgentTask | null;
  onInspect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onRetry: (taskId: string) => void;
  onAccept: (task: SubAgentTask) => void;
  onCloseInspect: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(tasks.length > 0 || Boolean(error));
  const running = tasks.filter((task) => task.status === "queued" || task.status === "running" || task.status === "cancelling").length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  const hasActivity = Boolean(error) || tasks.length > 0;
  const statusTone: "ok" | "warn" | "danger" | "muted" = error ? "danger" : failed ? "danger" : running ? "warn" : completed ? "ok" : "muted";
  const statusLabel = error ? t("subagent.statusNeedsAction") : running ? `${running} 运行中` : completed ? `${completed} 完成` : t("subagent.statusReady");
  const recentTasks = tasks.slice(0, 6);

  useEffect(() => {
    if (error || running > 0) {
      setOpen(true);
    }
  }, [error, running]);

  if (!hasActivity) {
    return null;
  }

  return (
    <section className="mb-4 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setOpen((value) => !value)}>
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <Bot className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("agent.subagentTask")}</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
        </button>
        {loading ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" /> : null}
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <DataLine label={t("doctor.error")} value={error} /> : null}
          {recentTasks.length ? (
            <div className="grid gap-2">
              {recentTasks.map((task) => (
                <div key={task.id} className="rounded-lg border border-border bg-background px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">
                      {task.displayName || t("agent.subagentTask")} · {subAgentRoleLabel(task.role)}
                    </span>
                    <Badge tone={subAgentStatusTone(task.status)} className="shrink-0">
                      {task.status}
                    </Badge>
                  </div>
                  <div className="mt-1 min-w-0 truncate text-xs text-muted-foreground">{task.summary || task.task || task.error || task.id}</div>
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onInspect(task.id)}>
                      <Eye className="h-3.5 w-3.5" />
                      {t("subagent.inspect")}
                    </Button>
                    {task.status === "queued" || task.status === "running" || task.status === "cancelling" ? (
                      <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onCancel(task.id)}>
                        <X className="h-3.5 w-3.5" />
                        {t("subagent.cancel")}
                      </Button>
                    ) : null}
                    {task.status === "failed" || task.status === "cancelled" ? (
                      <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onRetry(task.id)}>
                        <RefreshCw className="h-3.5 w-3.5" />
                        {t("doctor.retry")}
                      </Button>
                    ) : null}
                    {task.status === "completed" ? (
                      <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onAccept(task)}>
                        <Check className="h-3.5 w-3.5" />
                        Add
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">
              {t("subagent.noTasks")}
            </div>
          )}
          {selected ? (
            <div className="rounded-lg border border-border bg-background px-3 py-3">
              <div className="mb-2 flex min-w-0 items-center gap-2">
                <span className="min-w-0 flex-1 truncate text-sm font-semibold">{selected.displayName || selected.id}</span>
                <Badge tone={subAgentStatusTone(selected.status)} className="shrink-0">
                  {selected.status}
                </Badge>
                <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onCloseInspect}>
                  <X className="h-3.5 w-3.5" />
                </Button>
              </div>
              <DataLine label="Role" value={subAgentRoleLabel(selected.role)} />
              <DataLine label="Profile" value={selected.toolProfile || t("optimization.readOnly")} />
              {selected.projectPath ? <DataLine label={t("subagent.roles.projectIndexReview")} value={selected.projectPath} mono /> : null}
              {selected.summary ? <OutputBlock label="Summary" value={selected.summary} /> : null}
              {selected.error ? <OutputBlock label={t("doctor.error")} value={selected.error} danger /> : null}
              {selected.result !== undefined ? <OutputBlock label="Result" value={formatPayload(selected.result)} /> : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function ProviderSetup({
  provider,
  apiKey,
  baseUrl,
  model,
  saving,
  models,
  loadingModels,
  modelsError,
  testingProvider,
  providerTestMessage,
  keySaved = false,
  onLoadModels,
  onTestProvider,
  onProviderChange,
  onApiKeyChange,
  onBaseUrlChange,
  onModelChange,
  onSubmit,
}: {
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
  saving: boolean;
  models: Array<{ id: string; label: string }>;
  loadingModels: boolean;
  modelsError: string;
  testingProvider: string;
  providerTestMessage: string;
  keySaved?: boolean;
  onLoadModels: () => void;
  onTestProvider: (capability: "text" | "structured" | "vision") => void;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onSubmit: (event?: FormEvent) => void;
}) {
  const requiresBaseUrl = ["openai", "deepseek", "openrouter", "ollama", "vertexai", "custom"].includes(provider);
  const hasModelList = models.length > 0;
  const capabilities = providerCapabilities(provider);

  return (
    <form onSubmit={onSubmit} className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="grid gap-4">
        <FieldLabel label={i18n.t("provider.apiProvider")}>
          <select
            value={provider}
            onChange={(event) => onProviderChange(event.target.value)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
          >
            <option value="gemini">Google AI Studio</option>
            <option value="anthropic">Anthropic</option>
            <option value="openai">OpenAI</option>
            <option value="deepseek">DeepSeek</option>
            <option value="openrouter">OpenRouter</option>
            <option value="ollama">Ollama</option>
            <option value="vertexai">Vertex AI</option>
            <option value="custom">{i18n.t("provider.customEndpoint")}</option>
          </select>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {capabilities.map((capability) => (
              <Badge key={capability.label} tone={capability.tone} className="h-6 px-2 text-[10px]">
                {capability.label}
              </Badge>
            ))}
          </div>
        </FieldLabel>
        <FieldLabel label={i18n.t("provider.apiKey")}>
          {providerNeedsApiKey(provider) ? (
            <input
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              type="password"
              placeholder={keySaved ? i18n.t("provider.savedKeyHint") : i18n.t("provider.apiKeyPlaceholder")}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
              autoComplete="off"
            />
          ) : (
            <input
              value={i18n.t("provider.noKeyNeeded")}
              readOnly
              className="h-10 w-full rounded-md border border-border bg-muted px-3 text-sm text-muted-foreground outline-none"
            />
          )}
        </FieldLabel>
        {requiresBaseUrl ? (
          <FieldLabel label={i18n.t("provider.baseUrl")}>
            <input
              value={baseUrl}
              onChange={(event) => onBaseUrlChange(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </FieldLabel>
        ) : null}
        <FieldLabel label={i18n.t("provider.model")}>
          <div className="flex min-w-0 items-center gap-2">
            {hasModelList ? (
              <select
                value={models.some((item) => item.id === model) ? model : ""}
                onChange={(event) => onModelChange(event.target.value)}
                className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              >
                {!models.some((item) => item.id === model) ? (
                  <option value="" disabled>
                    {i18n.t("provider.selectModel")}
                  </option>
                ) : null}
                {models.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label || item.id}
                  </option>
                ))}
              </select>
            ) : (
              <input
                value={model}
                onChange={(event) => onModelChange(event.target.value)}
                placeholder={i18n.t("provider.modelPlaceholder")}
                className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
              />
            )}
            <Button
              type="button"
              variant="outline"
              className="h-10 shrink-0 gap-2 px-3 text-sm"
              onClick={onLoadModels}
              disabled={loadingModels || saving}
            >
              {loadingModels ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              {i18n.t("provider.refreshModels")}
            </Button>
          </div>
          {modelsError ? <div className="mt-1.5 text-xs text-destructive/80">{modelsError}</div> : null}
          {providerTestMessage ? <div className="mt-1.5 text-xs text-muted-foreground">{providerTestMessage}</div> : null}
          {hasModelList && !modelsError ? (
            <div className="mt-1.5 text-xs text-muted-foreground">{i18n.t("provider.fetchedModels", { count: models.length })}</div>
          ) : null}
        </FieldLabel>
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <Button type="button" variant="outline" disabled={saving || Boolean(testingProvider)} onClick={() => onTestProvider("text")}>
          {testingProvider === "text" ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessageSquare className="h-4 w-4" />}
          Text
        </Button>
        <Button type="button" variant="outline" disabled={saving || Boolean(testingProvider)} onClick={() => onTestProvider("structured")}>
          {testingProvider === "structured" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          JSON
        </Button>
        <Button type="button" variant="outline" disabled={saving || Boolean(testingProvider)} onClick={() => onTestProvider("vision")}>
          {testingProvider === "vision" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
          Vision
        </Button>
        <Button disabled={saving || (providerNeedsApiKey(provider) && !apiKey.trim() && !keySaved) || !model.trim()} type="submit">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {i18n.t("common.save")}
        </Button>
      </div>
    </form>
  );
}

function CheckpointStoragePanel({
  status,
  loading,
  isDesktop,
  limitInput,
  onLimitInputChange,
  onSaveLimit,
  onOpenFolder,
  onPickDirectory,
  onDeleteSelected,
  onRelocate,
}: {
  status: ExternalAgentConnectorStatus | null;
  loading: boolean;
  isDesktop: boolean;
  limitInput: string;
  onLimitInputChange: (value: string) => void;
  onSaveLimit: () => void;
  onOpenFolder: (targetPath: string) => void;
  onPickDirectory: (currentPath: string) => Promise<string>;
  onDeleteSelected: (ids: string[]) => void;
  onRelocate: (directory: string) => void;
}) {
  const { t } = useTranslation();
  const usage = status?.gateway?.checkpointArchiveUsage;
  const prune = status?.gateway?.checkpointArchivePrune;
  const maxSizeMb = status?.gateway?.checkpointArchiveMaxSizeMb ?? usage?.maxSizeMb ?? 0;
  const usageText = `${formatStorageSize(usage?.sizeBytes)} / ${maxSizeMb > 0 ? `${formatCount(maxSizeMb)} MB` : t("settings.unlimited")}`;
  const directory = usage?.directory || "";

  const archives = useMemo(
    () => (usage?.archives ?? []).filter((item): item is NonNullable<typeof item> & { checkpointId: string } => Boolean(item?.checkpointId)),
    [usage?.archives],
  );
  const selectableIds = useMemo(
    () => archives.filter((item) => !item.protected).map((item) => item.checkpointId),
    [archives],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [relocateInput, setRelocateInput] = useState("");

  // 当存档列表刷新（删除/迁移后）时，丢弃已不存在的选中项，避免误删幽灵 ID。
  useEffect(() => {
    setSelected((prev) => {
      const next = new Set<string>();
      for (const id of prev) {
        if (selectableIds.includes(id)) {
          next.add(id);
        }
      }
      return next.size === prev.size ? prev : next;
    });
  }, [selectableIds]);

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };
  const selectAll = () => setSelected(new Set(selectableIds));
  const invertSelection = () =>
    setSelected((prev) => {
      const next = new Set<string>();
      for (const id of selectableIds) {
        if (!prev.has(id)) {
          next.add(id);
        }
      }
      return next;
    });
  const cleanSelected = () => {
    const ids = selectableIds.filter((id) => selected.has(id));
    if (ids.length) {
      onDeleteSelected(ids);
    }
  };
  const pickDirectory = async () => {
    const selectedPath = await onPickDirectory(relocateInput || directory);
    if (selectedPath) {
      setRelocateInput(selectedPath);
    }
  };
  const disabled = loading || !status;
  const selectedCount = selectableIds.filter((id) => selected.has(id)).length;

  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="flex min-w-0 items-center gap-2">
        <Archive className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{t("settings.storage")}</h2>
        <Badge tone={maxSizeMb > 0 ? "ok" : "muted"} className="shrink-0">
          {maxSizeMb > 0 ? t("settings.limitOn") : t("settings.unlimited")}
        </Badge>
      </div>

      <div className="mt-4 grid gap-3">
        <DataLine label={t("settings.checkpointArchiveUsage")} value={usageText} />
        <DataLine label={t("settings.checkpointArchiveCount")} value={formatCount(usage?.archiveCount)} />
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <DataLine label={t("settings.checkpointArchiveDirectory")} value={directory || "-"} />
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={!directory || !isDesktop}
            title={!isDesktop ? t("settings.checkpointArchiveOpenFolderDesktopOnly") : undefined}
            onClick={() => onOpenFolder(directory)}
          >
            <FolderOpen className="h-4 w-4 shrink-0" />
            {t("settings.checkpointArchiveOpenFolder")}
          </Button>
        </div>
      </div>

      <div className="mt-5 flex min-w-0 flex-wrap items-end gap-3">
        <label className="min-w-48 flex-1 text-sm">
          <span className="mb-1 block font-medium">{t("settings.checkpointArchiveLimit")}</span>
          <input
            type="number"
            min={0}
            step={256}
            value={limitInput}
            onChange={(event) => onLimitInputChange(event.target.value)}
            disabled={disabled}
            className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
          />
        </label>
        <Button type="button" disabled={disabled} onClick={onSaveLimit}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t("common.save")}
        </Button>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">{t("settings.checkpointArchiveLimitHint")}</div>
      {prune ? (
        <div className="mt-3 text-xs text-muted-foreground">
          {t("settings.checkpointArchivePruned", {
            count: prune.deletedCount ?? 0,
            size: formatStorageSize(prune.deletedBytes),
          })}
        </div>
      ) : null}

      <div className="mt-6 border-t border-border pt-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">{t("settings.checkpointArchiveListTitle")}</h3>
          <Button type="button" variant="outline" disabled={disabled || !selectableIds.length} onClick={selectAll}>
            {t("settings.checkpointArchiveSelectAll")}
          </Button>
          <Button type="button" variant="outline" disabled={disabled || !selectableIds.length} onClick={invertSelection}>
            {t("settings.checkpointArchiveInvertSelection")}
          </Button>
          <Button type="button" variant="danger" disabled={disabled || selectedCount === 0} onClick={cleanSelected}>
            <Trash2 className="h-4 w-4 shrink-0" />
            {t("settings.checkpointArchiveCleanSelected")}
          </Button>
        </div>

        {selectedCount > 0 ? (
          <div className="mt-2 text-xs text-muted-foreground">
            {t("settings.checkpointArchiveSelectedCount", { count: selectedCount })}
          </div>
        ) : null}

        {archives.length ? (
          <ul className="mt-3 max-h-72 overflow-auto rounded-lg border border-border">
            {archives.map((item) => {
              const id = item.checkpointId;
              const isProtected = Boolean(item.protected);
              const checked = selected.has(id);
              return (
                <li
                  key={id}
                  className="flex min-w-0 items-center gap-3 border-b border-border/60 px-3 py-2 last:border-b-0"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled || isProtected}
                    onChange={() => toggleOne(id)}
                    className="h-4 w-4 shrink-0 accent-primary disabled:opacity-40"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{item.label || id}</div>
                    <div className="truncate text-xs text-muted-foreground/80">{item.path || id}</div>
                  </div>
                  <div className="shrink-0 text-xs text-muted-foreground">{formatStorageSize(item.sizeBytes)}</div>
                  {isProtected ? (
                    <Badge tone="warn" className="shrink-0" title={t("settings.checkpointArchiveProtectedHint")}>
                      {t("settings.checkpointArchiveProtected")}
                    </Badge>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="mt-3 rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
            {t("settings.checkpointArchiveNoArchives")}
          </div>
        )}
      </div>

      <div className="mt-6 border-t border-border pt-4">
        <label className="block text-sm">
          <span className="mb-1 block font-medium">{t("settings.checkpointArchiveNewDirectory")}</span>
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <input
              type="text"
              value={relocateInput}
              onChange={(event) => setRelocateInput(event.target.value)}
              disabled={disabled}
              placeholder="D:\\VRCForge\\checkpoint-archives"
              className="h-10 min-w-48 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
            />
            <Button
              type="button"
              variant="outline"
              disabled={disabled || !isDesktop}
              title={!isDesktop ? t("settings.checkpointArchivePickFolderDesktopOnly") : undefined}
              onClick={() => void pickDirectory()}
            >
              <FolderOpen className="h-4 w-4 shrink-0" />
              {t("settings.checkpointArchivePickFolder")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || !relocateInput.trim()}
              onClick={() => onRelocate(relocateInput)}
            >
              <FolderPlus className="h-4 w-4 shrink-0" />
              {t("settings.checkpointArchiveChangeDirectory")}
            </Button>
          </div>
        </label>
        <div className="mt-2 text-xs text-muted-foreground">{t("settings.checkpointArchiveNewDirectoryHint")}</div>
      </div>
    </div>
  );
}

function ExternalAgentConnectorsPanel({
  status,
  loading,
  message,
  selectedProjectPath,
  onRefresh,
  onToggleGateway,
  onToggleWriteRequests,
  onRevoke,
  onInstall,
  onUninstall,
  onCopy,
}: {
  status: ExternalAgentConnectorStatus | null;
  loading: boolean;
  message: string;
  selectedProjectPath: string;
  onRefresh: () => void;
  onToggleGateway: (enabled: boolean) => void;
  onToggleWriteRequests: (enabled: boolean) => void;
  onRevoke: () => void;
  onInstall: (client: ExternalAgentConnectorClient) => void;
  onUninstall: (client: ExternalAgentConnectorClient) => void;
  onCopy: (text: string, label: string) => void;
}) {
  const { t } = useTranslation();
  const gateway = status?.gateway;
  const codexText = status?.clientConfigs?.codex?.text || "";
  const codexStdioText = status?.clientConfigs?.codexStdio?.text || "";
  const claudeText = status?.clientConfigs?.claudeCode?.text || "";
  const claudeStdioText = status?.clientConfigs?.claudeCodeStdio?.text || status?.clientConfigs?.claudeCowork?.text || "";
  const toolCount = status?.advertisedTools?.length ?? 0;
  const writeTargetCount = status?.writeTargets?.length ?? 0;
  const launcherArgs = status?.launcher?.stdioBridge?.args || [];
  const launcherCommand = [status?.launcher?.stdioBridge?.command, ...launcherArgs].filter(Boolean).join(" ");
  const smokeArgs = status?.launcher?.smoke?.args || [];
  const smokeLiveArgs = status?.launcher?.smoke?.liveWriteRollbackArgs || [];
  const smokeCommand = [status?.launcher?.smoke?.command, ...smokeArgs, ...smokeLiveArgs].filter(Boolean).join(" ");
  const clients = status?.clients;
  const lastAction = status?.lastConnectorAction;
  const connectorRows: Array<{
    client: ExternalAgentConnectorClient;
    title: string;
    mode: string;
    copyText: string;
    copyLabel: string;
    shared?: string;
  }> = [
    {
      client: "codexApp",
      title: "Codex App",
      mode: t("connector.userConfig"),
      copyText: codexStdioText,
      copyLabel: "Codex App config",
      shared: t("connector.sharedWithCli"),
    },
    {
      client: "codexCli",
      title: "Codex CLI",
      mode: t("connector.userConfig"),
      copyText: codexStdioText,
      copyLabel: "Codex CLI config",
      shared: t("connector.sharedWithApp"),
    },
    {
      client: "claudeCode",
      title: "Claude Code CLI",
      mode: t("connector.projectConfig"),
      copyText: claudeStdioText,
      copyLabel: "Claude Code config",
    },
    {
      client: "claudeCowork",
      title: "Claude Cowork App",
      mode: t("connector.desktopConfig"),
      copyText: claudeStdioText,
      copyLabel: "Claude Cowork config",
    },
  ];
  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{t("connector.title")}</h2>
        <Badge tone={gateway?.enabled ? "ok" : "muted"} className="shrink-0">
          {gateway?.enabled ? t("skills.enabled") : t("connector.disabled")}
        </Badge>
      </div>

      <div className="mt-4 grid gap-3">
        <DataLine label={t("connector.endpoint")} value={status?.mcp?.url || gateway?.mcpUrl || "http://127.0.0.1:8757/mcp"} mono />
        <DataLine label={t("connector.tokenEnv")} value={status?.auth?.tokenEnvVar || "VRCFORGE_AGENT_TOKEN"} mono />
        <DataLine label={t("connector.stdioBridge")} value={launcherCommand || "-"} mono />
        <DataLine label={t("connector.smoke")} value={smokeCommand || "-"} mono />
        <DataLine label={t("connector.tools")} value={`${toolCount} read tools / ${writeTargetCount} write-request targets`} />
        <DataLine label={t("connector.config")} value={gateway?.configPath || "-"} />
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        <ConnectorToggle
          label={t("connector.gateway")}
          checked={Boolean(gateway?.enabled)}
          disabled={loading || !status}
          onChange={onToggleGateway}
        />
        <ConnectorToggle
          label={t("connector.writeRequests")}
          checked={Boolean(gateway?.allowWriteRequests)}
          disabled={loading || !status}
          onChange={onToggleWriteRequests}
        />
      </div>

      <div className="mt-5 grid gap-3">
        {connectorRows.map((row) => (
          <ConnectorClientRow
            key={row.client}
            client={row.client}
            title={row.title}
            mode={row.mode}
            state={clients?.[row.client]}
            loading={loading}
            copyText={row.copyText}
            copyLabel={row.copyLabel}
            shared={row.shared}
            selectedProjectPath={selectedProjectPath}
            lastAction={lastAction}
            onInstall={onInstall}
            onUninstall={onUninstall}
            onCopy={onCopy}
          />
        ))}
      </div>

      <div className="mt-5 flex flex-wrap justify-end gap-2">
        {message ? (
          <Badge tone={lastAction?.ok === false ? "danger" : "ok"} className="mr-auto shrink-0">
            {message}
          </Badge>
        ) : null}
        <Button type="button" variant="outline" disabled={loading} onClick={onRefresh}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </Button>
        <Button type="button" variant="outline" disabled={!codexText} onClick={() => onCopy(codexText, "Codex HTTP config")}>
          <Copy className="h-4 w-4" />
          {t("connector.codexHttp")}
        </Button>
        <Button type="button" variant="outline" disabled={!claudeText} onClick={() => onCopy(claudeText, "Claude HTTP config")}>
          <Copy className="h-4 w-4" />
          {t("connector.claudeHttp")}
        </Button>
        <Button type="button" variant="danger" disabled={loading || !status} onClick={onRevoke}>
          {t("connector.revokeToken")}
        </Button>
      </div>

      {status?.lastCalls?.length ? (
        <div className="mt-5 overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_120px] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span className="truncate">{t("connector.event")}</span>
            <span className="truncate">{t("proof.tool")}</span>
            <span className="truncate">{t("connector.status")}</span>
          </div>
          {status.lastCalls.slice(0, 8).map((call, index) => (
            <div
              key={`${call.event}-${call.createdAt}-${index}`}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_120px] gap-2 border-b border-border/60 px-3 py-2 text-xs last:border-b-0"
            >
              <span className="truncate">{call.event || "-"}</span>
              <span className="truncate font-mono">{call.targetTool || "-"}</span>
              <span className="truncate">{call.status || call.riskLevel || "-"}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

type ConnectorClientState = NonNullable<ExternalAgentConnectorStatus["clients"]>[ExternalAgentConnectorClient];

function ConnectorClientRow({
  client,
  title,
  mode,
  state,
  loading,
  copyText,
  copyLabel,
  shared,
  selectedProjectPath,
  lastAction,
  onInstall,
  onUninstall,
  onCopy,
}: {
  client: ExternalAgentConnectorClient;
  title: string;
  mode: string;
  state?: ConnectorClientState;
  loading: boolean;
  copyText: string;
  copyLabel: string;
  shared?: string;
  selectedProjectPath: string;
  lastAction?: ExternalAgentConnectorStatus["lastConnectorAction"];
  onInstall: (client: ExternalAgentConnectorClient) => void;
  onUninstall: (client: ExternalAgentConnectorClient) => void;
  onCopy: (text: string, label: string) => void;
}) {
  const { t } = useTranslation();
  const installed = Boolean(state?.installed);
  const needsProject = client === "claudeCode" && !selectedProjectPath;
  const installable = state?.installable !== false && !needsProject;
  const installActionDisabled = loading || !state;
  const actionMatches = normalizeConnectorClient(lastAction?.client) === client;
  const action = actionMatches ? lastAction : undefined;
  const handshake = action?.handshake;
  const statusTone = installed ? "ok" : installable ? "muted" : "warn";
  const statusLabel = installed ? t("connector.installed") : needsProject ? t("connector.needsProject") : installable ? t("connector.notInstalled") : t("connector.needsAttention");
  return (
    <div className="grid min-w-0 gap-3 rounded-lg border border-border bg-background/40 p-3 md:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <span className="min-w-0 truncate text-sm font-semibold">{title}</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
          <Badge tone="muted" className="shrink-0">
            {mode}
          </Badge>
          {shared ? (
            <Badge tone="muted" className="shrink-0">
              {shared}
            </Badge>
          ) : null}
          {state?.cliDetected !== null && state?.cliDetected !== undefined ? (
            <Badge tone={state.cliDetected ? "ok" : "muted"} className="shrink-0">
              CLI {state.cliDetected ? "found" : "not found"}
            </Badge>
          ) : null}
          {state?.appDetected !== null && state?.appDetected !== undefined ? (
            <Badge tone={state.appDetected ? "ok" : "muted"} className="shrink-0">
              App {state.appDetected ? "found" : "not found"}
            </Badge>
          ) : null}
        </div>
        <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
          <div className="min-w-0 truncate">
            <span className="mr-2 text-foreground/70">{t("connector.config")}</span>
            <span className="font-mono">{state?.configPath || "-"}</span>
          </div>
          {state?.cliPath ? (
            <div className="min-w-0 truncate">
              <span className="mr-2 text-foreground/70">CLI</span>
              <span className="font-mono">{state.cliPath}</span>
              {state.cliSource ? <span className="ml-2">({state.cliSource})</span> : null}
            </div>
          ) : null}
          {state?.cliError ? <div className="break-words text-amber-700 dark:text-amber-300">{state.cliError}</div> : null}
          {state?.appError ? <div className="break-words text-amber-700 dark:text-amber-300">{state.appError}</div> : null}
          {state?.lastError ? <div className="text-amber-700 dark:text-amber-300">{state.lastError}</div> : null}
          {needsProject ? (
            <div className="text-amber-700 dark:text-amber-300">{t("connector.needsProjectHint")}</div>
          ) : !installable ? (
            <div className="text-amber-700 dark:text-amber-300">{t("connector.notInstallableHint")}</div>
          ) : null}
          {action ? (
            <div
              className={cn(
                "mt-1 grid gap-1 rounded-md px-2 py-1.5",
                action.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-destructive/10 text-destructive",
              )}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-medium">{action.ok ? t("connector.selfTestPassed") : t("connector.selfTestFailed")}</span>
                {handshake?.toolCount !== undefined ? <span>{handshake.toolCount} tools</span> : null}
                {handshake?.connected ? <span>{t("connector.connected")}</span> : null}
                {handshake?.ready ? <span>{t("connector.ready")}</span> : null}
              </div>
              {action.error ? <div className="break-words">{action.error}</div> : null}
              {handshake?.warning ? <div className="break-words">{handshake.warning}</div> : null}
              {action.suggestion || handshake?.suggestion ? <div className="break-words">{action.suggestion || handshake?.suggestion}</div> : null}
              {action.backupPath ? <div className="truncate font-mono text-[11px]">Backup {action.backupPath}</div> : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="flex flex-wrap items-start justify-end gap-2">
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={loading || !copyText} onClick={() => onCopy(copyText, copyLabel)}>
          <Copy className="h-3.5 w-3.5" />
          {t("connector.copy")}
        </Button>
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={installActionDisabled} onClick={() => onInstall(client)}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          Install
        </Button>
        <Button type="button" variant="danger" className="h-8 px-3 text-xs" disabled={loading || !installed} onClick={() => onUninstall(client)}>
          <Trash2 className="h-3.5 w-3.5" />
          {t("connector.remove")}
        </Button>
      </div>
    </div>
  );
}

function ConnectorToggle({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "flex h-11 min-w-0 items-center gap-3 rounded-md border px-3 text-left text-sm transition-colors disabled:opacity-60",
        checked ? "border-primary bg-primary/5" : "border-border bg-background hover:bg-muted",
      )}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      <Badge tone={checked ? "ok" : "muted"} className="h-6 shrink-0 px-2">
        {checked ? t("connector.on") : t("connector.off")}
      </Badge>
    </button>
  );
}

function ProtectionWorkspace({
  plan,
  selectedProjectPath,
  avatarPath,
  avatars,
  profile,
  ownsAssets,
  loading,
  loadingAvatars,
  message,
  avatarMessage,
  requestingFamily,
  onAvatarPathChange,
  onProfileChange,
  onOwnsAssetsChange,
  onRefresh,
  onRefreshAvatars,
  onRequestApply,
}: {
  plan: AvatarEncryptionPlanResult | null;
  selectedProjectPath: string;
  avatarPath: string;
  avatars: AvatarListItem[];
  profile: string;
  ownsAssets: boolean;
  loading: boolean;
  loadingAvatars: boolean;
  message: string;
  avatarMessage: string;
  requestingFamily: string;
  onAvatarPathChange: (value: string) => void;
  onProfileChange: (value: string) => void;
  onOwnsAssetsChange: (value: boolean) => void;
  onRefresh: () => void;
  onRefreshAvatars: () => void;
  onRequestApply: (family: "liltoon" | "poiyomi") => void;
}) {
  const planPayload = protectionPlanPayload(plan);
  const activeProfile = protectionProfileCards(plan).find((item) => item.id === profile) || PROTECTION_PROFILE_FALLBACKS[1];
  const benchmarkRows = protectionBenchmarkRows(plan);
  const benchmarkGroups = groupProtectionBenchmarks(benchmarkRows);
  const hardGate = protectionRecord(planPayload.hardGate);
  const blockingIds = protectionArray(hardGate.blockingIds).map((item) => String(item));
  const connector = protectionRecord(planPayload.externalAddon);
  const connectorConfigured = Boolean(connector.configured);
  const requestReady = planPayload.status === "request_ready" && planPayload.writeStatus !== "blocked" && connectorConfigured;
  const profileApplyBlocked = String(activeProfile.applyStatus || "").startsWith("blocked");
  const selectedCandidates = protectionArray(planPayload.selectedCandidates);
  const hasLilToon = selectedCandidates.length === 0 || protectionFamilyAvailable(selectedCandidates, "liltoon");
  const hasPoiyomi = selectedCandidates.length === 0 || protectionFamilyAvailable(selectedCandidates, "poiyomi");
  const canRequest = requestReady && ownsAssets && Boolean(avatarPath.trim()) && !profileApplyBlocked && !loading;
  const impact = protectionImpactSummary(benchmarkRows, profile);

  return (
    <div className="min-h-0 flex-1 overflow-auto px-3 py-4 sm:px-6 sm:py-8">
      <div className="mx-auto grid max-w-6xl gap-6">
        <section className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <Shield className="h-4 w-4 shrink-0 text-primary" />
              <h1 className="truncate text-lg font-semibold">{i18n.t("encryption.title")}</h1>
              {activeProfile.recommended ? (
                <Badge tone="ok" className="shrink-0">
                  {i18n.t("encryption.recommended")}
                </Badge>
              ) : null}
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{selectedProjectPath || i18n.t("encryption.noUnityProject")}</div>
          </div>
          <Badge tone={requestReady ? "ok" : "warn"} className="shrink-0">
            {requestReady ? i18n.t("encryption.readyToRequest") : connectorConfigured ? i18n.t("encryption.needsReview") : i18n.t("encryption.privateAddonRequired")}
          </Badge>
          <Badge tone={profileApplyBlocked ? "warn" : "muted"} className="shrink-0">
            {activeProfile.label || profile}
          </Badge>
          <Button type="button" variant="outline" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </section>

        <section className="grid gap-3 md:grid-cols-3">
          {protectionProfileCards(plan).map((card) => {
            const selected = card.id === profile;
            return (
              <button
                key={card.id}
                type="button"
                onClick={() => onProfileChange(String(card.id))}
                className={cn(
                  "min-w-0 rounded-lg border bg-card p-4 text-left transition-colors",
                  selected ? "border-primary bg-primary/5" : "border-border hover:border-primary/40 hover:bg-muted/40",
                )}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <Shield className="h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1 truncate text-sm font-semibold">{card.title || card.label || card.id}</div>
                  {card.recommended ? (
                    <Badge tone="ok" className="shrink-0">
                      {i18n.t("encryption.default")}
                    </Badge>
                  ) : null}
                </div>
                <div className="mt-2 text-xs text-muted-foreground">{card.description || "-"}</div>
                <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label={i18n.t("encryption.protection")} value={card.protection || "-"} />
                  <DataLine label={i18n.t("encryption.device")} value={card.deviceFit || "-"} />
                  <DataLine label={i18n.t("encryption.impact")} value={protectionCostLabel(card.cost)} />
                </div>
                {String(card.applyStatus || "").startsWith("blocked") ? (
                  <Badge tone="warn" className="mt-3">
                    {i18n.t("encryption.proofGate")}
                  </Badge>
                ) : null}
              </button>
            );
          })}
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.75fr)]">
          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("encryption.planTarget")}</div>
            </div>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
              <select
                value={avatars.some((item) => item.avatarPath === avatarPath) ? avatarPath : ""}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                disabled={loadingAvatars || avatars.length === 0}
                className="h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:text-muted-foreground"
              >
                <option value="">{loadingAvatars ? i18n.t("encryption.scanningAvatars") : avatars.length ? i18n.t("encryption.selectAvatar") : i18n.t("encryption.noSceneAvatars")}</option>
                {avatars.map((avatar, index) => {
                  const value = avatar.avatarPath || "";
                  return (
                    <option key={`${value}-${index}`} value={value}>
                      {avatarOptionLabel(avatar)}
                    </option>
                  );
                })}
              </select>
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loadingAvatars} onClick={onRefreshAvatars}>
                {loadingAvatars ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Avatars
              </Button>
            </div>
            <div className="mt-2 flex min-w-0 items-center gap-2">
              <input
                value={avatarPath}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                placeholder={i18n.t("encryption.avatarScenePath")}
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading} onClick={onRefresh}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Plan
              </Button>
            </div>
            <label className="mt-3 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={ownsAssets}
                onChange={(event) => onOwnsAssetsChange(event.target.checked)}
                className="h-4 w-4 shrink-0 rounded border-border"
              />
              <span className="min-w-0 truncate">{i18n.t("encryption.ownsAssetsLabel")}</span>
            </label>
            {avatarMessage ? <div className="mt-2 truncate text-xs text-muted-foreground">{avatarMessage}</div> : null}
            {message ? <div className="mt-2 truncate text-xs text-muted-foreground">{message}</div> : null}
          </div>

          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <Shield className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("encryption.preview")}</div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <OptimizationMetric label={i18n.t("encryption.targets")} value={formatOptimizationMetric(planPayload.selectedCandidateCount)} />
              <OptimizationMetric label={i18n.t("encryption.mode")} value={activeProfile.label || profile} />
              <OptimizationMetric label={i18n.t("encryption.plan")} value={String(planPayload.status || i18n.t("encryption.notLoaded"))} />
              <OptimizationMetric label={i18n.t("encryption.expected")} value={impact} />
            </div>
            {blockingIds.length ? (
              <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                {blockingIds.slice(0, 4).map((item) => (
                  <DataLine key={item} label={i18n.t("encryption.gate")} value={protectionGateLabel(item)} />
                ))}
              </div>
            ) : null}
            <div className="mt-3 flex min-w-0 flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-8 px-3 text-xs"
                disabled={!canRequest || !hasLilToon || requestingFamily === "liltoon"}
                onClick={() => onRequestApply("liltoon")}
              >
                {requestingFamily === "liltoon" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                {i18n.t("encryption.requestLilToon")}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-8 px-3 text-xs"
                disabled={!canRequest || !hasPoiyomi || requestingFamily === "poiyomi"}
                onClick={() => onRequestApply("poiyomi")}
              >
                {requestingFamily === "poiyomi" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                {i18n.t("encryption.requestPoiyomi")}
              </Button>
              <Badge tone="muted" className="h-8 shrink-0">
                {i18n.t("encryption.approvalRequired")}
              </Badge>
            </div>
          </div>
        </section>

        <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <Gauge className="h-4 w-4 shrink-0 text-primary" />
            <div className="truncate text-sm font-semibold">{i18n.t("encryption.estimatedFrameImpact")}</div>
            <Badge tone="muted" className="ml-auto shrink-0">
              {i18n.t("encryption.planningEstimate")}
            </Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-separate border-spacing-0 text-left text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="border-b border-border px-3 py-2 font-medium">{i18n.t("encryption.avatarSize")}</th>
                  {PROTECTION_PROFILE_FALLBACKS.map((item) => (
                    <th key={item.id} className="border-b border-border px-3 py-2 font-medium">
                      {item.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {benchmarkGroups.map((group) => (
                  <tr key={group.scale}>
                    <td className="border-b border-border/70 px-3 py-2 text-muted-foreground">{group.scale}</td>
                    {PROTECTION_PROFILE_FALLBACKS.map((item) => {
                      const row = group.byProfile[String(item.id)];
                      return (
                        <td key={item.id} className="border-b border-border/70 px-3 py-2">
                          {row ? `${formatProofValue(row.estimatedFps)} fps / ${formatProofValue(row.estimatedImpactPercent)}%` : "-"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function OptimizationWorkspace({
  report,
  proofs,
  selectedProof,
  endpoint,
  permission,
  selectedProjectPath,
  avatarPath,
  avatars,
  targetProfile,
  loading,
  loadingProofs,
  loadingAvatars,
  message,
  proofMessage,
  avatarMessage,
  actionOptions,
  requestingActionId,
  requestingDependencyId,
  onAvatarPathChange,
  onTargetProfileChange,
  onRefresh,
  onRefreshProofs,
  onSelectProof,
  onRefreshAvatars,
  onActionOptionChange,
  onRequestAction,
  onRequestDependency,
}: {
  report: OptimizationPlannerReport | null;
  proofs: OptimizationProofSummary[];
  selectedProof: OptimizationProofDetail | null;
  endpoint: string;
  permission?: PermissionState;
  selectedProjectPath: string;
  avatarPath: string;
  avatars: AvatarListItem[];
  targetProfile: string;
  loading: boolean;
  loadingProofs: boolean;
  loadingAvatars: boolean;
  message: string;
  proofMessage: string;
  avatarMessage: string;
  actionOptions: Record<string, OptimizationActionOptions>;
  requestingActionId: string;
  requestingDependencyId: string;
  onAvatarPathChange: (value: string) => void;
  onTargetProfileChange: (profile: string) => void;
  onRefresh: () => void;
  onRefreshProofs: () => void;
  onSelectProof: (runId: string) => void;
  onRefreshAvatars: () => void;
  onActionOptionChange: (actionId: string, key: keyof OptimizationActionOptions, value: string) => void;
  onRequestAction: (card: NonNullable<OptimizationPlannerReport["actionCards"]>[number]) => void;
  onRequestDependency: (dependency: NonNullable<NonNullable<OptimizationPlannerReport["dependencyDoctor"]>["dependencies"]>[number]) => void;
}) {
  const dependencies = report?.dependencyDoctor?.dependencies ?? [];
  const actions = report?.actionCards ?? [];
  const offenders = report?.topOffenders ?? [];
  const metrics = report?.baseline?.metrics ?? {};
  const profile = report?.targetProfile;
  const optimizerApproval = optimizerApprovalBadge(permission);
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6">
        <section className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <Gauge className="h-4 w-4 shrink-0 text-primary" />
              <h1 className="truncate text-lg font-semibold">{i18n.t("optimization.title")}</h1>
              <Badge tone="muted" className="shrink-0">
                {report?.versionStage || "0.7.2-beta"}
              </Badge>
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{selectedProjectPath || i18n.t("encryption.noUnityProject")}</div>
          </div>
          <Badge tone={report?.readOnly && report?.noProjectWrites ? "ok" : "warn"} className="shrink-0">
            {report?.readOnly && report?.noProjectWrites ? i18n.t("optimization.readOnly") : i18n.t("encryption.needsReview")}
          </Badge>
          <Badge tone={report?.directApplyExposed ? "danger" : "muted"} className="shrink-0">
            {report?.directApplyExposed ? i18n.t("optimization.directApplyExposed") : i18n.t("optimization.noDirectApply")}
          </Badge>
          <Badge tone={optimizerApproval.modeTone} className="shrink-0">
            mode: {permission?.executionMode || "approval"}
          </Badge>
          <Button type="button" variant="outline" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("optimization.targetProfile")}</div>
              {profile?.label ? (
                <Badge tone="default" className="ml-auto shrink-0">
                  {profile.label}
                </Badge>
              ) : null}
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {OPTIMIZATION_TARGET_PROFILES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onTargetProfileChange(item.id)}
                  className={cn(
                    "h-10 min-w-0 rounded-md border px-3 text-sm transition-colors",
                    targetProfile === item.id ? "border-primary bg-primary/5 text-foreground" : "border-border text-muted-foreground hover:bg-muted",
                  )}
                >
                  <span className="block truncate">{item.label}</span>
                </button>
              ))}
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
              <select
                value={avatars.some((item) => item.avatarPath === avatarPath) ? avatarPath : ""}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                disabled={loadingAvatars || avatars.length === 0}
                className="h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:text-muted-foreground"
              >
                <option value="">{loadingAvatars ? i18n.t("encryption.scanningAvatars") : avatars.length ? i18n.t("encryption.selectAvatar") : i18n.t("encryption.noSceneAvatars")}</option>
                {avatars.map((avatar, index) => {
                  const value = avatar.avatarPath || "";
                  return (
                    <option key={`${value}-${index}`} value={value}>
                      {avatarOptionLabel(avatar)}
                    </option>
                  );
                })}
              </select>
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loadingAvatars} onClick={onRefreshAvatars}>
                {loadingAvatars ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Avatars
              </Button>
            </div>
            <div className="mt-2 flex min-w-0 items-center gap-2">
              <input
                value={avatarPath}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                placeholder={i18n.t("encryption.avatarScenePath")}
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading} onClick={onRefresh}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Scan
              </Button>
            </div>
            {avatarMessage ? <div className="mt-2 truncate text-xs text-muted-foreground">{avatarMessage}</div> : null}
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <OptimizationMetric label={i18n.t("optimization.pcRank")} value={report?.baseline?.performanceHeadline?.pc?.rank || i18n.t("optimization.unknown")} />
              <OptimizationMetric label={i18n.t("optimization.questRank")} value={report?.baseline?.performanceHeadline?.quest?.rank || i18n.t("optimization.unknown")} />
              <OptimizationMetric label={i18n.t("optimization.triangles")} value={formatOptimizationMetric(metrics.triangleCount)} />
              <OptimizationMetric label={i18n.t("optimization.parameterBits")} value={formatOptimizationMetric(metrics.expressionParameterBits)} />
            </div>
          </div>

          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("optimization.topOffenders")}</div>
              <Badge tone="muted" className="ml-auto shrink-0">
                {offenders.length}
              </Badge>
            </div>
            <div className="grid gap-2">
              {offenders.map((item) => (
                <div key={item.id || item.label} className="flex min-w-0 items-center gap-2 rounded-md border border-border px-3 py-2">
                  <div className="min-w-0 flex-1 truncate text-sm">{item.label || item.id}</div>
                  <Badge tone={offenderTone(item.severity)} className="shrink-0">
                    {item.count ?? 0}
                  </Badge>
                </div>
              ))}
              {message ? <div className="truncate text-xs text-muted-foreground">{message}</div> : null}
            </div>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <OptimizationMetric label={i18n.t("optimization.textureBytes")} value={formatOptimizationMetric(metrics.textureMemoryBytes)} />
          <OptimizationMetric label={i18n.t("optimization.materialSlots")} value={formatOptimizationMetric(metrics.materialSlots)} />
          <OptimizationMetric label={i18n.t("optimization.skinnedMeshes")} value={formatOptimizationMetric(metrics.skinnedMeshCount)} />
          <OptimizationMetric label={i18n.t("optimization.physBones")} value={formatOptimizationMetric(metrics.physBones)} />
          <OptimizationMetric label={i18n.t("optimization.generatedResidue")} value={formatOptimizationMetric(metrics.generatedResidueCount)} />
        </section>

        <OptimizationProofReadiness report={report} />
        <OptimizationProofViewer
          proofs={proofs}
          selectedProof={selectedProof}
          endpoint={endpoint}
          loading={loadingProofs}
          message={proofMessage}
          onRefresh={onRefreshProofs}
          onSelectProof={onSelectProof}
        />

        <section>
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <h2 className="truncate text-sm font-semibold">{i18n.t("optimization.dependencyStatus")}</h2>
            <Badge tone="muted" className="shrink-0">
              {dependencies.length}
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {dependencies.map((item) => (
              <div key={item.id || item.label} className="min-w-0 rounded-lg border border-border bg-card p-3">
                <div className="flex min-w-0 items-center gap-2">
                  <div className="min-w-0 flex-1 truncate text-sm font-medium">{item.label || item.id}</div>
                  <Badge tone={dependencyTone(item.status)} className="shrink-0">
                    {item.status || i18n.t("optimization.unknown")}
                  </Badge>
                </div>
                <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label={i18n.t("subagent.roles.outfitPackageInspection")} value={item.matchedPackageId || "-"} />
                  <DataLine label="Version" value={item.version || "-"} />
                  <DataLine label={i18n.t("package.tableRisk")} value={item.riskLevel || "-"} />
                </div>
                <div className="mt-2 max-h-10 overflow-hidden text-xs text-muted-foreground">{item.recommendedRole || "-"}</div>
                {item.status !== "installed" && item.packageIds?.length ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="mt-3 h-8 px-3 text-xs"
                    disabled={loading || !selectedProjectPath || requestingDependencyId === (item.id || item.packageIds[0])}
                    onClick={() => onRequestDependency(item)}
                  >
                    {requestingDependencyId === (item.id || item.packageIds[0]) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                    Install request
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <h2 className="truncate text-sm font-semibold">{i18n.t("optimization.recommendedOrder")}</h2>
            <Badge tone="muted" className="shrink-0">
              {report?.recommendedOrder?.length ?? 0}
            </Badge>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {actions.map((card) => (
              <div key={card.id} className={cn("min-w-0 rounded-lg border bg-card p-4", card.enabled ? "border-border" : "border-border opacity-70")}>
                <div className="flex min-w-0 items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{card.title}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{card.description}</div>
                  </div>
                  <Badge tone={optimizationRiskTone(card.riskLevel)} className="shrink-0">
                    {card.riskLevel || i18n.t("optimization.unknown")}
                  </Badge>
                </div>
                <div className="mt-3 flex min-w-0 flex-wrap gap-2">
                  <Badge tone={card.level === "read-only" ? "ok" : "muted"}>{card.level || "plan-only"}</Badge>
                  <Badge tone="muted">{card.dependency || "VRCForge"}</Badge>
                  <Badge tone="muted">{card.recommendedVersionStage || "0.7.2-beta"}</Badge>
                  {card.requestTool ? <Badge tone={optimizerApproval.requestTone}>{optimizerApproval.requestLabel}</Badge> : null}
                </div>
                <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label="Benefit" value={card.expectedBenefit || i18n.t("optimization.unknown")} />
                  <DataLine label={i18n.t("doctor.why")} value={card.whyRecommended || "-"} />
                  <DataLine label="Next" value={card.nextSafeAction || "-"} />
                  {card.requestTool ? <DataLine label={i18n.t("optimization.request")} value={card.requestTool} /> : null}
                  {card.blockedReason ? <DataLine label={i18n.t("package.labels.blocked")} value={card.blockedReason} /> : null}
                </div>
                {isTttOptimizationRequest(card) ? (
                  <textarea
                    value={actionOptions[card.id]?.atlasTargetMaterials ?? ""}
                    onChange={(event) => onActionOptionChange(card.id, "atlasTargetMaterials", event.target.value)}
                    placeholder="Assets/.../Material.mat"
                    rows={2}
                    className="mt-3 min-h-16 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-xs outline-none focus:border-primary"
                  />
                ) : null}
                {isMeshiaOptimizationRequest(card) ? (
                  <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
                    <input
                      value={actionOptions[card.id]?.rendererPath ?? ""}
                      onChange={(event) => onActionOptionChange(card.id, "rendererPath", event.target.value)}
                      placeholder={i18n.t("optimization.rendererPath")}
                      className="h-8 min-w-0 rounded-md border border-border bg-background px-3 text-xs outline-none focus:border-primary"
                    />
                    <input
                      value={actionOptions[card.id]?.relativeVertexCount ?? "0.9"}
                      onChange={(event) => onActionOptionChange(card.id, "relativeVertexCount", event.target.value)}
                      type="number"
                      min="0.75"
                      max="1"
                      step="0.05"
                      className="h-8 min-w-0 rounded-md border border-border bg-background px-3 text-xs outline-none focus:border-primary"
                    />
                  </div>
                ) : null}
                {card.requestTool ? (
                  <div className="mt-3 flex min-w-0 flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      className="h-8 px-3 text-xs"
                      disabled={
                        loading ||
                        !selectedProjectPath ||
                        !avatarPath.trim() ||
                        optimizationActionMissingRequiredOptions(card, actionOptions[card.id] ?? {}) ||
                        requestingActionId === card.id
                      }
                      onClick={() => onRequestAction(card)}
                    >
                      {requestingActionId === card.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                      Request
                    </Button>
                    <Badge tone={optimizerApproval.requestTone} className="h-8 shrink-0">
                      {optimizerApproval.requestLabel}
                    </Badge>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function optimizerApprovalBadge(permission?: PermissionState) {
  if (permission?.roslynFullAuto) {
    return {
      modeTone: "danger" as const,
      requestTone: "danger" as const,
      requestLabel: i18n.t("optimization.explicitApproval"),
    };
  }
  if (permission?.autoApprove || permission?.executionMode === "auto") {
    return {
      modeTone: "warn" as const,
      requestTone: "warn" as const,
      requestLabel: i18n.t("optimization.explicitApproval"),
    };
  }
  return {
    modeTone: "muted" as const,
    requestTone: "muted" as const,
    requestLabel: i18n.t("encryption.approvalRequired"),
  };
}

function OptimizationMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function OptimizationProofReadiness({ report }: { report: OptimizationPlannerReport | null }) {
  const { t } = useTranslation();
  const plans = optimizationRecord(report?.plans);
  const visual = optimizationRecord(plans.visualRegression);
  const rollback = optimizationRecord(plans.rollbackVerify);
  const parameterRegression = optimizationRecord(plans.parameterBehaviorRegression);
  const parameterPath = optimizationRecord(plans.parameterPathToSkill);
  const ma2bt = optimizationRecord(plans.ma2btConvertibility);
  const visualShots = optimizationArray(visual.shots);
  const visualPlayModeShots = visualShots.filter((item) => Boolean(optimizationRecord(item).requiresPlayMode));
  const rollbackReady = Boolean(rollback.canGenerateFutureProof);
  const parameterSummary = optimizationRecord(parameterRegression.summary);
  const parameterGates = optimizationRecord(parameterPath.hardGates);
  const ma2btSummary = optimizationRecord(ma2bt.summary);
  const ma2btDiagnostics = optimizationArray(ma2bt.diagnostics);
  const cards = [
    {
      id: "visual",
      icon: Eye,
      title: t("optimization.visualProof"),
      tone: visualShots.length ? ("ok" as const) : ("warn" as const),
      lines: [
        ["Shots", `${visualShots.length}`],
        ["Play Mode", `${visualPlayModeShots.length}`],
        ["Scoring", optimizationRecord(visual.scoring).mode ? String(optimizationRecord(visual.scoring).mode) : "not-run"],
      ],
    },
    {
      id: "rollback",
      icon: RotateCcw,
      title: t("optimization.rollbackProof"),
      tone: rollbackReady ? ("ok" as const) : ("warn" as const),
      lines: [
        ["Project", rollback.projectReadable ? "readable" : "not ready"],
        ["Residue", formatOptimizationMetric(rollback.generatedResidueCount)],
        ["Checkpoint", rollback.checkpointInfrastructureRequired ? "required" : t("optimization.unknown")],
      ],
    },
    {
      id: "parameters",
      icon: Shield,
      title: t("optimization.parameterGates"),
      tone: Number(parameterSummary.dangerParameterCount || parameterGates.blockedParameterCount || 0) ? ("warn" as const) : ("ok" as const),
      lines: [
        ["Cases", formatOptimizationMetric(parameterSummary.testCaseCount)],
        ["Blocked", formatOptimizationMetric(parameterGates.blockedParameterCount ?? parameterSummary.dangerParameterCount)],
        ["Apply", parameterPath.applyBlocked ? "blocked" : t("optimization.review")],
      ],
    },
    {
      id: "ma2bt",
      icon: Sparkles,
      title: t("optimization.ma2btDiagnostics"),
      tone: Number(ma2btSummary.skippedLayerCount || 0) ? ("warn" as const) : ("ok" as const),
      lines: [
        ["Convertible", formatOptimizationMetric(ma2btSummary.convertibleLayerCount)],
        ["Skipped", formatOptimizationMetric(ma2btSummary.skippedLayerCount)],
        ["Reasons", `${ma2btDiagnostics.length}`],
      ],
    },
  ];
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="truncate text-sm font-semibold">{t("optimization.proofReadiness")}</h2>
        <Badge tone="muted" className="ml-auto shrink-0">
          {t("optimization.gates09")}
        </Badge>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.id} className="min-w-0 rounded-lg border border-border bg-background p-3">
              <div className="mb-2 flex min-w-0 items-center gap-2">
                <Icon className="h-4 w-4 shrink-0 text-primary" />
                <div className="min-w-0 flex-1 truncate text-sm font-medium">{card.title}</div>
                <Badge tone={card.tone} className="shrink-0">
                  {card.tone === "ok" ? t("connector.ready") : t("optimization.review")}
                </Badge>
              </div>
              <div className="grid gap-1 text-xs text-muted-foreground">
                {card.lines.map(([label, value]) => (
                  <DataLine key={label} label={label} value={value || "-"} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function OptimizationProofViewer({
  proofs,
  selectedProof,
  endpoint,
  loading,
  message,
  onRefresh,
  onSelectProof,
}: {
  proofs: OptimizationProofSummary[];
  selectedProof: OptimizationProofDetail | null;
  endpoint: string;
  loading: boolean;
  message: string;
  onRefresh: () => void;
  onSelectProof: (runId: string) => void;
}) {
  const { t } = useTranslation();
  const proof = selectedProof?.proof || proofs[0] || null;
  const visual = optimizationRecord(proof?.visualRegression);
  const screenshots = optimizationRecord(visual.screenshots);
  const profile = optimizationRecord(proof?.profileDiff);
  const pc = optimizationRecord(profile.pc);
  const quest = optimizationRecord(profile.quest);
  const parameters = optimizationRecord(proof?.parameterBudgetDelta);
  const rollback = optimizationRecord(proof?.rollbackProof);
  const stageIds = ["before", "after_apply", "after_rollback"];
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
        <History className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{t("optimization.optimizerProof")}</h2>
        {message ? (
          <Badge tone="muted" className="shrink-0">
            {message}
          </Badge>
        ) : null}
        <Button type="button" variant="ghost" className="h-8 px-2 text-xs" disabled={loading} onClick={onRefresh}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {!proof ? (
        <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">{t("optimization.noProofRuns")}</div>
      ) : (
        <div className="grid gap-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
            <div className="grid gap-2 rounded-lg border border-border bg-background p-3">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge tone={proof.ok ? "ok" : "danger"} className="shrink-0">
                  {proof.status || (proof.ok ? "passed" : "failed")}
                </Badge>
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{proof.runId}</span>
              </div>
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                <DataLine label={t("proof.tool")} value={proof.tool || "-"} />
                <DataLine label={t("recovery.checkpoint")} value={proof.checkpointId || "-"} mono />
                <DataLine label={t("checkpoint.changedFiles")} value={formatOptimizationMetric(proof.changedFileCount)} />
                <DataLine label={t("optimization.proofMetric.rollback")} value={proof.rollbackDone ? t("proof.rollbackDone") : t("proof.rollbackNotDone")} />
              </div>
            </div>
            <select
              value={proof.runId}
              disabled={loading || proofs.length === 0}
              onChange={(event) => onSelectProof(event.target.value)}
              className="h-10 min-w-0 self-start rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            >
              {proofs.map((item) => (
                <option key={item.runId} value={item.runId}>
                  {item.status || "proof"} / {item.tool || item.runId}
                </option>
              ))}
            </select>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <ProofMetricCard title={t("optimization.pcRank")} before={pc.rankBefore} after={pc.rankAfter} rollback={pc.rankRollback} />
            <ProofMetricCard title={t("optimization.questRank")} before={quest.rankBefore} after={quest.rankAfter} rollback={quest.rankRollback} />
            <ProofMetricCard title={t("optimization.parameterBits")} before="delta" after={parameters.syncedBitsDelta} rollback={parameters.rollbackMatchesBefore ? "matched" : t("optimization.review")} />
            <ProofMetricCard title="Rollback gate" before="severity/gate" after={rollback.matchesBeforeSeverityAndGate ? "matched" : t("optimization.review")} rollback={rollback.remainingFindingCount ?? "-"} />
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            {stageIds.map((stage) => {
              const entry = optimizationRecord(screenshots[stage]);
              const imageUrl = proofImageUrl(endpoint, entry.imageUrl);
              const ok = Boolean(entry.artifactOk || entry.exists);
              return (
                <div key={stage} className="grid min-w-0 gap-2 rounded-lg border border-border bg-background p-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <Eye className="h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1 truncate text-sm font-medium">{proofStageLabel(stage)}</div>
                    <Badge tone={ok ? "ok" : "warn"} className="shrink-0">
                      {ok ? t("optimization.captured") : t("optimization.missing")}
                    </Badge>
                  </div>
                  {imageUrl ? (
                    <div className="aspect-square overflow-hidden rounded-md border border-border bg-muted">
                      <img src={imageUrl} alt={proofStageLabel(stage)} className="h-full w-full object-contain" />
                    </div>
                  ) : (
                    <div className="grid aspect-square place-items-center rounded-md border border-dashed border-border text-xs text-muted-foreground">{t("optimization.noScreenshot")}</div>
                  )}
                  <div className="grid gap-1 text-xs text-muted-foreground">
                    <DataLine label="SHA" value={String(entry.sha256 || "-")} mono />
                    <DataLine label="Size" value={formatOptimizationMetric(entry.size)} />
                  </div>
                </div>
              );
            })}
          </div>
          {proof.failedSteps?.length ? <OutputBlock label={t("optimization.failedSteps")} value={proof.failedSteps.join("\n")} /> : null}
        </div>
      )}
    </section>
  );
}

function ProofMetricCard({ title, before, after, rollback }: { title: string; before: unknown; after: unknown; rollback: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="min-w-0 rounded-lg border border-border bg-background p-3">
      <div className="mb-2 truncate text-sm font-medium">{title}</div>
      <div className="grid gap-1 text-xs text-muted-foreground">
        <DataLine label={t("optimization.proofStages.before")} value={formatProofValue(before)} />
        <DataLine label={t("optimization.proofMetric.after")} value={formatProofValue(after)} />
        <DataLine label={t("optimization.proofMetric.rollback")} value={formatProofValue(rollback)} />
      </div>
    </div>
  );
}

function CheckpointWorkspace({
  checkpoints,
  interruptedRecoveries,
  adjustmentCheckpoints,
  selectedProjectPath,
  preview,
  recoveryPreview,
  adjustmentPreview,
  loading,
  restoringId,
  recoveryBusyId,
  adjustmentBusyId,
  message,
  recoveryMessage,
  adjustmentMessage,
  onRefresh,
  onPreview,
  onRestore,
  onPreviewRecovery,
  onRestoreRecovery,
  onExportRecoveryBundle,
  onResolveRecovery,
  onCreateAdjustment,
  onPreviewAdjustment,
  onSelectAdjustment,
  onApplyAdjustment,
  onOverwriteAdjustment,
  onRenameAdjustment,
  onDeleteAdjustment,
}: {
  checkpoints: AgentCheckpoint[];
  interruptedRecoveries: InterruptedApplyRecovery[];
  adjustmentCheckpoints: AdjustmentCheckpoint[];
  selectedProjectPath: string;
  preview: AgentCheckpointPreview | null;
  recoveryPreview: InterruptedApplyRecoveryPreview | null;
  adjustmentPreview: AdjustmentCheckpointPreview | null;
  loading: boolean;
  restoringId: string;
  recoveryBusyId: string;
  adjustmentBusyId: string;
  message: string;
  recoveryMessage: string;
  adjustmentMessage: string;
  onRefresh: () => void;
  onPreview: (checkpointId: string) => void;
  onRestore: (checkpointId: string) => void;
  onPreviewRecovery: (recoveryId: string) => void;
  onRestoreRecovery: (recoveryId: string) => void;
  onExportRecoveryBundle: (recoveryId: string) => void;
  onResolveRecovery: (recoveryId: string) => void;
  onCreateAdjustment: (kind: "face" | "shader") => void;
  onPreviewAdjustment: (checkpointId: string) => void;
  onSelectAdjustment: (checkpointId: string, slot: "A" | "B") => void;
  onApplyAdjustment: (checkpointId: string) => void;
  onOverwriteAdjustment: (checkpointId: string) => void;
  onRenameAdjustment: (checkpoint: AdjustmentCheckpoint) => void;
  onDeleteAdjustment: (checkpointId: string) => void;
}) {
  const selectedId = preview?.checkpoint?.id || "";
  const selectedRecoveryId = recoveryPreview?.recovery?.id || "";
  const selectedAdjustmentId = adjustmentPreview?.adjustmentCheckpoint?.id || "";
  const changedFiles = preview?.changedFiles || [];
  const workingTreeStatus = preview?.workingTreeStatus || [];
  const recoveryCheckpointPreview = recoveryPreview?.checkpointPreview || null;
  const recoveryChangedFiles = recoveryCheckpointPreview?.changedFiles || [];
  const recoveryWorkingTreeStatus = recoveryCheckpointPreview?.workingTreeStatus || [];
  const adjustmentChangedFiles = adjustmentPreview?.changedFiles || [];
  const adjustmentWorkingTreeStatus = adjustmentPreview?.workingTreeStatus || [];
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
        <div className="grid min-w-0 gap-6">
          <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-4 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.interruptedWrites")}</div>
              <Badge tone={interruptedRecoveries.length > 0 ? "warn" : "muted"} className="ml-auto shrink-0">
                {interruptedRecoveries.length}
              </Badge>
            </div>
            <div className="max-h-[24vh] space-y-2 overflow-auto pr-1">
              {interruptedRecoveries.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-5 text-center text-xs text-muted-foreground">
                  {i18n.t("checkpoint.noInterruptedWrites")}
                </div>
              ) : null}
              {interruptedRecoveries.map((recovery) => {
                const busy =
                  recoveryBusyId === recovery.id ||
                  recoveryBusyId.endsWith(`:${recovery.id}`) ||
                  recoveryBusyId.startsWith(`${recovery.id}:`);
                const status = recovery.status || "needs_recovery";
                return (
                  <div
                    key={recovery.id}
                    className={cn(
                      "grid min-w-0 gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                      selectedRecoveryId === recovery.id ? "border-primary bg-primary/5" : "border-border",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-mono text-xs">{recovery.id}</span>
                      <Badge tone={status === "applying" ? "warn" : status === "needs_recovery" ? "danger" : "muted"} className="h-6 shrink-0">
                        {status}
                      </Badge>
                      {busy ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" /> : null}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{recovery.targetTool || "-"}</div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{recovery.checkpointId || "-"}</div>
                    <div className="flex min-w-0 flex-wrap gap-1.5">
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onPreviewRecovery(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="danger"
                        className="h-7 px-2 text-xs"
                        onClick={() => onRestoreRecovery(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        {i18n.t("checkpoint.restore")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onExportRecoveryBundle(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <Download className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => onResolveRecovery(recovery.id)}
                        disabled={Boolean(recoveryBusyId)}
                      >
                        <Check className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-4 flex items-center gap-2">
              <History className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.checkpoints")}</div>
              <Badge tone="muted" className="ml-auto shrink-0">
                {checkpoints.length}
              </Badge>
              <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onRefresh} disabled={loading}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              </Button>
            </div>
            {selectedProjectPath ? <div className="mb-3 truncate text-xs text-muted-foreground">{selectedProjectPath}</div> : null}
            <div className="max-h-[34vh] space-y-2 overflow-auto pr-1">
              {checkpoints.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                  {i18n.t("checkpoint.noCheckpoints")}
                </div>
              ) : null}
              {checkpoints.map((checkpoint) => (
                <button
                  key={checkpoint.id}
                  type="button"
                  onClick={() => onPreview(checkpoint.id)}
                  className={cn(
                    "grid w-full min-w-0 gap-1 rounded-md border px-3 py-2 text-left text-sm transition-colors",
                    selectedId === checkpoint.id
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-primary/40 hover:bg-muted/60",
                  )}
                >
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs">{checkpoint.id}</span>
                    <Badge tone={checkpoint.ok ? "ok" : "warn"} className="h-6 shrink-0">
                      {checkpoint.status || (checkpoint.ok ? i18n.t("connector.ready") : "unavailable")}
                    </Badge>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{checkpoint.targetTool || "-"}</div>
                  <div className="truncate text-xs text-muted-foreground">{formatCheckpointTime(checkpoint.createdAt)}</div>
                </button>
              ))}
            </div>
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-4 flex items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.adjustmentTimeline")}</div>
              <Badge tone="muted" className="ml-auto shrink-0">
                {adjustmentCheckpoints.length}
              </Badge>
            </div>
            <div className="mb-3 flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => onCreateAdjustment("face")}
                disabled={Boolean(adjustmentBusyId) || loading}
              >
                <Plus className="h-3.5 w-3.5" />
                {i18n.t("checkpoint.face")}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-7 px-2 text-xs"
                onClick={() => onCreateAdjustment("shader")}
                disabled={Boolean(adjustmentBusyId) || loading}
              >
                <Plus className="h-3.5 w-3.5" />
                {i18n.t("checkpoint.shader")}
              </Button>
            </div>
            <div className="max-h-[40vh] space-y-2 overflow-auto pr-1">
              {adjustmentCheckpoints.length === 0 ? (
                <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                  {i18n.t("checkpoint.noAdjustments")}
                </div>
              ) : null}
              {adjustmentCheckpoints.map((checkpoint) => {
                const slots = checkpoint.selectedSlots || (checkpoint.selectionSlot ? [checkpoint.selectionSlot] : []);
                const slotA = slots.includes("A");
                const slotB = slots.includes("B");
                const busy =
                  adjustmentBusyId === checkpoint.id ||
                  adjustmentBusyId.startsWith(`${checkpoint.id}:`) ||
                  adjustmentBusyId.endsWith(`:${checkpoint.id}`);
                return (
                  <div
                    key={checkpoint.id}
                    className={cn(
                      "grid min-w-0 gap-2 rounded-md border px-3 py-2 text-sm transition-colors",
                      selectedAdjustmentId === checkpoint.id ? "border-primary bg-primary/5" : "border-border",
                    )}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Badge tone={checkpoint.kind === "face" ? "default" : "muted"} className="h-6 shrink-0">
                        {checkpoint.kind}
                      </Badge>
                      <button
                        type="button"
                        className="min-w-0 flex-1 truncate text-left font-medium"
                        onClick={() => onPreviewAdjustment(checkpoint.id)}
                      >
                        {checkpoint.label || checkpoint.id}
                      </button>
                      {busy ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" /> : null}
                    </div>
                    <div className="truncate font-mono text-xs text-muted-foreground">{checkpoint.checkpointId || checkpoint.id}</div>
                    <div className="flex min-w-0 flex-wrap gap-1.5">
                      <Button
                        type="button"
                        variant={slotA ? "primary" : "outline"}
                        className="h-7 px-2 text-xs"
                        onClick={() => onSelectAdjustment(checkpoint.id, "A")}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        A
                      </Button>
                      <Button
                        type="button"
                        variant={slotB ? "primary" : "outline"}
                        className="h-7 px-2 text-xs"
                        onClick={() => onSelectAdjustment(checkpoint.id, "B")}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        B
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onPreviewAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Eye className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onApplyAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <RotateCcw className="h-3.5 w-3.5" />
                        {i18n.t("checkpoint.apply")}
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-7 px-2 text-xs"
                        onClick={() => onOverwriteAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Archive className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-xs"
                        onClick={() => onRenameAdjustment(checkpoint)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        className="h-7 px-2 text-xs text-destructive"
                        onClick={() => onDeleteAdjustment(checkpoint.id)}
                        disabled={Boolean(adjustmentBusyId)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>
        </div>

        <div className="grid min-w-0 gap-6">
          <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-5 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.recoveryPreview")}</div>
              {recoveryPreview ? (
                <Badge tone={recoveryPreview.ok ? "warn" : "danger"} className="ml-auto shrink-0">
                  {recoveryPreview.recovery?.status || (recoveryPreview.ok ? i18n.t("connector.ready") : "blocked")}
                </Badge>
              ) : null}
            </div>

            {!recoveryPreview ? (
              <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                {i18n.t("checkpoint.selectInterruptedWrite")}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="grid gap-3">
                  <DataLine label={i18n.t("recovery.recovery")} value={recoveryPreview.recovery?.id || "-"} mono />
                  <DataLine label={i18n.t("recovery.target")} value={recoveryPreview.recovery?.targetTool || "-"} />
                  <DataLine label={i18n.t("recovery.checkpoint")} value={recoveryPreview.recovery?.checkpointId || "-"} mono />
                  <DataLine label={i18n.t("subagent.roles.projectIndexReview")} value={recoveryPreview.recovery?.projectRoot || "-"} />
                  {recoveryPreview.error ? <DataLine label={i18n.t("doctor.error")} value={recoveryPreview.error} /> : null}
                </div>
                <OutputBlock label={i18n.t("checkpoint.changedFiles")} value={recoveryChangedFiles.join("\n")} />
                <OutputBlock label={i18n.t("checkpoint.workingTree")} value={recoveryWorkingTreeStatus.join("\n")} />
                {recoveryMessage ? <div className="text-sm text-muted-foreground">{recoveryMessage}</div> : null}
                <div className="flex flex-wrap justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    disabled={!recoveryPreview.recovery?.id || Boolean(recoveryBusyId)}
                    onClick={() => recoveryPreview.recovery?.id && onExportRecoveryBundle(recoveryPreview.recovery.id)}
                  >
                    {recoveryBusyId.startsWith("bundle:") ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                    Bundle
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!recoveryPreview.ok || !recoveryPreview.recovery?.id || Boolean(recoveryBusyId)}
                    onClick={() => recoveryPreview.recovery?.id && onRestoreRecovery(recoveryPreview.recovery.id)}
                  >
                    {recoveryBusyId.startsWith("restore:") ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RotateCcw className="h-4 w-4" />
                    )}
                    Restore
                  </Button>
                </div>
              </div>
            )}
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-5 flex items-center gap-2">
              <RotateCcw className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.restorePreview")}</div>
              {preview ? (
                <Badge tone={preview.ok ? "ok" : "danger"} className="ml-auto shrink-0">
                  {preview.ok ? i18n.t("connector.ready") : "blocked"}
                </Badge>
              ) : null}
            </div>

            {!preview ? (
              <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                {i18n.t("checkpoint.selectCheckpoint")}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="grid gap-3">
                  <DataLine label={i18n.t("recovery.checkpoint")} value={preview.checkpoint?.id || "-"} mono />
                  <DataLine label={i18n.t("recovery.target")} value={preview.checkpoint?.targetTool || "-"} />
                  <DataLine label={i18n.t("subagent.roles.projectIndexReview")} value={preview.checkpoint?.projectRoot || "-"} />
                  <DataLine label={i18n.t("recovery.gitRef")} value={shortRef(preview.checkpoint?.checkpointRef)} mono />
                  {preview.error ? <DataLine label={i18n.t("doctor.error")} value={preview.error} /> : null}
                </div>
                <OutputBlock label={i18n.t("checkpoint.changedFiles")} value={changedFiles.join("\n")} />
                <OutputBlock label={i18n.t("checkpoint.workingTree")} value={workingTreeStatus.join("\n")} />
                {message ? <div className="text-sm text-muted-foreground">{message}</div> : null}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!preview.ok || !preview.checkpoint?.id || Boolean(restoringId)}
                    onClick={() => preview.checkpoint?.id && onRestore(preview.checkpoint.id)}
                  >
                    {restoringId ? <Loader2 className="h-4 w-4 animate-spin" /> : <RotateCcw className="h-4 w-4" />}
                    Restore
                  </Button>
                </div>
              </div>
            )}
          </section>

          <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-5 flex items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("checkpoint.adjustmentPreview")}</div>
              {adjustmentPreview ? (
                <Badge tone={adjustmentPreview.ok ? "ok" : "danger"} className="ml-auto shrink-0">
                  {adjustmentPreview.ok ? i18n.t("connector.ready") : "blocked"}
                </Badge>
              ) : null}
            </div>

            {!adjustmentPreview ? (
              <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
                {i18n.t("checkpoint.selectFaceShader")}
              </div>
            ) : (
              <div className="grid gap-4">
                <div className="grid gap-3">
                  <DataLine label="Adjustment" value={adjustmentPreview.adjustmentCheckpoint?.label || "-"} />
                  <DataLine label="Kind" value={adjustmentPreview.adjustmentCheckpoint?.kind || "-"} />
                  <DataLine label={i18n.t("recovery.checkpoint")} value={adjustmentPreview.checkpoint?.id || "-"} mono />
                  <DataLine label={i18n.t("subagent.roles.projectIndexReview")} value={adjustmentPreview.checkpoint?.projectRoot || "-"} />
                  <DataLine label={i18n.t("recovery.gitRef")} value={shortRef(adjustmentPreview.checkpoint?.checkpointRef)} mono />
                  {adjustmentPreview.error ? <DataLine label={i18n.t("doctor.error")} value={adjustmentPreview.error} /> : null}
                </div>
                <OutputBlock label={i18n.t("checkpoint.changedFiles")} value={adjustmentChangedFiles.join("\n")} />
                <OutputBlock label={i18n.t("checkpoint.workingTree")} value={adjustmentWorkingTreeStatus.join("\n")} />
                {adjustmentMessage ? <div className="text-sm text-muted-foreground">{adjustmentMessage}</div> : null}
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="danger"
                    disabled={!adjustmentPreview.ok || !adjustmentPreview.adjustmentCheckpoint?.id || Boolean(adjustmentBusyId)}
                    onClick={() =>
                      adjustmentPreview.adjustmentCheckpoint?.id && onApplyAdjustment(adjustmentPreview.adjustmentCheckpoint.id)
                    }
                  >
                    {adjustmentBusyId.startsWith("apply:") ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <RotateCcw className="h-4 w-4" />
                    )}
                    Apply
                  </Button>
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function SkillsWorkspace({
  skills,
  skillCount,
  skillCheck,
  selectedSkillName,
  draft,
  saving,
  onSelect,
  onNew,
  onCheck,
  onDraftChange,
  onSave,
  onDelete,
  packages,
  packageStore,
  packagesLoading,
  packageMessage,
  packageError,
  packageGovernance,
  packageAudit,
  onRefreshPackages,
  onPreflightPackage,
  onImportPackage,
  onExportPackage,
  onSetPackageEnabled,
  onUninstallPackage,
  onSetSafeMode,
  onTrustSigner,
  onRevokeSigner,
  onBlockPackage,
}: {
  skills: AgentSkill[];
  skillCount: number;
  skillCheck: AgentSkillCheck | null;
  selectedSkillName: string;
  draft: Partial<AgentSkill>;
  saving: boolean;
  onSelect: (skill: AgentSkill) => void;
  onNew: () => void;
  onCheck: () => void;
  onDraftChange: (skill: Partial<AgentSkill>) => void;
  onSave: (event?: FormEvent) => void;
  onDelete: () => void;
  packages: SkillPackageEntry[];
  packageStore: string;
  packagesLoading: boolean;
  packageMessage: string;
  packageError: string;
  packageGovernance: Record<string, unknown>;
  packageAudit: Array<Record<string, unknown>>;
  onRefreshPackages: () => void;
  onPreflightPackage: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImportPackage: (packagePath: string) => Promise<unknown>;
  onExportPackage: (skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) => Promise<unknown>;
  onSetPackageEnabled: (skillPackageId: string, enabled: boolean) => Promise<unknown>;
  onUninstallPackage: (skillPackageId: string) => Promise<unknown>;
  onSetSafeMode: (enabled: boolean, reason?: string) => Promise<unknown>;
  onTrustSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onRevokeSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onBlockPackage: (request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) => Promise<unknown>;
}) {
  const editable = !draft.source || draft.source === "user";
  const userSkillSelected = Boolean(selectedSkillName && draft.source === "user");
  const selectedCheck = skillCheck?.checks.find((item) => item.name === draft.name);
  const checkTone = selectedCheck?.status === "error" ? "danger" : selectedCheck?.status === "warning" ? "warn" : "muted";
  const [skillQuery, setSkillQuery] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const query = skillQuery.trim().toLowerCase();
  const visibleSkills = query
    ? skills.filter((skill) =>
        `${skill.name} ${skill.title || ""} ${skill.description || ""} ${skill.category || ""}`.toLowerCase().includes(query),
      )
    : skills;
  const groupedSkills = useMemo(() => {
    const map = new Map<string, AgentSkill[]>();
    for (const skill of visibleSkills) {
      const domain = skillDomain(skill);
      const list = map.get(domain) || [];
      list.push(skill);
      map.set(domain, list);
    }
    return SKILL_DOMAIN_ORDER.filter((domain) => map.has(domain)).map((domain) => ({
      domain,
      items: map.get(domain) || [],
    }));
  }, [visibleSkills]);

  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-4 flex items-center gap-2">
            <Wrench className="h-4 w-4 shrink-0 text-primary" />
            <div className="truncate text-sm font-semibold">{i18n.t("skills.title")}</div>
            <Badge tone="muted" className="ml-auto shrink-0">
              {skillCount}
            </Badge>
            <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onCheck} disabled={saving}>
              {i18n.t("skills.check")}
            </Button>
          </div>
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={skillQuery}
              onChange={(event) => setSkillQuery(event.target.value)}
              placeholder={i18n.t("skills.searchPlaceholder")}
              className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
            />
          </div>
          <div className="max-h-[calc(100vh-230px)] space-y-2 overflow-auto pr-1">
            {groupedSkills.length === 0 ? (
              <div className="px-3 py-4 text-xs text-muted-foreground">{i18n.t("skills.noMatch")}</div>
            ) : null}
            {groupedSkills.map((group) => {
              const collapsed = Boolean(collapsedGroups[group.domain]) && !query;
              return (
                <div key={group.domain} className="min-w-0">
                  <button
                    type="button"
                    onClick={() =>
                      setCollapsedGroups((current) => ({ ...current, [group.domain]: !current[group.domain] }))
                    }
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 transition-transform", collapsed ? "-rotate-90" : "")} />
                    <span className="min-w-0 flex-1 truncate">{group.domain}</span>
                    <Badge tone="muted" className="h-5 shrink-0 px-1.5 text-[10px]">
                      {group.items.length}
                    </Badge>
                  </button>
                  {collapsed
                    ? null
                    : group.items.map((skill) => (
                        <button
                          key={`${skill.source}-${skill.name}`}
                          onClick={() => onSelect(skill)}
                          className={cn(
                            "grid w-full min-w-0 gap-1 rounded-md px-3 py-2 text-left text-sm transition-colors",
                            selectedSkillName === skill.name
                              ? "bg-muted text-foreground"
                              : "text-muted-foreground hover:bg-muted hover:text-foreground",
                          )}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="min-w-0 flex-1 truncate font-medium">{skill.title || skill.name}</span>
                            <Badge tone={skill.available ? "ok" : "warn"} className="h-6 shrink-0">
                              {skill.skillType || skill.source}
                            </Badge>
                          </div>
                          <div className="truncate text-xs text-muted-foreground">{skill.permissionMode}</div>
                        </button>
                      ))}
                </div>
              );
            })}
          </div>
        </section>

        <div className="grid min-w-0 gap-6">
        <form onSubmit={onSave} className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
          <div className="mb-5 flex items-center gap-2">
            <div className="truncate text-sm font-semibold">{editable ? i18n.t("skills.userSkill") : i18n.t("skills.readOnlySkill")}</div>
            <Badge tone={checkTone} className="ml-auto shrink-0">
              {selectedCheck?.status || draft.permissionMode || "instruction_only"}
            </Badge>
          </div>
          <div className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label={i18n.t("skillForm.name")}>
                <input
                  value={draft.name || ""}
                  onChange={(event) => onDraftChange({ ...draft, name: event.target.value })}
                  disabled={!editable || userSkillSelected}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.titleField")}>
                <input
                  value={draft.title || ""}
                  onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-4">
              <FieldLabel label={i18n.t("skillForm.categoryField")}>
                <input
                  value={draft.category || ""}
                  onChange={(event) => onDraftChange({ ...draft, category: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.type")}>
                <input
                  value={draft.skillType || "package"}
                  onChange={(event) => onDraftChange({ ...draft, skillType: event.target.value })}
                  disabled
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.permission")}>
                <select
                  value={draft.permissionMode || "instruction_only"}
                  onChange={(event) => onDraftChange({ ...draft, permissionMode: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                >
                  <option value="instruction_only">instruction_only</option>
                  <option value="read_only">read_only</option>
                  <option value="preview">preview</option>
                  <option value="approval_required">approval_required</option>
                  <option value="advanced_power_mode">advanced_power_mode</option>
                </select>
              </FieldLabel>
              <FieldLabel label={i18n.t("package.tableRisk")}>
                <select
                  value={draft.riskLevel || "low"}
                  onChange={(event) => onDraftChange({ ...draft, riskLevel: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="critical">critical</option>
                </select>
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={draft.enabled !== false}
                  onChange={(event) => onDraftChange({ ...draft, enabled: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">{i18n.t("skills.enabled")}</span>
              </label>
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={draft.userInvocable !== false}
                  onChange={(event) => onDraftChange({ ...draft, userInvocable: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">{i18n.t("skills.slashCallable")}</span>
              </label>
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={Boolean(draft.disableModelInvocation)}
                  onChange={(event) => onDraftChange({ ...draft, disableModelInvocation: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">{i18n.t("skills.manualOnly")}</span>
              </label>
            </div>
            <FieldLabel label={i18n.t("skillForm.whenToUse")}>
              <textarea
                value={draft.whenToUse || ""}
                onChange={(event) => onDraftChange({ ...draft, whenToUse: event.target.value })}
                disabled={!editable}
                className="min-h-20 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </FieldLabel>
            <FieldLabel label={i18n.t("skillForm.description")}>
              <textarea
                value={draft.description || ""}
                onChange={(event) => onDraftChange({ ...draft, description: event.target.value })}
                disabled={!editable}
                className="min-h-16 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </FieldLabel>
            <div className="grid gap-4 md:grid-cols-3">
              <FieldLabel label={i18n.t("skillForm.allowedTools")}>
                <input
                  value={(draft.allowedTools || draft.tools || []).join(", ")}
                  onChange={(event) => {
                    const tools = splitList(event.target.value);
                    onDraftChange({ ...draft, tools, allowedTools: tools });
                  }}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.disallowedTools")}>
                <input
                  value={(draft.disallowedTools || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, disallowedTools: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.entrypoint")}>
                <input
                  value={draft.entrypointTool || ""}
                  onChange={(event) => onDraftChange({ ...draft, entrypointTool: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label={i18n.t("skillForm.argumentHint")}>
                <input
                  value={draft.argumentHint || ""}
                  onChange={(event) => onDraftChange({ ...draft, argumentHint: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.testCommand")}>
                <input
                  value={draft.testCommand || ""}
                  onChange={(event) => onDraftChange({ ...draft, testCommand: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label={i18n.t("skillForm.inputs")}>
                <textarea
                  value={(draft.inputs || []).join("\n")}
                  onChange={(event) => onDraftChange({ ...draft, inputs: splitLines(event.target.value) })}
                  disabled={!editable}
                  className="min-h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.outputs")}>
                <textarea
                  value={(draft.outputs || []).join("\n")}
                  onChange={(event) => onDraftChange({ ...draft, outputs: splitLines(event.target.value) })}
                  disabled={!editable}
                  className="min-h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label={i18n.t("skillForm.sideEffects")}>
                <input
                  value={draft.sideEffects || ""}
                  onChange={(event) => onDraftChange({ ...draft, sideEffects: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.backupRestore")}>
                <input
                  value={draft.backupRestore || ""}
                  onChange={(event) => onDraftChange({ ...draft, backupRestore: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <FieldLabel label={i18n.t("skillForm.requiresEnv")}>
                <input
                  value={(draft.requiresEnv || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, requiresEnv: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.requiresBinaries")}>
                <input
                  value={(draft.requiresBinaries || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, requiresBinaries: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.supportedOs")}>
                <input
                  value={(draft.supportedOs || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, supportedOs: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label={i18n.t("skillForm.supportFiles")}>
                <input
                  value={(draft.supportFiles || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, supportFiles: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label={i18n.t("skillForm.tags")}>
                <input
                  value={(draft.tags || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, tags: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <FieldLabel label={i18n.t("skillForm.instructions")}>
              <textarea
                value={draft.instructions || ""}
                onChange={(event) => onDraftChange({ ...draft, instructions: event.target.value })}
                disabled={!editable}
                className="min-h-40 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </FieldLabel>
            {selectedCheck?.reasons?.length ? (
              <div className="grid gap-1 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground">
                {selectedCheck.reasons.map((reason) => (
                  <div key={reason} className="break-words">
                    {reason}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onNew} disabled={saving}>
              {i18n.t("skills.new")}
            </Button>
            {userSkillSelected ? (
              <Button type="button" variant="danger" onClick={onDelete} disabled={saving}>
                {i18n.t("skills.delete")}
              </Button>
            ) : null}
            <Button type="submit" disabled={!editable || saving || !draft.name}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Save
            </Button>
          </div>
        </form>
        <SkillPackageManagerPanel
          packages={packages}
          packageStore={packageStore}
          loading={packagesLoading}
          message={packageMessage}
          error={packageError}
          governance={packageGovernance}
          audit={packageAudit}
          onRefresh={onRefreshPackages}
          onPreflight={onPreflightPackage}
          onImport={onImportPackage}
          onExport={onExportPackage}
          onSetEnabled={onSetPackageEnabled}
          onUninstall={onUninstallPackage}
          onSetSafeMode={onSetSafeMode}
          onTrustSigner={onTrustSigner}
          onRevokeSigner={onRevokeSigner}
          onBlockPackage={onBlockPackage}
        />
        </div>
      </div>
    </div>
  );
}

function SkillPackageManagerPanel({
  packages,
  packageStore,
  loading,
  message,
  error,
  governance,
  audit,
  onRefresh,
  onPreflight,
  onImport,
  onExport,
  onSetEnabled,
  onUninstall,
  onSetSafeMode,
  onTrustSigner,
  onRevokeSigner,
  onBlockPackage,
}: {
  packages: SkillPackageEntry[];
  packageStore: string;
  loading: boolean;
  message: string;
  error: string;
  governance: Record<string, unknown>;
  audit: Array<Record<string, unknown>>;
  onRefresh: () => void;
  onPreflight: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImport: (packagePath: string) => Promise<unknown>;
  onExport: (skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) => Promise<unknown>;
  onSetEnabled: (skillPackageId: string, enabled: boolean) => Promise<unknown>;
  onUninstall: (skillPackageId: string) => Promise<unknown>;
  onSetSafeMode: (enabled: boolean, reason?: string) => Promise<unknown>;
  onTrustSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onRevokeSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onBlockPackage: (request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) => Promise<unknown>;
}) {
  const [packagePath, setPackagePath] = useState("");
  const [exportSkillName, setExportSkillName] = useState("");
  const [exportPath, setExportPath] = useState("");
  const [exportPrivateKeyPath, setExportPrivateKeyPath] = useState("");
  const [releaseExport, setReleaseExport] = useState(false);
  const [preflight, setPreflight] = useState<SkillPackagePreflight | null>(null);
  const [localMessage, setLocalMessage] = useState("");
  const [localError, setLocalError] = useState("");
  const [packageActionId, setPackageActionId] = useState("");
  const [governanceReason, setGovernanceReason] = useState("");
  const [signerFingerprint, setSignerFingerprint] = useState("");
  const [blockPackageId, setBlockPackageId] = useState("");
  const preview = normalizeSkillPackagePreview(preflight);
  const safeModeEnabled = skillPackageSafeModeEnabled(governance);
  const auditTail = audit.slice(-3).reverse();
  async function runPreflight() {
    if (!packagePath.trim()) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      const payload = await onPreflight(packagePath.trim());
      setPreflight(payload);
      setLocalMessage(i18n.t("package.messages.preflightComplete"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  async function runImport() {
    if (!packagePath.trim()) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      await onImport(packagePath.trim());
      setLocalMessage(i18n.t("package.messages.packageImported"));
      setPreflight(null);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  async function runExport() {
    const privateKeyPath = exportPrivateKeyPath.trim();
    if (!exportSkillName.trim() || !exportPath.trim() || (releaseExport && !privateKeyPath)) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      await onExport(exportSkillName.trim(), exportPath.trim(), releaseExport, privateKeyPath || undefined);
      setLocalMessage(releaseExport ? i18n.t("package.messages.releaseExported") : i18n.t("package.messages.devExported"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  async function runSetEnabled(skillPackageIdValue: string, enabled: boolean) {
    if (!skillPackageIdValue || skillPackageIdValue === "-") {
      return;
    }
    setPackageActionId(skillPackageIdValue);
    setLocalMessage("");
    setLocalError("");
    try {
      await onSetEnabled(skillPackageIdValue, enabled);
      setLocalMessage(enabled ? i18n.t("package.messages.packageEnabled") : i18n.t("package.messages.packageDisabled"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runUninstall(skillPackageIdValue: string) {
    if (!skillPackageIdValue || skillPackageIdValue === "-") {
      return;
    }
    setPackageActionId(skillPackageIdValue);
    setLocalMessage("");
    setLocalError("");
    try {
      await onUninstall(skillPackageIdValue);
      setLocalMessage(i18n.t("package.messages.packageUninstalled"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runSetSafeMode(enabled: boolean) {
    setPackageActionId("safe-mode");
    setLocalMessage("");
    setLocalError("");
    try {
      await onSetSafeMode(enabled, governanceReason.trim() || undefined);
      setLocalMessage(enabled ? i18n.t("package.messages.safeModeEnabled") : i18n.t("package.labels.safeModeDisabled"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runTrustSigner(value = signerFingerprint) {
    const fingerprint = value.trim();
    if (!fingerprint || fingerprint === "-") {
      return;
    }
    setPackageActionId(`signer-${fingerprint}`);
    setLocalMessage("");
    setLocalError("");
    try {
      await onTrustSigner(fingerprint, governanceReason.trim() || undefined);
      setLocalMessage(i18n.t("package.messages.signerTrusted"));
      setSignerFingerprint("");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runRevokeSigner(value = signerFingerprint) {
    const fingerprint = value.trim();
    if (!fingerprint || fingerprint === "-") {
      return;
    }
    setPackageActionId(`signer-${fingerprint}`);
    setLocalMessage("");
    setLocalError("");
    try {
      await onRevokeSigner(fingerprint, governanceReason.trim() || undefined);
      setLocalMessage(i18n.t("package.messages.signerRevoked"));
      setSignerFingerprint("");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runBlockPackage(pkg?: SkillPackageEntry) {
    const id = (pkg ? skillPackageId(pkg) : blockPackageId.trim()).trim();
    const packageSha256 = pkg ? skillPackagePackageSha(pkg) : "";
    if ((!id || id === "-") && !packageSha256) {
      return;
    }
    setPackageActionId(`block-${id || packageSha256}`);
    setLocalMessage("");
    setLocalError("");
    try {
      await onBlockPackage({
        packageId: id && id !== "-" ? id : undefined,
        packageSha256: packageSha256 || undefined,
        reason: governanceReason.trim() || undefined,
      });
      setLocalMessage(i18n.t("package.messages.packageBlocked"));
      setBlockPackageId("");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  const displayMessage = localMessage || message;
  const displayError = localError || error;
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
      <div className="mb-5 flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{i18n.t("package.title")}</div>
        <Badge tone="muted" className="shrink-0">
          {packages.length}
        </Badge>
        <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onRefresh} disabled={loading}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
      </div>

      <div className="grid gap-4">
        <div className="grid gap-3">
          <DataLine label={i18n.t("package.store")} value={packageStore || "-"} />
          {displayMessage ? <Badge tone="ok" className="w-fit">{displayMessage}</Badge> : null}
          {displayError ? <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">{displayError}</div> : null}
        </div>

        <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{i18n.t("package.governance")}</span>
            <Badge tone={safeModeEnabled ? "warn" : "muted"} className="shrink-0">
              {safeModeEnabled ? i18n.t("package.safeMode") : i18n.t("package.standard")}
            </Badge>
            <Badge tone="muted" className="shrink-0">
              {audit.length} audit
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <FieldLabel label={i18n.t("package.reason")}>
              <input
                value={governanceReason}
                onChange={(event) => setGovernanceReason(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
            <Button
              type="button"
              variant={safeModeEnabled ? "outline" : "primary"}
              className="self-end"
              disabled={loading || packageActionId === "safe-mode"}
              onClick={() => void runSetSafeMode(!safeModeEnabled)}
            >
              {packageActionId === "safe-mode" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Shield className="h-4 w-4" />}
              {safeModeEnabled ? i18n.t("package.disableSafeMode") : i18n.t("package.enableSafeMode")}
            </Button>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto]">
            <FieldLabel label={i18n.t("package.signerFingerprint")}>
              <input
                value={signerFingerprint}
                onChange={(event) => setSignerFingerprint(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 font-mono text-xs outline-none focus:border-primary"
              />
            </FieldLabel>
            <Button type="button" variant="outline" className="self-end" disabled={loading || !signerFingerprint.trim()} onClick={() => void runTrustSigner()}>
              <Check className="h-4 w-4" />
              {i18n.t("package.trust")}
            </Button>
            <Button type="button" variant="danger" className="self-end" disabled={loading || !signerFingerprint.trim()} onClick={() => void runRevokeSigner()}>
              <X className="h-4 w-4" />
              {i18n.t("package.revoke")}
            </Button>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <FieldLabel label={i18n.t("package.packageId")}>
              <input
                value={blockPackageId}
                onChange={(event) => setBlockPackageId(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
            <Button type="button" variant="danger" className="self-end" disabled={loading || !blockPackageId.trim()} onClick={() => void runBlockPackage()}>
              <EyeOff className="h-4 w-4" />
              {i18n.t("package.block")}
            </Button>
          </div>
          {auditTail.length ? (
            <div className="grid gap-1 border-t border-border pt-3 text-xs text-muted-foreground">
              {auditTail.map((item, index) => (
                <div key={`${String(item.event || i18n.t("package.audit"))}-${index}`} className="flex min-w-0 gap-2">
                  <span className="shrink-0 font-mono">{String(item.event || i18n.t("package.audit"))}</span>
                  <span className="min-w-0 truncate">{String(item.skill_id || item.signer_fingerprint || item.package_id || item.reason || "")}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto]">
          <FieldLabel label={i18n.t("package.packagePath")}>
            <input
              value={packagePath}
              onChange={(event) => setPackagePath(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </FieldLabel>
          <Button type="button" variant="outline" className="self-end" disabled={loading || !packagePath.trim()} onClick={() => void runPreflight()}>
            <Eye className="h-4 w-4" />
            {i18n.t("package.preflight")}
          </Button>
          <Button type="button" className="self-end" disabled={loading || !packagePath.trim()} onClick={() => void runImport()}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            Import
          </Button>
        </div>

        {preview ? (
          <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{skillPackageTitle(preview)}</span>
              {skillPackageLabels(preview).map((label) => (
                <Badge key={label} tone={skillPackageLabelTone(label)} className="h-6 shrink-0">
                  {label}
                </Badge>
              ))}
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <DataLine label="Version" value={String(preview.version || "-")} />
              <DataLine label={i18n.t("package.tableRisk")} value={skillPackageRisk(preview)} />
              <DataLine label="Signer" value={skillPackageSigner(preview)} mono />
            </div>
            <OutputBlock label="Permissions" value={skillPackagePermissions(preview).join("\n")} />
            {preview.governance ? <OutputBlock label={i18n.t("package.governance")} value={formatPayload(preview.governance)} /> : null}
            {preview.dryRun ? <OutputBlock label="Dry Run" value={formatPayload(preview.dryRun)} /> : null}
            {preview.manifest ? <OutputBlock label="Manifest" value={formatPayload(preview.manifest)} /> : null}
          </div>
        ) : null}

        <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <FieldLabel label={i18n.t("package.skillName")}>
              <input
                value={exportSkillName}
                onChange={(event) => setExportSkillName(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
            <FieldLabel label={i18n.t("package.outputPath")}>
              <input
                value={exportPath}
                onChange={(event) => setExportPath(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <FieldLabel label={i18n.t("package.privateKeyPath")}>
              <input
                value={exportPrivateKeyPath}
                onChange={(event) => setExportPrivateKeyPath(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
            <label className="flex h-10 min-w-0 items-center gap-2 self-end rounded-md border border-border px-3 text-sm text-muted-foreground">
              <input type="checkbox" checked={releaseExport} onChange={(event) => setReleaseExport(event.target.checked)} />
              <span className="truncate">{i18n.t("skills.signedRelease")}</span>
            </label>
          </div>
          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              disabled={loading || !exportSkillName.trim() || !exportPath.trim() || (releaseExport && !exportPrivateKeyPath.trim())}
              onClick={() => void runExport()}
            >
              <Copy className="h-4 w-4" />
              {i18n.t("package.export")}
            </Button>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-[minmax(0,1fr)_76px_150px_minmax(300px,390px)] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span className="truncate">{i18n.t("subagent.roles.outfitPackageInspection")}</span>
            <span className="truncate">{i18n.t("package.tableRisk")}</span>
            <span className="truncate">{i18n.t("connector.status")}</span>
            <span className="truncate">{i18n.t("package.tableActions")}</span>
          </div>
          {packages.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">{i18n.t("package.noPackages")}</div>
          ) : null}
          {packages.map((pkg, index) => {
            const id = skillPackageId(pkg);
            const enabled = skillPackageEnabled(pkg);
            const busy = loading || packageActionId === id;
            return (
              <div key={`${id}-${index}`} className="grid grid-cols-[minmax(0,1fr)_76px_150px_minmax(300px,390px)] gap-2 border-b border-border/60 px-3 py-2 text-xs last:border-b-0">
                <div className="min-w-0">
                  <div className="truncate font-medium">{skillPackageTitle(pkg)}</div>
                  <div className="truncate text-muted-foreground">{id}</div>
                </div>
                <span className="truncate">{skillPackageRisk(pkg)}</span>
                <div className="flex min-w-0 flex-wrap gap-1">
                  {skillPackageLabels(pkg).map((label) => (
                    <Badge key={label} tone={skillPackageLabelTone(label)} className="h-5 px-1.5 text-[10px]">
                      {label}
                    </Badge>
                  ))}
                </div>
                <div className="flex min-w-0 flex-wrap justify-end gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2 text-xs"
                    disabled={busy || id === "-"}
                    onClick={() => void runSetEnabled(id, !enabled)}
                  >
                    {packageActionId === id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : enabled ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    {enabled ? i18n.t("package.disable") : i18n.t("package.enable")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2 text-xs"
                    disabled={busy || skillPackageSigner(pkg) === "-"}
                    onClick={() => void runTrustSigner(skillPackageSigner(pkg))}
                  >
                    <Check className="h-3.5 w-3.5" />
                    {i18n.t("package.trust")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2 text-xs"
                    disabled={busy || skillPackageSigner(pkg) === "-"}
                    onClick={() => void runRevokeSigner(skillPackageSigner(pkg))}
                  >
                    <X className="h-3.5 w-3.5" />
                    {i18n.t("package.revoke")}
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    className="h-8 px-2 text-xs"
                    disabled={busy || id === "-"}
                    onClick={() => void runBlockPackage(pkg)}
                  >
                    <EyeOff className="h-3.5 w-3.5" />
                    {i18n.t("package.block")}
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    className="h-8 px-2 text-xs"
                    disabled={busy || id === "-"}
                    onClick={() => void runUninstall(id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {i18n.t("package.uninstall")}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function normalizeSkillPackagePreview(payload: SkillPackagePreflight | null): SkillPackageEntry | null {
  if (!payload) {
    return null;
  }
  return payload.preview || payload;
}

function skillPackageId(pkg: SkillPackageEntry): string {
  return String(pkg.id || pkg.name || pkg.manifest?.id || "-");
}

function skillPackageTitle(pkg: SkillPackageEntry): string {
  return String(pkg.title || pkg.manifest?.title || skillPackageId(pkg));
}

function skillPackageRisk(pkg: SkillPackageEntry): string {
  return String(pkg.risk_level || pkg.riskLevel || "low");
}

function skillPackageSigner(pkg: SkillPackageEntry): string {
  return String(pkg.signer_fingerprint || pkg.signerFingerprint || "-");
}

function skillPackagePackageSha(pkg: SkillPackageEntry): string {
  return String(pkg.package_sha256 || pkg.packageSha256 || "");
}

function skillPackageEnabled(pkg: SkillPackageEntry): boolean {
  return pkg.enabled !== false && pkg.available !== false;
}

function skillPackagePermissions(pkg: SkillPackageEntry): string[] {
  const permissions = pkg.permissions || [];
  const tiers = pkg.permission_tiers || pkg.permissionTiers || {};
  const tierValues = Object.entries(tiers).flatMap(([tier, items]) => (items || []).map((item) => `${tier}: ${item}`));
  return [...permissions, ...tierValues];
}

function skillPackageLabels(pkg: SkillPackageEntry): string[] {
  const labels: string[] = [];
  const status = String(pkg.signature_status || pkg.signatureStatus || "").toLowerCase();
  const errorText = [...(pkg.errors || []), ...(pkg.warnings || [])].join(" ").toLowerCase();
  const governance = skillPackageGovernance(pkg);
  const signerStatus = String(governance.signerTrustStatus || governance.signer_trust_status || "").toLowerCase();
  const importAllowed = governance.importAllowed ?? governance.import_allowed;
  const enableAllowed = governance.enableAllowed ?? governance.enable_allowed;
  const safeMode = skillPackageSafeMode(governance);
  if (pkg.source === "builtin") {
    labels.push("Built-in");
  }
  if (status === "signed") {
    labels.push("Signed");
  } else if (status === "dev") {
    labels.push("Dev");
  } else {
    labels.push("Unsigned");
  }
  if (errorText.includes("signature")) {
    labels.push("Signature mismatch");
  }
  if (signerStatus === "trusted") {
    labels.push("Trusted signer");
  } else if (signerStatus === "revoked") {
    labels.push("Revoked signer");
  } else if (signerStatus === "untrusted") {
    labels.push("Untrusted signer");
  }
  if (safeMode.defaultEnabled === false || safeMode.disablesRiskLevel === true || safeMode.disables_risk_level === true) {
    labels.push("Safe Mode disabled");
  }
  if (importAllowed === false) {
    labels.push("Import blocked");
  }
  if (enableAllowed === false) {
    labels.push("Enable blocked");
  }
  if (pkg.dryRun) {
    labels.push("Dry run");
  }
  if (pkg.enabled === false || pkg.available === false || errorText.includes("blocked")) {
    labels.push("Blocked");
  }
  return [...new Set(labels)];
}

function skillPackageLabelTone(label: string): "ok" | "warn" | "danger" | "muted" {
  if (label === "Signed" || label === "Built-in" || label === "Trusted signer") {
    return "ok";
  }
  if (label === "Signature mismatch" || label === "Blocked" || label === "Revoked signer" || label.includes("blocked")) {
    return "danger";
  }
  if (label === "Unsigned" || label === "Dev" || label === "Untrusted signer" || label === "Safe Mode disabled") {
    return "warn";
  }
  return "muted";
}

function skillPackageGovernance(pkg: SkillPackageEntry): Record<string, unknown> {
  return asRecord(pkg.governance) || {};
}

function skillPackageSafeMode(governance: Record<string, unknown>): Record<string, unknown> {
  return asRecord(governance.safeMode) || asRecord(governance.safe_mode) || {};
}

function skillPackageSafeModeEnabled(governance: Record<string, unknown>): boolean {
  const safeMode = skillPackageSafeMode(governance);
  return safeMode.enabled === true;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function FieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-2 text-sm">
      <span className="truncate font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function DoctorWorkspace({
  report,
  loading,
  message,
  repairingUnityBridge,
  exportingSupportBundle,
  onRefresh,
  onRepairUnityBridge,
  onOpenSettings,
  onExportSupportBundle,
  onCopy,
}: {
  report: DoctorReport | null;
  loading: boolean;
  message: string;
  repairingUnityBridge: boolean;
  exportingSupportBundle: boolean;
  onRefresh: () => void;
  onRepairUnityBridge: () => void;
  onOpenSettings: () => void;
  onExportSupportBundle: () => void;
  onCopy: () => void;
}) {
  const { t } = useTranslation();
  const summary = report?.summary;
  const checks = report?.checks ?? [];
  const suggestedFixes = checks.filter((check) => check.status !== "ok" && (check.fixCommand || check.howToFix)).slice(0, 8);
  const groupedChecks = useMemo(() => groupDoctorChecks(checks, report?.sections), [checks, report?.sections]);
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <section className="rounded-xl border border-border bg-card p-5 shadow-panel">
          <div className="flex min-w-0 items-center gap-3">
            <Shield className="h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-lg font-semibold">{t("doctor.title")}</h1>
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="truncate">{report?.version || "runtime"}</span>
                {report?.scope ? <span className="truncate">{report.scope}</span> : null}
                {report?.selectedUnityEnvironment?.label ? <span className="truncate">{report.selectedUnityEnvironment.label}</span> : null}
              </div>
            </div>
            <Badge tone={report?.ok ? "ok" : "warn"} className="shrink-0">
              {report?.ok ? t("doctor.ready") : t("connector.needsAttention")}
            </Badge>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <DoctorSummaryTile label={t("doctor.ok")} value={summary?.okCount ?? 0} tone="ok" />
            <DoctorSummaryTile label={t("doctor.warning")} value={summary?.warningCount ?? 0} tone="warn" />
            <DoctorSummaryTile label={t("doctor.error")} value={summary?.errorCount ?? 0} tone="danger" />
            <DoctorSummaryTile label={t("doctor.unknown")} value={summary?.unknownCount ?? 0} tone="muted" />
          </div>
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            {message ? (
              <Badge tone="ok" className="mr-auto shrink-0">
                {message}
              </Badge>
            ) : null}
            <Button type="button" variant="outline" onClick={onOpenSettings}>
              <Settings className="h-4 w-4" />
              {t("doctor.settings")}
            </Button>
            <Button type="button" variant="outline" onClick={onExportSupportBundle} disabled={exportingSupportBundle}>
              {exportingSupportBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Support Bundle
            </Button>
            <Button type="button" variant="outline" onClick={onCopy} disabled={!report}>
              <Copy className="h-4 w-4" />
              {t("connector.copy")}
            </Button>
            <Button type="button" onClick={onRefresh} disabled={loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              Retry
            </Button>
          </div>
        </section>

        {suggestedFixes.length > 0 ? (
          <section className="rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-4 flex min-w-0 items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              <h2 className="truncate text-sm font-semibold">{t("doctor.suggestedFixes")}</h2>
              <Badge tone="warn" className="ml-auto shrink-0">
                {suggestedFixes.length}
              </Badge>
            </div>
            <div className="grid gap-2">
              {suggestedFixes.map((check) => (
                <div key={`fix-${check.id}`} className="grid gap-1 rounded-lg border border-border bg-background px-3 py-2 text-sm">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-medium">{check.title}</span>
                    <Badge tone={doctorTone(check.status)} className="h-6 shrink-0">
                      {doctorStatusLabel(check.status)}
                    </Badge>
                  </div>
                  <div className="break-words text-xs text-muted-foreground">{check.fixCommand || check.howToFix}</div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <div className="grid gap-6">
          {checks.length === 0 ? (
            <div className="rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground shadow-panel">
              {loading ? t("doctor.running") : t("doctor.noResults")}
            </div>
          ) : null}
          {groupedChecks.map((group) => (
            <section key={group.name} className="grid gap-3">
              <div className="flex min-w-0 items-center gap-2 px-1">
                <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{group.name}</h2>
                <Badge tone={group.summary.errorCount > 0 ? "danger" : group.summary.warningCount > 0 ? "warn" : "muted"} className="shrink-0">
                  {group.items.length}
                </Badge>
              </div>
              {group.items.map((check) => (
                <DoctorCheckRow
                  key={check.id}
                  check={check}
                  repairingUnityBridge={repairingUnityBridge}
                  onRepairUnityBridge={onRepairUnityBridge}
                />
              ))}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

function groupDoctorChecks(
  checks: DoctorCheck[],
  sections?: DoctorReport["sections"],
): Array<{ name: string; summary: { okCount: number; warningCount: number; errorCount: number; unknownCount: number }; items: DoctorCheck[] }> {
  const byId = new Map(checks.map((check) => [check.id, check]));
  if (sections?.length) {
    return sections
      .map((section) => ({
        name: section.name,
        summary: section.summary,
        items: section.checkIds.map((id) => byId.get(id)).filter((item): item is DoctorCheck => Boolean(item)),
      }))
      .filter((section) => section.items.length > 0);
  }
  const grouped = new Map<string, DoctorCheck[]>();
  for (const check of checks) {
    const section = check.section || "Doctor";
    grouped.set(section, [...(grouped.get(section) || []), check]);
  }
  return [...grouped.entries()].map(([name, items]) => ({
    name,
    summary: {
      okCount: items.filter((check) => check.status === "ok").length,
      warningCount: items.filter((check) => check.status === "warning").length,
      errorCount: items.filter((check) => check.status === "error").length,
      unknownCount: items.filter((check) => check.status === "unknown").length,
    },
    items,
  }));
}

function DoctorSummaryTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "ok" | "warn" | "danger" | "muted";
}) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-background px-3 py-3">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <Badge tone={tone} className="h-6 px-2">
          {value}
        </Badge>
      </div>
    </div>
  );
}

function DoctorCheckRow({
  check,
  repairingUnityBridge,
  onRepairUnityBridge,
}: {
  check: DoctorCheck;
  repairingUnityBridge: boolean;
  onRepairUnityBridge: () => void;
}) {
  const { t } = useTranslation();
  const openByDefault = check.status === "error" || check.status === "warning";
  const [open, setOpen] = useState(openByDefault);
  const tone = doctorTone(check.status);
  const canRepairUnityBridge = check.status !== "ok" && Boolean(check.fixable) && (check.actions || []).includes("repair_unity_bridge");
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full min-w-0 items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{check.title}</span>
        <Badge tone={tone} className="shrink-0">
          {doctorStatusLabel(check.status)}
        </Badge>
      </button>
      {open ? (
        <div className="grid gap-3 border-t border-border px-4 py-4">
          <DataLine label={t("doctor.whatFailed")} value={check.whatFailed || (check.status === "ok" ? "-" : check.message)} />
          <DataLine label={t("doctor.why")} value={check.whyItMatters || "-"} />
          <DataLine label={t("doctor.howToFix")} value={check.howToFix || "-"} />
          {check.fixCommand ? <DataLine label={t("doctor.fix")} value={check.fixCommand} /> : null}
          {canRepairUnityBridge ? (
            <div className="flex justify-end">
              <Button type="button" variant="outline" className="h-8 px-3 text-xs" onClick={onRepairUnityBridge} disabled={repairingUnityBridge}>
                {repairingUnityBridge ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
                Repair bridge
              </Button>
            </div>
          ) : null}
          <DataLine label={t("doctor.message")} value={check.message || "-"} />
          {check.detail !== undefined ? <OutputBlock label={t("doctor.detail")} value={formatPayload(check.detail)} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function doctorTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "ok") {
    return "ok";
  }
  if (status === "warning") {
    return "warn";
  }
  if (status === "error") {
    return "danger";
  }
  return "muted";
}

function doctorStatusLabel(status: string): string {
  switch (status) {
    case "ok":
      return "OK";
    case "warning":
      return "Warning";
    case "error":
      return "Error";
    default:
      return "Unknown";
  }
}

function ConversationCard({ item, onOpenSettings }: { item: ConversationItem; onOpenSettings?: () => void }) {
  const { t } = useTranslation();
  if (item.type === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[78%] rounded-2xl bg-primary px-4 py-3 text-sm text-primary-foreground">
          <p className="whitespace-pre-wrap break-words">{item.text}</p>
        </div>
      </div>
    );
  }

  if (item.type === "error") {
    return (
      <div className="rounded-lg border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive/75">
        <span className="break-words">{item.text}</span>
      </div>
    );
  }

  if (item.type === "result") {
    return <ShellResultCard title={item.error === "rejected" ? t("agent.rejected") : t("agent.executionResult")} result={item.result} error={item.error} />;
  }

  if (item.type === "compact") {
    return (
      <div className="rounded-xl border border-dashed border-border bg-muted/40 px-4 py-3">
        <div className="mb-2 text-xs font-medium text-muted-foreground">{t("agent.compactedHistory")}</div>
        <pre className="app-scrollbar max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">{item.text}</pre>
      </div>
    );
  }

  if (item.type === "subagent") {
    const task = item.task;
    return (
      <div className="flex justify-start">
        <div className="w-full max-w-[85%] space-y-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm shadow-panel">
          <div className="flex min-w-0 items-center gap-2">
            <Bot className="h-4 w-4 shrink-0 text-primary" />
            <span className="min-w-0 flex-1 truncate font-medium">
              {task.displayName || t("agent.subagentTask")} · {subAgentRoleLabel(task.role)}
            </span>
            <Badge tone={subAgentStatusTone(task.status)} className="shrink-0">
              {task.status}
            </Badge>
          </div>
          <p className="whitespace-pre-wrap break-words leading-relaxed text-muted-foreground">
            {task.summary || task.error || task.task || t("agent.noSummaryReturned")}
          </p>
          {task.result !== undefined ? <OutputBlock label="Result" value={formatPayload(task.result)} /> : null}
        </div>
      </div>
    );
  }

  const response = item.response;
  const shell = response.shell;
  const skill = response.skill;
  const awaitingApproval = shell?.status === "pending_approval";
  const localIdle =
    response.plan.planner === "deterministic-local" &&
    response.plan.nextStep === "await_user_instruction" &&
    !response.plan.skillTool &&
    !response.plan.shellCommand;
  const nextStep = response.plan.nextStep || "";
  const showIntent = Boolean(nextStep) && nextStep !== "await_user_instruction" && nextStep !== "done";

  if (localIdle) {
    return (
      <div className="flex justify-start">
        <div className="max-w-[85%] space-y-3 rounded-2xl border border-border bg-card px-4 py-3 text-sm shadow-panel">
          <p className="text-muted-foreground">
            {t("agent.keywordPlannerHint1")}
          </p>
          <p className="text-muted-foreground">
            {t("agent.keywordPlannerHint2")}
          </p>
          <Button type="button" variant="outline" className="h-8 px-3 text-xs" onClick={() => onOpenSettings?.()}>
            <Settings className="mr-1 h-3.5 w-3.5" />
            {t("agent.openSettings")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[85%] space-y-2">
        <div className="rounded-2xl border border-border bg-card px-4 py-3 text-sm shadow-panel">
          <p className="whitespace-pre-wrap break-words leading-relaxed">{response.plan.reply || response.plan.summary}</p>
          {showIntent ? (
            <p className="mt-2 flex items-center gap-1.5 text-xs text-primary">
              <Sparkles className="h-3.5 w-3.5 shrink-0" />
              <span>
                {t("agent.willDoNext", { step: displayStep(nextStep) })}
                {response.plan.skillTool ? `：${response.plan.skillTool}` : ""}
              </span>
            </p>
          ) : null}
          <div className="mt-2 flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>{response.plan.plannerLabel || displayPlanner(response.plan.planner)}</span>
            {item.elapsedSeconds ? <span>{t("agent.elapsed", { time: formatDuration(item.elapsedSeconds) })}</span> : null}
          </div>
        </div>

        <ReasoningTracePanel
          trace={response.reasoning}
          fallbackLabel={response.plan.plannerLabel || displayPlanner(response.plan.planner)}
          elapsedSeconds={item.elapsedSeconds}
        />

        {shell?.classification ? (
          <RunRow
            icon="shell"
            title={shell.classification.command}
            statusTone={shell.result ? (shell.result.ok ? "ok" : "danger") : awaitingApproval ? "warn" : riskTone(shell.classification.risk)}
            statusLabel={
              shell.result
                ? t("shell.exitCodeDuration", { code: shell.result.exitCode, time: formatDuration(shell.result.durationSeconds) })
                : awaitingApproval
                  ? t("shell.awaitConfirmation")
                  : t("shell.riskLevel", { level: shell.classification.risk })
            }
          >
            <DataLine label={t("approval.directory")} value={shell.classification.cwd} />
            <div className="overflow-hidden rounded-md border border-border bg-muted/50 p-3 font-mono text-xs">
              <pre className="whitespace-pre-wrap break-words">{shell.classification.command}</pre>
            </div>
            {shell.classification.reasons.length ? (
              <div className="flex flex-wrap gap-2">
                {shell.classification.reasons.map((reason) => (
                  <Badge key={reason} tone="muted" className="max-w-full">
                    <span className="truncate">{reason}</span>
                  </Badge>
                ))}
              </div>
            ) : null}
            {shell.result ? (
              <>
                <DataLine label={t("shell.elapsed")} value={formatDuration(shell.result.durationSeconds)} />
                <OutputBlock label={t("shell.output")} value={shell.result.stdout} />
                {shell.result.stderr ? <OutputBlock label={t("shell.errorOutput")} value={shell.result.stderr} danger /> : null}
              </>
            ) : null}
          </RunRow>
        ) : null}

        {skill ? (
          <RunRow icon="skill" title={skill.tool || t("skills.skillCall")} statusTone={skillTone(skill)} statusLabel={displaySkillStatus(skill.status)}>
            <DataLine label={t("skills.tool")} value={skill.tool || "-"} mono />
            {skill.category ? <DataLine label={t("skills.category")} value={skill.category} /> : null}
            {skill.error ? <DataLine label={t("skills.error")} value={skill.error} /> : null}
            {skill.result !== undefined ? <OutputBlock label={t("skills.data")} value={formatPayload(skill.result)} /> : null}
          </RunRow>
        ) : null}

        {awaitingApproval ? (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span>{t("approval.awaitingInline")}</span>
          </div>
        ) : null}
        {shell?.error ? (
          <RunRow icon="shell" title={t("shell.executionError")} statusTone="danger" statusLabel={t("skillStatus.failed")}>
            <DataLine label={t("skills.error")} value={shell.error} />
          </RunRow>
        ) : null}
      </div>
    </div>
  );
}

function ReasoningTracePanel({
  trace,
  fallbackLabel,
  elapsedSeconds,
}: {
  trace?: AgentReasoningTrace;
  fallbackLabel: string;
  elapsedSeconds?: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const items = (trace?.items || []).filter((item) => (item.text || "").trim() || item.opaque);
  if (!items.length) {
    return null;
  }
  const status = thinkingStatusForModel(trace?.provider || trace?.providerLabel || fallbackLabel, trace?.model || "");
  const provider = trace?.providerLabel || trace?.provider || fallbackLabel || "model";
  const model = trace?.model || "";
  const title = model ? `${status} · ${provider} · ${model}` : `${status} · ${provider}`;
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{title}</span>
        {elapsedSeconds ? <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{formatDuration(elapsedSeconds)}</span> : null}
        <Badge tone={trace?.redacted ? "warn" : "muted"} className="shrink-0">
          {items.length}
        </Badge>
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <DataLine label="Provider" value={provider} />
          {model ? <DataLine label="Model" value={model} mono /> : null}
          {trace?.source ? <DataLine label="Source" value={trace.source} mono /> : null}
          {items.map((item, index) => (
            <OutputBlock
              key={`${item.title || item.kind || "reasoning"}-${index}`}
              label={item.title || item.kind || t("thinking.reasoning")}
              value={item.text || (item.opaque ? "Opaque reasoning item retained by provider response." : "")}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RunRow({
  icon,
  title,
  statusTone,
  statusLabel,
  children,
}: {
  icon: "shell" | "skill";
  title: string;
  statusTone: "ok" | "warn" | "danger" | "muted";
  statusLabel: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const Icon = icon === "shell" ? TerminalSquare : Wrench;
  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/50"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <Icon className="h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="min-w-0 flex-1 truncate font-mono text-xs">{title}</span>
        <Badge tone={statusTone} className="shrink-0">
          {statusLabel}
        </Badge>
      </button>
      {open ? <div className="space-y-3 border-t border-border px-3 py-3">{children}</div> : null}
    </div>
  );
}

function RunningIndicator({ startedAt, text, provider, model }: { startedAt: number; text: string; provider: string; model: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  const status = thinkingStatusForModel(provider, model);
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[85%] min-w-0 items-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm text-muted-foreground shadow-panel">
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
        <span className="shrink-0 font-medium text-foreground">{status}</span>
        <span className="min-w-0 truncate text-muted-foreground">「{text}」</span>
        <span className="shrink-0 font-mono text-xs">{formatDuration(seconds)}</span>
      </div>
    </div>
  );
}

function ApprovalCard({
  approval,
  loading,
  onApprove,
  onReject,
}: {
  approval: AgentApproval;
  loading: boolean;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 shadow-panel">
      <div className="flex min-w-0 items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
        <div className="truncate text-sm font-semibold">{t("header.pendingApprovals")}</div>
        <Badge tone="warn" className="ml-auto shrink-0">
          {approval.riskLevel || "high"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-3">
        <DataLine label={t("approval.command")} value={approval.preview?.command || "-"} mono />
        <DataLine label={t("approval.directory")} value={approval.preview?.cwd || "-"} />
        <DataLine label={t("approval.reason")} value={approval.reason || "-"} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4" />
          {t("approval.reject")}
        </Button>
        <Button variant="primary" disabled={loading} onClick={() => onApprove(approval.id)}>
          <Check className="h-4 w-4" />
          {t("approval.approve")}
        </Button>
      </div>
    </section>
  );
}

function ShellResultCard({ title, result, error }: { title: string; result?: AgentShellResult; error?: string }) {
  const { t } = useTranslation();
  return (
    <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 items-center gap-2">
        <TerminalSquare className="h-4 w-4 shrink-0 text-primary" />
        <div className="truncate text-sm font-semibold">{title}</div>
        {result ? (
          <Badge tone={result.ok ? "ok" : "danger"} className="ml-auto shrink-0">
            {t("shell.exitCode", { code: result.exitCode })}
          </Badge>
        ) : null}
      </div>
      {error ? <DataLine label={t("skills.error")} value={error} /> : null}
      {result ? (
        <div className="grid gap-3">
          <DataLine label={t("shell.elapsed")} value={`${result.durationSeconds}s`} />
          <OutputBlock label={t("shell.output")} value={result.stdout} />
          {result.stderr ? <OutputBlock label={t("shell.errorOutput")} value={result.stderr} danger /> : null}
        </div>
      ) : null}
    </section>
  );
}

function OutputBlock({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <pre
        className={cn(
          "max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-muted/50 p-3 font-mono text-xs",
          danger ? "text-destructive" : "text-foreground",
        )}
      >
        {value || "-"}
      </pre>
    </div>
  );
}

function DataLine({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="grid min-w-0 grid-cols-[120px_minmax(0,1fr)] gap-3 text-sm">
      <div className="truncate text-muted-foreground">{label}</div>
      <div className={cn("min-w-0 break-words font-medium", mono && "font-mono text-xs")}>{value}</div>
    </div>
  );
}

function SidebarSection({
  title,
  children,
  collapsed = false,
  onToggleCollapse,
}: {
  title: string;
  children: ReactNode;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="mt-8 min-w-0 max-md:hidden">
      {onToggleCollapse ? (
        <button
          type="button"
          onClick={onToggleCollapse}
          title={collapsed ? t("common.expand") : t("common.collapse")}
          className="group mb-3 flex w-full items-center gap-1 px-2 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          <span className="truncate">{title}</span>
          <span className={cn("shrink-0", collapsed ? "" : "opacity-0 group-hover:opacity-100")}>
            {collapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </span>
        </button>
      ) : (
        <div className="mb-3 px-2 text-xs font-medium text-muted-foreground">{title}</div>
      )}
      {collapsed ? null : <div className="space-y-1">{children}</div>}
    </section>
  );
}

function SidebarProject({
  name,
  meta,
  active = false,
  collapsed = false,
  hasChats = false,
  pinned = false,
  renaming = false,
  renameDraft = "",
  onClick,
  onToggleCollapse,
  onOpenMenu,
  onContextMenu,
  onRenameChange,
  onRenameCommit,
}: {
  name: string;
  meta?: string;
  active?: boolean;
  collapsed?: boolean;
  hasChats?: boolean;
  pinned?: boolean;
  renaming?: boolean;
  renameDraft?: string;
  onClick?: () => void;
  onToggleCollapse?: () => void;
  onOpenMenu?: (event: ReactMouseEvent) => void;
  onContextMenu?: (event: ReactMouseEvent) => void;
  onRenameChange?: (value: string) => void;
  onRenameCommit?: (cancel?: boolean) => void;
}) {
  if (renaming) {
    return (
      <div className="flex h-11 w-full min-w-0 items-center rounded-md bg-muted px-2">
        <input
          autoFocus
          value={renameDraft}
          onChange={(event) => onRenameChange?.(event.target.value)}
          onBlur={() => onRenameCommit?.()}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onRenameCommit?.();
            }
            if (event.key === "Escape") {
              onRenameCommit?.(true);
            }
          }}
          className="h-7 w-full min-w-0 rounded border border-primary/40 bg-background px-2 text-sm outline-none focus:border-primary"
        />
      </div>
    );
  }
  return (
    <div
      onContextMenu={onContextMenu}
      className={cn(
        "group flex h-11 w-full min-w-0 items-center rounded-md pr-1 text-sm transition-colors",
        active ? "bg-muted text-foreground" : "text-muted-foreground",
        onClick ? "hover:bg-muted hover:text-foreground" : "cursor-default",
      )}
    >
      <button onClick={onClick} disabled={!onClick} className="flex h-full min-w-0 flex-1 items-center gap-3 px-3 text-left">
        <Folder className="h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate">{name}</span>
        {pinned ? <Pin className="h-3.5 w-3.5 shrink-0 text-primary/60" /> : null}
        {meta ? <span className="max-w-[78px] shrink-0 truncate text-xs text-muted-foreground">{meta}</span> : null}
      </button>
      {onOpenMenu ? (
        <button
          type="button"
          title={i18n.t("project.menu")}
          onClick={onOpenMenu}
          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:bg-background hover:text-foreground group-hover:opacity-100"
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onToggleCollapse ? (
        <button
          type="button"
          title={collapsed ? i18n.t("project.expandChats") : i18n.t("project.collapseChats")}
          onClick={(event) => {
            event.stopPropagation();
            onToggleCollapse();
          }}
          className={cn(
            "shrink-0 rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground",
            collapsed || hasChats ? "" : "opacity-0 group-hover:opacity-100",
          )}
        >
          {collapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
        </button>
      ) : null}
    </div>
  );
}

function SidebarChat({
  title,
  active = false,
  indent = false,
  pinned = false,
  renaming = false,
  renameDraft = "",
  onClick,
  onTogglePin,
  onDelete,
  onContextMenu,
  onRenameChange,
  onRenameCommit,
}: {
  title: string;
  active?: boolean;
  indent?: boolean;
  pinned?: boolean;
  renaming?: boolean;
  renameDraft?: string;
  onClick: () => void;
  onTogglePin?: () => void;
  onDelete?: () => void;
  onContextMenu?: (event: ReactMouseEvent) => void;
  onRenameChange?: (value: string) => void;
  onRenameCommit?: (cancel?: boolean) => void;
}) {
  const { t } = useTranslation();
  if (renaming) {
    return (
      <div className={cn("flex h-9 w-full min-w-0 items-center rounded-md bg-muted px-2", indent ? "pl-9" : "")}>
        <input
          autoFocus
          value={renameDraft}
          onChange={(event) => onRenameChange?.(event.target.value)}
          onBlur={() => onRenameCommit?.()}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onRenameCommit?.();
            }
            if (event.key === "Escape") {
              onRenameCommit?.(true);
            }
          }}
          className="h-7 w-full min-w-0 rounded border border-primary/40 bg-background px-2 text-sm outline-none focus:border-primary"
        />
      </div>
    );
  }
  return (
    <div
      onContextMenu={onContextMenu}
      className={cn(
        "group flex h-9 w-full min-w-0 items-center rounded-md pr-1 text-sm transition-colors",
        indent ? "pl-6" : "",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <button onClick={onClick} className="flex h-full min-w-0 flex-1 items-center gap-3 px-3 text-left">
        {indent ? null : <MessageSquare className="h-4 w-4 shrink-0" />}
        <span className="min-w-0 flex-1 truncate">{title}</span>
      </button>
      {pinned ? <Pin className="h-3.5 w-3.5 shrink-0 text-primary/60 group-hover:hidden" /> : null}
      <div className="hidden shrink-0 items-center gap-0.5 group-hover:flex">
        <button
          type="button"
          title={pinned ? t("contextMenu.unpinChat") : "置顶"}
          onClick={(event) => {
            event.stopPropagation();
            onTogglePin?.();
          }}
          className="rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground"
        >
          <Pin className={cn("h-3.5 w-3.5", pinned ? "text-primary" : "")} />
        </button>
        <button
          type="button"
          title={t("contextMenu.permanentDelete")}
          onClick={(event) => {
            event.stopPropagation();
            onDelete?.();
          }}
          className="rounded p-1 text-destructive/60 hover:bg-destructive/10 hover:text-destructive"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

function projectKey(project: { path?: string; name?: string }): string {
  return project.path || project.name || "";
}

function sortChatsByPin(list: ChatThread[]): ChatThread[] {
  return [...list].sort((a, b) => Number(b.pinned ?? false) - Number(a.pinned ?? false));
}

const SKILL_DOMAIN_RULES: Array<{ label: string; pattern: RegExp }> = [
  { label: i18n.t("skills.domains.roslyn"), pattern: /roslyn/i },
  { label: i18n.t("skills.domains.face"), pattern: /blendshape|face|expression/i },
  { label: i18n.t("skills.domains.material"), pattern: /material|shader|texture/i },
  { label: i18n.t("skills.domains.clothing"), pattern: /clothing|outfit|wardrobe|gesture|\bfx\b|fx_/i },
  { label: i18n.t("skills.domains.parameter"), pattern: /parameter|param_/i },
  { label: i18n.t("skills.domains.screenshot"), pattern: /screenshot|capture|scene_view|vision|game_view/i },
  { label: i18n.t("skills.domains.package"), pattern: /package|vpm|addon|modular/i },
  { label: i18n.t("skills.domains.approval"), pattern: /approval|approve|backup|restore|rollback/i },
  { label: i18n.t("skills.domains.shell"), pattern: /shell|command|console|debug/i },
  { label: i18n.t("skills.domains.diagnostics"), pattern: /\blog|health|diagno|status|check/i },
  { label: i18n.t("skills.domains.avatarScan"), pattern: /scan|avatar|inventory|control|animation|toggle/i },
];
const SKILL_DOMAIN_FALLBACK = "skills.domainFallback";
const SKILL_DOMAIN_ORDER = [...SKILL_DOMAIN_RULES.map((rule) => rule.label), SKILL_DOMAIN_FALLBACK];

function skillDomain(skill: AgentSkill): string {
  const haystack = `${skill.name} ${skill.title || ""} ${skill.category || ""} ${skill.description || ""}`;
  for (const rule of SKILL_DOMAIN_RULES) {
    if (rule.pattern.test(haystack)) {
      return rule.label;
    }
  }
  return SKILL_DOMAIN_FALLBACK;
}

function isStoredChat(value: unknown): value is ChatThread {
  if (!value || typeof value !== "object") {
    return false;
  }
  const chat = value as Partial<ChatThread>;
  return typeof chat.id === "string" && chat.id.length > 0 && Array.isArray(chat.items);
}

function formatOptimizationMetric(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value >= 1000 ? Math.round(value).toLocaleString() : String(value);
  }
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return "unknown";
}

function optimizationRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function optimizationArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function proofStageLabel(stage: string): string {
  if (stage === "after_apply") {
    return "After apply";
  }
  if (stage === "after_rollback") {
    return "After rollback";
  }
  return "Before";
}

function proofImageUrl(endpoint: string, value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  if (/^https?:\/\//i.test(raw)) {
    return raw;
  }
  if (raw.startsWith("/")) {
    return `${endpoint.replace(/\/$/, "")}${raw}`;
  }
  return raw;
}

function formatProofValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? i18n.t("proof.yes") : i18n.t("proof.no");
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}

function protectionRecord(value: unknown): Record<string, unknown> {
  return optimizationRecord(value);
}

function protectionArray(value: unknown): unknown[] {
  return optimizationArray(value);
}

function protectionPlanPayload(result: AvatarEncryptionPlanResult | null): Record<string, unknown> {
  return protectionRecord(result?.plan);
}

function protectionProfileCards(result: AvatarEncryptionPlanResult | null): AvatarEncryptionProfileCard[] {
  const plan = protectionPlanPayload(result);
  const cards = protectionArray(plan.profileCards).filter((item): item is AvatarEncryptionProfileCard => Boolean(item) && typeof item === "object");
  if (!cards.length) {
    return PROTECTION_PROFILE_FALLBACKS;
  }
  const seen = new Set<string>();
  return [...cards, ...PROTECTION_PROFILE_FALLBACKS].filter((item) => {
    const id = String(item.id || "");
    if (!id || seen.has(id)) {
      return false;
    }
    seen.add(id);
    return true;
  });
}

function protectionBenchmarkRows(result: AvatarEncryptionPlanResult | null): AvatarEncryptionBenchmarkRow[] {
  const plan = protectionPlanPayload(result);
  return protectionArray(plan.benchmarkTable).filter((item): item is AvatarEncryptionBenchmarkRow => Boolean(item) && typeof item === "object");
}

function groupProtectionBenchmarks(rows: AvatarEncryptionBenchmarkRow[]): Array<{ scale: string; byProfile: Record<string, AvatarEncryptionBenchmarkRow> }> {
  const groups: Array<{ scale: string; triangles: number; byProfile: Record<string, AvatarEncryptionBenchmarkRow> }> = [];
  for (const row of rows) {
    const triangles = Number(row.triangles || 0);
    const scale = row.avatarScale || (triangles ? `${Math.round(triangles / 10000)}万面` : i18n.t("optimization.unknown"));
    let group = groups.find((item) => item.scale === scale);
    if (!group) {
      group = { scale, triangles, byProfile: {} };
      groups.push(group);
    }
    if (row.profile) {
      group.byProfile[String(row.profile)] = row;
    }
  }
  return groups.sort((a, b) => a.triangles - b.triangles).map(({ scale, byProfile }) => ({ scale, byProfile }));
}

function protectionFamilyAvailable(candidates: unknown[], family: "liltoon" | "poiyomi"): boolean {
  return candidates.some((candidate) => {
    const item = protectionRecord(candidate);
    const familyId = String(item.shaderFamilyId || item.shaderFamily || "").toLowerCase();
    if (family === "liltoon") {
      return familyId.includes("liltoon") || familyId.includes("liltoon");
    }
    return familyId.includes("poiyomi") || familyId.includes("poi");
  });
}

function protectionCostLabel(value?: string): string {
  const cost = String(value || "").toLowerCase();
  if (cost === "lowest") {
    return "Lowest";
  }
  if (cost === "balanced") {
    return "Balanced";
  }
  if (cost === "highest") {
    return "Highest";
  }
  return value || "-";
}

function protectionGateLabel(value: string): string {
  if (value === "platform.windows_only") {
    return "Windows avatar only";
  }
  if (value === "profile.paranoid_blendshape_proof_required") {
    return "Highest mode needs proof";
  }
  if (value === "profile.custom_layers_not_supported") {
    return "Choose one of the three modes";
  }
  if (value === "layer.experimental_or_research_only") {
    return "Layer still in testing";
  }
  if (value === "targets.requested_targets_not_found") {
    return "Selected target not found";
  }
  if (value === "shader_family.no_liltoon_or_poiyomi_candidate") {
    return "No supported shader target";
  }
  if (value === "shader_family.requested_restore_adapter_missing") {
    return "Shader adapter missing";
  }
  if (value === "plan.untrusted_external_plan") {
    return "Plan needs fresh scan";
  }
  return "Needs review";
}

function protectionImpactSummary(rows: AvatarEncryptionBenchmarkRow[], profile: string): string {
  const candidates = rows.filter((row) => row.profile === profile);
  if (!candidates.length) {
    return "not estimated";
  }
  const maxImpact = Math.max(...candidates.map((row) => Number(row.estimatedImpactPercent || 0)));
  return `${formatProofValue(maxImpact)}% max`;
}

function avatarOptionLabel(avatar: AvatarListItem): string {
  const name = avatar.avatarName || shortPath(avatar.avatarPath || "") || "Avatar";
  const parts = [name];
  if (avatar.sceneName) {
    parts.push(avatar.sceneName);
  }
  const stats: string[] = [];
  if (typeof avatar.rendererCount === "number") {
    stats.push(`${avatar.rendererCount} renderers`);
  }
  if (typeof avatar.blendshapeCount === "number") {
    stats.push(`${avatar.blendshapeCount} blendshapes`);
  }
  if (stats.length) {
    parts.push(stats.join(", "));
  }
  return parts.join(" - ");
}

function optimizationRequestSignature(card: OptimizationActionCardItem): string {
  return `${card.id || ""} ${card.requestTool || ""} ${card.title || ""}`.toLowerCase();
}

function isTttOptimizationRequest(card: OptimizationActionCardItem): boolean {
  const signature = optimizationRequestSignature(card);
  return signature.includes("ttt") || signature.includes("textrans") || signature.includes("atlas");
}

function isMeshiaOptimizationRequest(card: OptimizationActionCardItem): boolean {
  return optimizationRequestSignature(card).includes("meshia");
}

function splitOptimizationOptionLines(value?: string): string[] {
  return String(value || "")
    .split(/[\n,;]+/g)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildOptimizationRequestOptions(card: OptimizationActionCardItem, options: OptimizationActionOptions): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (isTttOptimizationRequest(card)) {
    payload.atlasTargetMaterials = splitOptimizationOptionLines(options.atlasTargetMaterials);
  }
  if (isMeshiaOptimizationRequest(card)) {
    payload.rendererPath = String(options.rendererPath || "").trim();
    const ratio = Number(options.relativeVertexCount || "0.9");
    if (Number.isFinite(ratio)) {
      payload.relativeVertexCount = ratio;
    }
  }
  return payload;
}

function optimizationActionMissingRequiredOptions(card: OptimizationActionCardItem, options: OptimizationActionOptions): boolean {
  if (isTttOptimizationRequest(card)) {
    return splitOptimizationOptionLines(options.atlasTargetMaterials).length === 0;
  }
  if (isMeshiaOptimizationRequest(card)) {
    return !String(options.rendererPath || "").trim();
  }
  return false;
}

function dependencyTone(status?: string) {
  const value = String(status || "").toLowerCase();
  if (value === "installed") {
    return "ok" as const;
  }
  if (value === "missing") {
    return "warn" as const;
  }
  return "muted" as const;
}

function optimizationRiskTone(risk?: string) {
  const value = String(risk || "").toLowerCase();
  if (value === "high" || value.includes("danger")) {
    return "danger" as const;
  }
  if (value === "medium") {
    return "warn" as const;
  }
  if (value === "low") {
    return "ok" as const;
  }
  return "muted" as const;
}

function offenderTone(severity?: string) {
  const value = String(severity || "").toLowerCase();
  if (value.includes("error") || value.includes("danger")) {
    return "danger" as const;
  }
  if (value.includes("warn")) {
    return "warn" as const;
  }
  if (value.includes("suggest")) {
    return "ok" as const;
  }
  return "muted" as const;
}

function StatusChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <Badge tone={ok ? "ok" : "warn"} className="max-w-[180px]">
      <span className="truncate">{label}</span>
    </Badge>
  );
}

function getHealthDetailNumber(detail: unknown, key: string): number {
  if (!detail || typeof detail !== "object") {
    return 0;
  }
  const value = (detail as Record<string, unknown>)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function defaultModelForProvider(provider: string): string {
  switch (provider) {
    case "anthropic":
      return "claude-opus-4-6";
    case "deepseek":
      return "deepseek-chat";
    case "openrouter":
      return "openai/gpt-4.1-mini";
    case "openai":
      return "gpt-4.1-mini";
    case "ollama":
      return "llama3.2";
    case "vertexai":
      return "gemini-2.5-flash";
    case "custom":
      return "gpt-4.1-mini";
    case "gemini":
    default:
      return "gemini-2.5-flash";
  }
}

function defaultBaseUrlForProvider(provider: string): string {
  switch (provider) {
    case "openai":
      return "https://api.openai.com/v1";
    case "deepseek":
      return "https://api.deepseek.com";
    case "openrouter":
      return "https://openrouter.ai/api/v1";
    case "ollama":
      return "http://127.0.0.1:11434/v1";
    default:
      return "";
  }
}

function providerNeedsApiKey(provider: string): boolean {
  return provider !== "ollama" && provider !== "vertexai";
}

function providerCapabilities(provider: string): Array<{ label: string; tone: "ok" | "warn" | "danger" | "muted" | "default" }> {
  const paid = provider !== "ollama";
  const local = provider === "ollama";
  const capabilities: Array<{ label: string; tone: "ok" | "warn" | "danger" | "muted" | "default" }> = [
    { label: i18n.t("providerCapability.text"), tone: "muted" },
    { label: i18n.t("providerCapability.structuredJson"), tone: "muted" },
  ];
  if (["gemini", "openai", "openrouter", "vertexai"].includes(provider)) {
    capabilities.push({ label: i18n.t("providerCapability.vision"), tone: "muted" });
  }
  if (local) {
    capabilities.push({ label: i18n.t("providerCapability.local"), tone: "ok" }, { label: i18n.t("providerCapability.offline"), tone: "ok" }, { label: i18n.t("providerCapability.freeLocal"), tone: "ok" });
  }
  if (paid) {
    capabilities.push({ label: i18n.t("providerCapability.paidApi"), tone: "warn" });
  }
  if (["gemini", "anthropic", "openai", "openrouter", "vertexai"].includes(provider)) {
    capabilities.push({ label: i18n.t("providerCapability.longContext"), tone: "muted" });
  }
  return capabilities;
}

function thinkingStatusForModel(provider: string, model: string): string {
  const key = `${provider || ""} ${model || ""}`.toLowerCase();
  if (/(deepseek-reasoner|deepseek-r1|\br1\b|\bo[134](?:-|$)|reason|thinking)/.test(key)) {
    return "Reasoning";
  }
  if (/(claude|anthropic)/.test(key)) {
    return "Thinking";
  }
  if (/(gemini|google|vertex)/.test(key)) {
    return "Thinking";
  }
  if (/(gpt|openai)/.test(key)) {
    return "Thinking";
  }
  if (/(deepseek|grok|x-ai|openrouter)/.test(key)) {
    return "Thinking";
  }
  if (/(ollama|llama|qwen|mistral|mixtral|phi|local|custom)/.test(key)) {
    return "Working on it";
  }
  return "Working on it";
}

function emptySkillDraft(): Partial<AgentSkill> {
  return {
    name: "",
    title: "",
    description: "",
    category: "user",
    source: "user",
    skillType: "package",
    enabled: true,
    available: true,
    permissionMode: "instruction_only",
    riskLevel: "low",
    whenToUse: "",
    inputs: [],
    outputs: [],
    sideEffects: "none",
    backupRestore: "not required",
    tools: [],
    allowedTools: [],
    disallowedTools: [],
    entrypointTool: "",
    userInvocable: true,
    disableModelInvocation: false,
    argumentHint: "",
    requiresEnv: [],
    requiresBinaries: [],
    supportedOs: ["windows"],
    supportFiles: [],
    testCommand: "",
    instructions: "",
    tags: ["user"],
  };
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function displayPlanner(planner: string): string {
  if (planner === "deterministic-local") {
    return i18n.t("planner.local");
  }
  if (planner === "llm") {
    return i18n.t("planner.ai");
  }
  return planner || i18n.t("planner.fallback");
}

function displayStep(step: string): string {
  const labels: Record<string, string> = {
    classify_shell: i18n.t("step.classifyShell"),
    execute_shell: i18n.t("step.executeShell"),
    call_skill: i18n.t("step.callSkill"),
    request_approval: i18n.t("shell.awaitConfirmation"),
    await_user_instruction: i18n.t("step.awaitUserInstruction"),
    done: i18n.t("step.done"),
  };
  return labels[step] || step;
}

function riskTone(risk: string): "ok" | "warn" | "danger" | "muted" {
  if (risk === "low") return "ok";
  if (risk === "high") return "warn";
  if (risk === "reject") return "danger";
  return "muted";
}

function skillTone(skill: AgentSkillResult): "ok" | "warn" | "danger" | "muted" {
  if (skill.status === "executed" && skill.ok) return "ok";
  if (skill.status === "loaded" && skill.ok) return "ok";
  if (skill.status === "blocked") return "warn";
  if (skill.status === "failed" || !skill.ok) return "danger";
  return "muted";
}

function displaySkillStatus(status: string): string {
  const labels: Record<string, string> = {
    executed: i18n.t("agent.executed"),
    loaded: i18n.t("skillStatus.loaded"),
    failed: i18n.t("skillStatus.failed"),
    blocked: i18n.t("skillStatus.blocked"),
  };
  return labels[status] || status || "-";
}

function formatPayload(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatProjectIndexPaths(entries: Array<{ path: string; category?: string; size?: number }>): string {
  const lines = entries.slice(0, 80).map((entry) => {
    const category = entry.category ? ` [${entry.category}]` : "";
    const size = typeof entry.size === "number" && entry.size > 0 ? ` ${formatCount(entry.size)}b` : "";
    return `${entry.path}${category}${size}`;
  });
  if (entries.length > lines.length) {
    lines.push(`... ${entries.length - lines.length} more`);
  }
  return lines.join("\n");
}

function isAgentShellResult(value: unknown): value is AgentShellResult {
  if (!value || typeof value !== "object") {
    return false;
  }
  const payload = value as Partial<AgentShellResult>;
  return typeof payload.command === "string" && typeof payload.exitCode === "number";
}

function shortPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).slice(-1)[0] || path;
}

function shortRef(ref?: string) {
  return ref ? ref.slice(0, 12) : "-";
}

function formatCheckpointTime(value?: string) {
  if (!value) {
    return "-";
  }
  const time = new Date(value);
  if (Number.isNaN(time.getTime())) {
    return value;
  }
  return time.toLocaleString();
}

function quoteLines(text: string): string {
  return text
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
}

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  if (seconds < 60) {
    return i18n.t("format.seconds", { n: seconds });
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) {
    return rest > 0 ? i18n.t("format.minutesSeconds", { m: minutes, s: rest }) : i18n.t("format.minutes", { n: minutes });
  }
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes > 0 ? i18n.t("format.hoursMinutes", { h: hours, m: restMinutes }) : i18n.t("format.hours", { n: hours });
}

const HISTORY_ENTRY_MAX_CHARS = 2000;
const COMPACT_ENTRY_MAX_CHARS = 400;
const COMPACT_HEAD_ENTRIES = 2;
const COMPACT_TAIL_ENTRIES = 8;

function clipText(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function buildChatHistory(items: ConversationItem[]): ChatHistoryEntry[] {
  const history: ChatHistoryEntry[] = [];
  for (const item of items) {
    if (item.type === "user") {
      history.push({ role: "user", text: clipText(item.text, HISTORY_ENTRY_MAX_CHARS) });
    } else if (item.type === "agent") {
      const parts = [item.response.plan?.summary || ""];
      const stdout = item.response.result?.stdout || item.response.shell?.result?.stdout || "";
      if (stdout.trim()) {
        parts.push(stdout.trim());
      }
      const text = parts.filter(Boolean).join("\n").trim();
      if (text) {
        history.push({ role: "agent", text: clipText(text, HISTORY_ENTRY_MAX_CHARS) });
      }
    } else if (item.type === "result") {
      const text = (item.result?.stdout || item.error || "").trim();
      if (text) {
        history.push({ role: "agent", text: clipText(text, HISTORY_ENTRY_MAX_CHARS) });
      }
    } else if (item.type === "compact") {
      history.push({ role: "agent", text: clipText(item.text, HISTORY_ENTRY_MAX_CHARS) });
    } else if (item.type === "subagent") {
      const task = item.task;
      const text = [
        `${i18n.t("subagent.taskLabel")} ${task.displayName || task.id} (${subAgentRoleLabel(task.role)}) ${task.status}`,
        task.summary || task.error || task.task || "",
      ]
        .filter(Boolean)
        .join("\n")
        .trim();
      if (text) {
        history.push({ role: "agent", text: clipText(text, HISTORY_ENTRY_MAX_CHARS) });
      }
    }
  }
  return history;
}

function buildCompactSummary(items: ConversationItem[]): string {
  const entries = buildChatHistory(items).map(
    (entry) => `${entry.role === "user" ? i18n.t("compact.user") : i18n.t("compact.assistant")}: ${clipText(entry.text.replace(/\s+/g, " ").trim(), COMPACT_ENTRY_MAX_CHARS)}`,
  );
  let lines = entries;
  if (entries.length > COMPACT_HEAD_ENTRIES + COMPACT_TAIL_ENTRIES) {
    const omitted = entries.length - COMPACT_HEAD_ENTRIES - COMPACT_TAIL_ENTRIES;
    lines = [
      ...entries.slice(0, COMPACT_HEAD_ENTRIES),
      i18n.t("compact.omitted", { count: omitted }),
      ...entries.slice(entries.length - COMPACT_TAIL_ENTRIES),
    ];
  }
  return `${i18n.t("compact.summary", { count: entries.length })}\n${lines.join("\n")}`;
}
