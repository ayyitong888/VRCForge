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
  Wrench,
  X,
} from "lucide-react";
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
  AgentCheckpoint,
  AgentCheckpointPreview,
  AgentRuntimeResponse,
  AgentReasoningTrace,
  AgentSkill,
  AgentSkillRegistry,
  AgentSkillResult,
  AgentShellResult,
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
  fetchCheckpoints,
  fetchBootstrap,
  fetchDiagnostics,
  fetchDoctor,
  fetchExternalAgentConnectors,
  fetchOptimizationPlan,
  fetchSkillPackages,
  fetchSkills,
  AgentSkillCheck,
  ExecutionMode,
  PermissionState,
  fetchAgentNotes,
  fetchChats,
  fetchProjectPrefs,
  fetchProviderModels,
  fetchSubAgent,
  fetchSubAgents,
  installExternalAgentConnector,
  importSkillPackage,
  planOutfitImport,
  preflightSkillPackage,
  ProjectPrefs,
  previewRestoreCheckpoint,
  rejectAgentApproval,
  requestOptimizationApply,
  requestOutfitImport,
  requestPackageInstall,
  requestRestoreCheckpoint,
  repairUnityMcpBridge,
  retrySubAgent,
  saveChats,
  saveProjectPrefs,
  saveAgentNotes,
  scanProjectIndex,
  sendAgentMessage,
  setAppSessionToken,
  testProviderCapability,
  updateApiConfig,
  updateDiagnostics,
  updateExternalAgentGateway,
  updatePermission,
  updateSkill,
  uninstallExternalAgentConnector,
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
    const ready = action.handshake?.ready ? "ready" : action.handshake?.connected ? "connected" : "checked";
    return `${label} installed; ${ready}${toolCount !== undefined ? `, ${toolCount} tools` : ""}`;
  }
  return `${label} ${verb}`;
}

type ConversationItem =
  | { id: string; type: "user"; text: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse; elapsedSeconds?: number }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string }
  | { id: string; type: "error"; text: string }
  | { id: string; type: "compact"; text: string }
  | { id: string; type: "subagent"; task: SubAgentTask };

type ActiveView = "chat" | "doctor" | "optimization" | "skills" | "checkpoints" | "settings";

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
  { value: "approval", label: "受限模式", description: "沙箱模式：高风险命令与写操作逐项审批，最安全。" },
  { value: "auto", label: "自动审批", description: "审批自动通过并留痕，Roslyn 高级能力保持关闭。" },
  { value: "roslyn_full_auto", label: "完全权限", description: "自动审批 + Roslyn 全自动，风险最高，首次开启需确认。" },
];

function executionModeLabel(mode?: string): string {
  return EXECUTION_MODES.find((item) => item.value === mode)?.label || "受限模式";
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
  const [loadingOptimization, setLoadingOptimization] = useState(false);
  const [optimizationMessage, setOptimizationMessage] = useState("");
  const [requestingOptimizationAction, setRequestingOptimizationAction] = useState("");
  const [requestingOptimizationDependency, setRequestingOptimizationDependency] = useState("");
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
  const [checkpointPreview, setCheckpointPreview] = useState<AgentCheckpointPreview | null>(null);
  const [loadingCheckpoints, setLoadingCheckpoints] = useState(false);
  const [restoringCheckpointId, setRestoringCheckpointId] = useState("");
  const [checkpointMessage, setCheckpointMessage] = useState("");
  const [agentNotes, setAgentNotes] = useState("");
  const [agentNotesPath, setAgentNotesPath] = useState("");
  const [agentNotesLoaded, setAgentNotesLoaded] = useState(false);
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const [connectorStatus, setConnectorStatus] = useState<ExternalAgentConnectorStatus | null>(null);
  const [loadingConnectors, setLoadingConnectors] = useState(false);
  const [connectorMessage, setConnectorMessage] = useState("");
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
    const list: Array<{ name: string; title: string }> = [{ name: "compact", title: "压缩当前会话历史，释放上下文" }];
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
    ? "核心未连接"
    : vrcForgeSkillsReady
      ? `头像能力 ${vrcForgeToolsCount}`
      : "基础模式";
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
        .sort((a, b) => Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(b)))) - Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(a)))))
        .slice(0, 24),
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
  const projectPromptTitle = activeProjectPath && activeProjectName ? `想在 ${activeProjectName} 里改什么？` : "随心聊点什么？";
  const emptyProjectState = useMemo(() => {
    if (projectItems.length > 0) {
      return null;
    }
    if (loading && !error) {
      return { name: "扫描中", meta: "wait" };
    }
    if (hasStartupIssue || !runtimeConnected) {
      return { name: "核心未连接", meta: "retry" };
    }
    if (error) {
      return { name: "刷新失败", meta: "retry" };
    }
    return { name: "未发现 Unity 项目", meta: "empty" };
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
      .then(setProjectPrefs)
      .catch(() => {
        projectPrefsLoadedRef.current = false;
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
    if (!runtimeConnected || chatsLoadedRef.current) {
      return;
    }
    chatsLoadedRef.current = true;
    void (async () => {
      try {
        const payload = await fetchChats<unknown>(endpoint);
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
  }, [runtimeConnected, endpoint]);

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
      await updatePermission(endpoint, mode, acknowledge);
      await refresh();
      setShowRoslynWarning(false);
      setPendingMode(null);
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
      setError("当前会话还没有可压缩的内容。");
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
        summary = `（模型压缩摘要，共 ${payload.entryCount ?? items.length} 条消息）\n${summary}`;
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
          throw new Error("核心未连接，消息未发送。");
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
      setProjectModalError("请输入真正的 Unity 工程根目录：这一层必须同时包含 Assets/、Packages/ 和 ProjectSettings/ProjectVersion.txt。");
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

  async function openProjectFolder(path: string) {
    const targetPath = path.trim();
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
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimization(false);
    }
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
      setSkillPackageMessage(payload.changed === false ? "Package already installed" : "Package imported");
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

  async function exportVskPackage(skillName: string, outputPath: string, release: boolean) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await exportSkillPackage(endpoint, { skillName, outputPath, release });
      setSkillPackageMessage(release ? "Release package exported" : "Dev package exported");
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

  async function updateGatewaySettings(request: { enabled?: boolean; allowWriteRequests?: boolean; revokeToken?: boolean }) {
    setLoadingConnectors(true);
    setConnectorMessage("");
    setError("");
    try {
      const payload = await updateExternalAgentGateway(endpoint, request);
      setConnectorStatus(payload);
      setConnectorMessage(request.revokeToken ? "Token revoked" : "Gateway updated");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingConnectors(false);
    }
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
      setNotesMessage("已保存");
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
      const payload = await fetchCheckpoints(targetEndpoint, activeProjectPath || undefined);
      setCheckpoints(payload.checkpoints || []);
      if (checkpointPreview?.checkpoint?.id && !payload.checkpoints?.some((item) => item.id === checkpointPreview.checkpoint?.id)) {
        setCheckpointPreview(null);
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
          setModelsError("核心未连接，无法获取模型列表");
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
        setModelsError("该供应商未返回模型列表，可手动填写模型名");
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
      <div className="grid h-screen grid-cols-[320px_minmax(0,1fr)]">
        <aside className="sidebar-scrollbar flex h-screen min-w-0 flex-col overflow-y-auto border-r border-border bg-sidebar px-4 py-4">
          <div className="flex h-10 items-center gap-3 px-2">
            <Bot className="h-5 w-5 shrink-0 text-primary" />
            <div className="truncate text-base font-semibold">VRCForge</div>
          </div>

          <nav className="mt-5 space-y-1">
            <button
              onClick={newTemporaryChat}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "chat" && !activeProjectPath && !activeChat
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
              <span className="truncate">临时对话</span>
            </button>
            <button
              onClick={() => {
                setProjectModalError("");
                setShowProjectModal(true);
              }}
              className="flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <FolderPlus className="h-4 w-4 shrink-0" />
              <span className="truncate">新项目</span>
            </button>
            <button
              onClick={() => void openDoctor()}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "doctor"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Shield className="h-4 w-4 shrink-0" />
              <span className="truncate">Doctor</span>
            </button>
            <button
              onClick={() => void openOptimization()}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "optimization"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Gauge className="h-4 w-4 shrink-0" />
              <span className="truncate">Optimization</span>
            </button>
            <button
              onClick={() => void openSkills()}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "skills"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Wrench className="h-4 w-4 shrink-0" />
              <span className="truncate">能力库</span>
            </button>
            <button
              onClick={() => void openCheckpoints()}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "checkpoints"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <History className="h-4 w-4 shrink-0" />
              <span className="truncate">Checkpoints</span>
            </button>
          </nav>

          <SidebarSection title="项目">
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
                        title={chat.title || "新对话"}
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
              <SidebarProject name={emptyProjectState?.name || "未发现 Unity 项目"} meta={emptyProjectState?.meta} active />
            )}
          </SidebarSection>

          <SidebarSection
            title="对话"
            collapsed={Boolean(collapsedProjects[TEMP_CHATS_COLLAPSE_KEY])}
            onToggleCollapse={() => toggleProjectCollapse(TEMP_CHATS_COLLAPSE_KEY)}
          >
            {temporaryChats.length > 0 ? (
              temporaryChats.map((chat) => (
                <SidebarChat
                  key={chat.id}
                  title={chat.title || "新对话"}
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
              <div className="px-3 py-1 text-xs text-muted-foreground/70">暂无临时对话</div>
            )}
          </SidebarSection>

          <div className="mt-auto">
            <button
              onClick={() => void openSettings()}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "settings"
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Settings className="h-4 w-4 shrink-0" />
              <span className="truncate">设置</span>
            </button>
          </div>
        </aside>

        <section className="flex h-screen min-w-0 flex-col overflow-hidden bg-workspace">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-6">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="truncate text-muted-foreground">{activeProjectPath ? activeProjectName : "临时对话"}</span>
              <span className="text-muted-foreground">/</span>
              <span className="truncate font-medium">
                {activeView === "doctor"
                  ? "Doctor"
                  : activeView === "optimization"
                    ? "Optimization"
                  : activeView === "skills"
                    ? "能力库"
                    : activeView === "settings"
                      ? "设置"
                      : activeChat
                        ? activeChat.title || "当前会话"
                        : "新任务"}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {permission?.roslynFullAuto ? (
                <Badge tone="danger">
                  <AlertTriangle className="mr-1 h-3.5 w-3.5 shrink-0" />
                  完全权限
                </Badge>
              ) : permission?.executionMode === "auto" ? (
                <Badge tone="warn">自动审批</Badge>
              ) : null}
              <StatusChip ok={runtimeConnected} label={runtimeConnected ? "核心在线" : "核心离线"} />
              <Badge tone={pendingApprovals > 0 ? "warn" : "muted"}>{formatCount(pendingApprovals)} 待确认</Badge>
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
                    {hasStartupIssue ? "Startup issue detected" : "Environment needs attention"}
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
                  {loading ? "Retrying" : "Retry"}
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
                  {loading ? "重连中" : "重连"}
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
                  .then(() => setDoctorMessage("已复制诊断摘要"))
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
              onRefreshPackages={() => void loadSkillPackages()}
              onPreflightPackage={preflightVskPackage}
              onImportPackage={importVskPackage}
              onExportPackage={exportVskPackage}
            />
          ) : activeView === "checkpoints" ? (
            <CheckpointWorkspace
              checkpoints={checkpoints}
              selectedProjectPath={activeProjectPath}
              preview={checkpointPreview}
              loading={loadingCheckpoints}
              restoringId={restoringCheckpointId}
              message={checkpointMessage}
              onRefresh={() => void loadCheckpoints()}
              onPreview={previewCheckpoint}
              onRestore={restoreCheckpoint}
            />
          ) : activeView === "optimization" ? (
            <OptimizationWorkspace
              report={optimizationReport}
              selectedProjectPath={activeProjectPath}
              avatarPath={optimizationAvatarPath}
              targetProfile={optimizationTargetProfile}
              loading={loadingOptimization}
              message={optimizationMessage}
              requestingActionId={requestingOptimizationAction}
              requestingDependencyId={requestingOptimizationDependency}
              onAvatarPathChange={setOptimizationAvatarPath}
              onTargetProfileChange={setOptimizationTargetProfile}
              onRefresh={() => void loadOptimizationPlan()}
              onRequestAction={(card) => void requestOptimizationAction(card)}
              onRequestDependency={(dependency) => void requestOptimizationDependencyInstall(dependency)}
            />
          ) : activeView === "settings" ? (
            <div className="app-scrollbar min-h-0 flex-1 overflow-y-auto px-6 py-10">
              <div className="mx-auto w-full max-w-3xl">
                <h1 className="text-2xl font-semibold tracking-tight">设置</h1>
                <p className="mt-1 text-sm text-muted-foreground">配置权限模式、模型供应商与全局自定义指令。</p>

                <section className="mt-10">
                  <div className="flex min-w-0 items-center gap-2">
                    <h2 className="truncate text-base font-semibold">权限模式</h2>
                    <Badge tone={permission?.roslynFullAuto ? "danger" : permission?.autoApprove ? "warn" : "muted"} className="shrink-0">
                      当前：{executionModeLabel(permission?.executionMode)}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">控制智能体执行命令与写操作时的审批策略，随时可切换。</p>
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
                              高风险
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
                    <div className="mt-3 text-xs text-muted-foreground">完全权限风险确认：已确认（仅首次开启时弹出）</div>
                  ) : null}
                </section>

                <section className="mt-12">
                  <h2 className="text-base font-semibold">新手引导</h2>
                  <p className="mt-1 text-sm text-muted-foreground">重新打开首次启动的三步引导（连接核心 / 绑定模型 / 选择项目）。</p>
                  <div className="mt-4">
                    <Button type="button" variant="outline" onClick={restartOnboarding}>
                      <RefreshCw className="mr-1 h-4 w-4" />
                      重新引导
                    </Button>
                  </div>
                </section>

                <section className="mt-12">
                  <h2 className="text-base font-semibold">模型供应商</h2>
                  <p className="mt-1 text-sm text-muted-foreground">连接供应商后点击「刷新模型列表」，即可从该账号可用的模型中选择。</p>
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
                    <h2 className="truncate text-base font-semibold">Diagnostics</h2>
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
                          {diagnosticsStatus?.debugLogging ? "Recording local API, MCP, agent, checkpoint, and runtime interactions" : "Off"}
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
                    <h2 className="truncate text-base font-semibold">自定义指令</h2>
                    {notesMessage ? (
                      <Badge tone="ok" className="shrink-0">
                        {notesMessage}
                      </Badge>
                    ) : null}
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    写给智能体的全局规则与偏好（AGENTS.md），会注入每一次规划、审批与执行。
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
                      placeholder={agentNotesLoaded ? "例如：回复使用中文；改动 Unity 工程前先列出计划；禁止删除任何资源文件……" : "核心未连接，无法加载 AGENTS.md"}
                      className="min-h-56 w-full resize-y rounded-xl border border-border bg-background px-4 py-3 text-sm leading-relaxed outline-none focus:border-primary disabled:bg-muted"
                    />
                    <div className="mt-3 flex justify-end">
                      <Button type="submit" disabled={savingNotes || !agentNotesLoaded}>
                        {savingNotes ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                        保存
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
                          已排队 · 当前任务结束后自动发送
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
        </section>
      </div>

      {showOnboarding && !onboardingMinimized
        ? (() => {
            const steps = [
              {
                title: "连接核心与 Unity",
                done: runtimeConnected,
                doneDesc: "核心已在线，连接正常。",
                todoDesc: "正在等待核心启动，连上后这一步会自动完成；若长时间离线，点下方「重试连接」。",
                action: (
                  <Button variant="outline" disabled={loading} onClick={() => void startRuntime()}>
                    <RefreshCw className="mr-1 h-4 w-4" />
                    {loading ? "连接中…" : "重试连接"}
                  </Button>
                ),
              },
              {
                title: "绑定模型供应商",
                done: Boolean(apiConfig?.apiKeyPresent),
                doneDesc: "模型密钥已配置，可以用自然语言对话了。",
                todoDesc: "去设置里选择供应商并填入密钥，保存成功后这一步会自动完成，引导会自己回来。",
                action: (
                  <Button
                    variant="outline"
                    onClick={() => {
                      setOnboardingMinimized(true);
                      void openSettings();
                    }}
                  >
                    <Settings className="mr-1 h-4 w-4" />
                    去设置
                  </Button>
                ),
              },
              {
                title: "选择 Unity 项目",
                done: projectItems.length > 0,
                doneDesc: "已发现 Unity 项目，左侧边栏可直接进入。",
                todoDesc: "扫描到项目后这一步自动完成；也可以用「新项目」手动填路径，或先跳过用临时对话。",
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
                    新项目
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
                    <h2 className="truncate text-lg font-semibold">欢迎使用 VRCForge</h2>
                    <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                      第 {onboardingStep + 1} / {steps.length} 步
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
                        {step.done ? "已完成" : "检测中"}
                      </Badge>
                    </div>
                    <p className="mt-2 text-sm text-muted-foreground">{step.done ? step.doneDesc : step.todoDesc}</p>
                    {!step.done ? <div className="mt-4">{step.action}</div> : null}
                  </div>
                  <div className="mt-6 flex items-center gap-3">
                    <Button variant="ghost" className="text-muted-foreground" onClick={finishOnboarding}>
                      跳过引导
                    </Button>
                    <div className="ml-auto flex gap-3">
                      {onboardingStep > 0 ? (
                        <Button variant="outline" onClick={() => setOnboardingStep((value) => Math.max(0, value - 1))}>
                          上一步
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
                        {isLast ? "开始使用" : "下一步"}
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
          <span>继续新手引导（第 {onboardingStep + 1} / 3 步）</span>
        </button>
      ) : null}

      {showProjectModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
          <section className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-border bg-card p-6 shadow-panel">
            <div className="flex min-w-0 items-center gap-2">
              <FolderPlus className="h-5 w-5 shrink-0 text-primary" />
              <h2 className="truncate text-lg font-semibold">选择 Unity 项目</h2>
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
                <div className="mb-2 text-xs font-medium text-muted-foreground">已扫描到的项目</div>
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
                              title="从列表移除（不删除文件）"
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
                    暂未扫描到项目，可以在下方手动填入 Unity 工程文件夹路径。
                  </p>
                )}
              </div>
              {hiddenProjects.length > 0 ? (
                <div>
                  <div className="mb-2 text-xs font-medium text-muted-foreground">已隐藏的项目</div>
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
                          恢复
                        </Button>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              <div>
                <div className="mb-2 text-xs font-medium text-muted-foreground">手动添加项目文件夹</div>
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
                    placeholder="例如 D:\Unity\MyAvatarProject"
                    className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
                  />
                  <Button type="button" disabled={savingProjectPrefs || !newProjectPath.trim()} onClick={() => void addProjectPath()}>
                    {savingProjectPrefs ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                    添加
                  </Button>
                </div>
                {projectModalError ? <p className="mt-2 text-xs text-destructive">{projectModalError}</p> : null}
                <p className="mt-2 text-xs text-muted-foreground">填入 Unity 工程根目录的完整路径，添加后会保存到本地，下次启动仍然可见。</p>
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
                    {pinned ? "取消置顶项目" : "置顶项目"}
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
                    在资源管理器中打开
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
                    在此项目新对话
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
                    重命名项目
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
                    {collapsed ? "展开对话" : "折叠对话"}
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
                      {projectChatCount > 0 ? "归档项目会话" : "恢复归档会话"}
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
                    隐藏项目
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
                      移除项目
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
            复制
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => addSelectionToComposer(selectionMenu.text)}
          >
            <MessageSquare className="h-3.5 w-3.5 shrink-0" />
            添加到对话
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1.5 text-xs transition-colors hover:bg-muted"
            onClick={() => askInNewSession(selectionMenu.text)}
          >
            <Bot className="h-3.5 w-3.5 shrink-0" />
            新会话提问
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
                    {menuChat.pinned ? "取消置顶" : "置顶对话"}
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
                    重命名对话
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
                    永久删除
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
              <h2 className="truncate text-base font-semibold">永久删除对话</h2>
            </div>
            <p className="mt-3 text-sm text-muted-foreground">
              「{chats.find((chat) => chat.id === deleteTargetId)?.title || "新对话"}」将被永久删除，本地记录一并清除，无法恢复。
            </p>
            <div className="mt-5 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setDeleteTargetId("")}>
                取消
              </Button>
              <Button variant="danger" onClick={() => deleteChatPermanently(deleteTargetId)}>
                永久删除
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
              <h2 className="truncate text-lg font-semibold">开启完全权限</h2>
            </div>
            <p className="mt-4 text-sm text-muted-foreground">
              完全权限会自动通过所有审批，并启用 Roslyn 全自动写入能力。代理可以在不经确认的情况下修改 Unity 工程文件，存在不可逆风险。此确认仅在首次开启时出现。
            </p>
            <div className="mt-5 grid gap-3 text-sm">
              <DataLine label="风险确认" value={permission?.roslynRiskAcknowledged ? "已确认" : "未确认"} />
              <DataLine label="目标模式" value="完全权限（Roslyn 全自动）" />
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setShowRoslynWarning(false)}>
                取消
              </Button>
              <Button variant="danger" onClick={confirmRoslynWarning} disabled={loading}>
                我已知风险，开启完全权限
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
          placeholder="随心输入"
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
                执行中 · 继续输入可排队引导{queuedCount > 0 ? `（${queuedCount} 条待发）` : ""}
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
          title="切换对话绑定的项目"
        >
          {projectLabel ? <Folder className="h-4 w-4 shrink-0" /> : <MessageSquare className="h-4 w-4 shrink-0" />}
          <span className="truncate">{projectLabel ? `在 ${projectLabel} 中工作` : "临时对话 · 不绑定项目"}</span>
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
              <span className="min-w-0 flex-1 truncate">临时对话 · 不绑定项目</span>
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
              临时对话同样拥有完整智能体能力（技能 / Shell / Unity 工具），只是不归档到项目下。
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
  const statusLabel = error ? "失败" : loading ? "索引中" : firstScan ? "基线" : changed ? "有变更" : "干净";
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
          <span className="min-w-0 flex-1 truncate text-xs font-medium">项目变更 · {projectName || shortPath(projectPath)}</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
          <span className="shrink-0 font-mono text-xs text-muted-foreground">{changeText}</span>
        </button>
        <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" disabled={loading} onClick={onScan}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
        <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" disabled={loading} onClick={onReview} title="后台复查项目变更">
          <Bot className="h-3.5 w-3.5" />
          复查
        </Button>
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <DataLine label="Error" value={error} /> : null}
          <DataLine label="Project" value={projectPath} mono />
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
  const [open, setOpen] = useState(false);
  if (!projectPath) {
    return null;
  }
  const plan = result?.plan;
  const summary = result?.inspection?.summary;
  const ready = Boolean(plan?.readyToApply);
  const hasResult = Boolean(result);
  const tone: "ok" | "warn" | "danger" | "muted" = !hasResult ? "muted" : result?.ok && ready ? "ok" : result?.ok ? "warn" : "danger";
  const label = !hasResult ? "待检查" : ready ? "可请求" : result?.ok ? "需确认" : "受阻";
  const expected = plan?.expectedAssetPaths || [];
  const dependencyPreflight = result?.dependencyPreflight || plan?.dependencyPreflight;
  const dependencyEntries = dependencyPreflight?.entries || [];
  const visibleDependencyEntries = dependencyEntries.filter((entry) => entry.status && entry.status !== "not_detected");
  const packageOrder = dependencyPreflight?.packageOrder;
  const importQueue = packageOrder?.importQueue || plan?.source?.importQueue || [];
  const skippedInstalledSupportPackages = packageOrder?.skippedInstalledSupportPackages || [];
  const compatibility = dependencyPreflight?.compatibility;
  const dependencySummary = dependencyPreflight
    ? `${dependencyPreflight.readyForImport ? "ready" : "blocked"} / ${dependencyPreflight.blockingIssueCount || dependencyPreflight.blockingMissingCount || 0} issue(s) / ${
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
          <span className="min-w-0 flex-1 truncate text-xs font-medium">服装导入</span>
          <Badge tone={tone} className="shrink-0">
            {label}
          </Badge>
          {summary ? (
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              pkg {summary.unityPackageCount || 0} 路 prefab {summary.prefabCandidateCount || 0}
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
              title="后台复查导入方案"
            >
              <Bot className="h-3.5 w-3.5" />
              复查
            </Button>
          </div>
          {status ? <DataLine label="Status" value={status} /> : null}
          {plan?.kind ? <DataLine label="Plan" value={plan.kind} /> : null}
          {dependencySummary ? <DataLine label="Dependency preflight" value={dependencySummary} /> : null}
          {compatibility ? (
            <DataLine
              label="Avatar compatibility"
              value={`${compatibility.status || "unknown"}${compatibility.message ? ` - ${compatibility.message}` : ""}`}
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
                  return `${entry.status || "unknown"} ${entry.label || entry.id || "dependency"}${entry.blockingBeforeImport ? " [before import]" : ""}${
                    evidence.length ? `\n  ${evidence.slice(0, 3).join("\n  ")}` : ""
                  }`;
                })
                .join("\n")}
            />
          ) : null}
          {plan?.targetFolder ? <DataLine label="Target" value={plan.targetFolder} mono /> : null}
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
      return role || "Worker";
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
  const [open, setOpen] = useState(tasks.length > 0 || Boolean(error));
  const running = tasks.filter((task) => task.status === "queued" || task.status === "running" || task.status === "cancelling").length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  const failed = tasks.filter((task) => task.status === "failed").length;
  const hasActivity = Boolean(error) || tasks.length > 0;
  const statusTone: "ok" | "warn" | "danger" | "muted" = error ? "danger" : failed ? "danger" : running ? "warn" : completed ? "ok" : "muted";
  const statusLabel = error ? "需处理" : running ? `${running} 运行中` : completed ? `${completed} 完成` : "就绪";
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
          <span className="min-w-0 flex-1 truncate text-xs font-medium">后台任务</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
        </button>
        {loading ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" /> : null}
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <DataLine label="Error" value={error} /> : null}
          {recentTasks.length ? (
            <div className="grid gap-2">
              {recentTasks.map((task) => (
                <div key={task.id} className="rounded-lg border border-border bg-background px-3 py-2">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-sm font-medium">
                      {task.displayName || "后台任务"} · {subAgentRoleLabel(task.role)}
                    </span>
                    <Badge tone={subAgentStatusTone(task.status)} className="shrink-0">
                      {task.status}
                    </Badge>
                  </div>
                  <div className="mt-1 min-w-0 truncate text-xs text-muted-foreground">{task.summary || task.task || task.error || task.id}</div>
                  <div className="mt-2 flex flex-wrap justify-end gap-2">
                    <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onInspect(task.id)}>
                      <Eye className="h-3.5 w-3.5" />
                      Inspect
                    </Button>
                    {task.status === "queued" || task.status === "running" || task.status === "cancelling" ? (
                      <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onCancel(task.id)}>
                        <X className="h-3.5 w-3.5" />
                        Cancel
                      </Button>
                    ) : null}
                    {task.status === "failed" || task.status === "cancelled" ? (
                      <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onRetry(task.id)}>
                        <RefreshCw className="h-3.5 w-3.5" />
                        Retry
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
              暂无后台任务。
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
              <DataLine label="Profile" value={selected.toolProfile || "read-only"} />
              {selected.projectPath ? <DataLine label="Project" value={selected.projectPath} mono /> : null}
              {selected.summary ? <OutputBlock label="Summary" value={selected.summary} /> : null}
              {selected.error ? <OutputBlock label="Error" value={selected.error} danger /> : null}
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
        <FieldLabel label="API 供应商">
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
            <option value="custom">自定义兼容接口</option>
          </select>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {capabilities.map((capability) => (
              <Badge key={capability.label} tone={capability.tone} className="h-6 px-2 text-[10px]">
                {capability.label}
              </Badge>
            ))}
          </div>
        </FieldLabel>
        <FieldLabel label="访问密钥">
          {providerNeedsApiKey(provider) ? (
            <input
              value={apiKey}
              onChange={(event) => onApiKeyChange(event.target.value)}
              type="password"
              placeholder={keySaved ? "已保存密钥，留空即沿用" : "输入供应商 API Key"}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground/60 focus:border-primary"
              autoComplete="off"
            />
          ) : (
            <input
              value="无需密钥"
              readOnly
              className="h-10 w-full rounded-md border border-border bg-muted px-3 text-sm text-muted-foreground outline-none"
            />
          )}
        </FieldLabel>
        {requiresBaseUrl ? (
          <FieldLabel label="接口地址">
            <input
              value={baseUrl}
              onChange={(event) => onBaseUrlChange(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </FieldLabel>
        ) : null}
        <FieldLabel label="模型">
          <div className="flex min-w-0 items-center gap-2">
            {hasModelList ? (
              <select
                value={models.some((item) => item.id === model) ? model : ""}
                onChange={(event) => onModelChange(event.target.value)}
                className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              >
                {!models.some((item) => item.id === model) ? (
                  <option value="" disabled>
                    请选择模型
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
                placeholder="点击右侧刷新拉取模型列表，或手动填写"
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
              刷新模型列表
            </Button>
          </div>
          {modelsError ? <div className="mt-1.5 text-xs text-destructive/80">{modelsError}</div> : null}
          {providerTestMessage ? <div className="mt-1.5 text-xs text-muted-foreground">{providerTestMessage}</div> : null}
          {hasModelList && !modelsError ? (
            <div className="mt-1.5 text-xs text-muted-foreground">已拉取 {models.length} 个可用模型</div>
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
          保存
        </Button>
      </div>
    </form>
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
      mode: "User config",
      copyText: codexStdioText,
      copyLabel: "Codex App config",
      shared: "Shared with Codex CLI",
    },
    {
      client: "codexCli",
      title: "Codex CLI",
      mode: "User config",
      copyText: codexStdioText,
      copyLabel: "Codex CLI config",
      shared: "Shared with Codex App",
    },
    {
      client: "claudeCode",
      title: "Claude Code CLI",
      mode: "Project config",
      copyText: claudeStdioText,
      copyLabel: "Claude Code config",
    },
    {
      client: "claudeCowork",
      title: "Claude Cowork App",
      mode: "Desktop config",
      copyText: claudeStdioText,
      copyLabel: "Claude Cowork config",
    },
  ];
  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">Agent Connectors</h2>
        <Badge tone={gateway?.enabled ? "ok" : "muted"} className="shrink-0">
          {gateway?.enabled ? "Enabled" : "Disabled"}
        </Badge>
      </div>

      <div className="mt-4 grid gap-3">
        <DataLine label="Endpoint" value={status?.mcp?.url || gateway?.mcpUrl || "http://127.0.0.1:8757/mcp"} mono />
        <DataLine label="Token env" value={status?.auth?.tokenEnvVar || "VRCFORGE_AGENT_TOKEN"} mono />
        <DataLine label="Stdio bridge" value={launcherCommand || "-"} mono />
        <DataLine label="Smoke" value={smokeCommand || "-"} mono />
        <DataLine label="Tools" value={`${toolCount} read tools / ${writeTargetCount} write-request targets`} />
        <DataLine label="Config" value={gateway?.configPath || "-"} />
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        <ConnectorToggle
          label="Gateway"
          checked={Boolean(gateway?.enabled)}
          disabled={loading || !status}
          onChange={onToggleGateway}
        />
        <ConnectorToggle
          label="Write requests"
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
          Codex HTTP
        </Button>
        <Button type="button" variant="outline" disabled={!claudeText} onClick={() => onCopy(claudeText, "Claude HTTP config")}>
          <Copy className="h-4 w-4" />
          Claude HTTP
        </Button>
        <Button type="button" variant="danger" disabled={loading || !status} onClick={onRevoke}>
          Revoke token
        </Button>
      </div>

      {status?.lastCalls?.length ? (
        <div className="mt-5 overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_120px] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span className="truncate">Event</span>
            <span className="truncate">Tool</span>
            <span className="truncate">Status</span>
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
  const installed = Boolean(state?.installed);
  const needsProject = client === "claudeCode" && !selectedProjectPath;
  const installable = state?.installable !== false && !needsProject;
  const installActionDisabled = loading || !state;
  const actionMatches = normalizeConnectorClient(lastAction?.client) === client;
  const action = actionMatches ? lastAction : undefined;
  const handshake = action?.handshake;
  const statusTone = installed ? "ok" : installable ? "muted" : "warn";
  const statusLabel = installed ? "Installed" : needsProject ? "Needs project" : installable ? "Not installed" : "Needs attention";
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
            <span className="mr-2 text-foreground/70">Config</span>
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
            <div className="text-amber-700 dark:text-amber-300">Install will check the selected project and return a fix if none is available.</div>
          ) : !installable ? (
            <div className="text-amber-700 dark:text-amber-300">Install can still run diagnostics and return a repair hint.</div>
          ) : null}
          {action ? (
            <div
              className={cn(
                "mt-1 grid gap-1 rounded-md px-2 py-1.5",
                action.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-destructive/10 text-destructive",
              )}
            >
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span className="font-medium">{action.ok ? "Self-test passed" : "Self-test failed"}</span>
                {handshake?.toolCount !== undefined ? <span>{handshake.toolCount} tools</span> : null}
                {handshake?.connected ? <span>connected</span> : null}
                {handshake?.ready ? <span>ready</span> : null}
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
          Copy
        </Button>
        <Button type="button" variant="outline" className="h-8 px-3 text-xs" disabled={installActionDisabled} onClick={() => onInstall(client)}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          Install
        </Button>
        <Button type="button" variant="danger" className="h-8 px-3 text-xs" disabled={loading || !installed} onClick={() => onUninstall(client)}>
          <Trash2 className="h-3.5 w-3.5" />
          Remove
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
        {checked ? "On" : "Off"}
      </Badge>
    </button>
  );
}

function OptimizationWorkspace({
  report,
  selectedProjectPath,
  avatarPath,
  targetProfile,
  loading,
  message,
  requestingActionId,
  requestingDependencyId,
  onAvatarPathChange,
  onTargetProfileChange,
  onRefresh,
  onRequestAction,
  onRequestDependency,
}: {
  report: OptimizationPlannerReport | null;
  selectedProjectPath: string;
  avatarPath: string;
  targetProfile: string;
  loading: boolean;
  message: string;
  requestingActionId: string;
  requestingDependencyId: string;
  onAvatarPathChange: (value: string) => void;
  onTargetProfileChange: (profile: string) => void;
  onRefresh: () => void;
  onRequestAction: (card: NonNullable<OptimizationPlannerReport["actionCards"]>[number]) => void;
  onRequestDependency: (dependency: NonNullable<NonNullable<OptimizationPlannerReport["dependencyDoctor"]>["dependencies"]>[number]) => void;
}) {
  const dependencies = report?.dependencyDoctor?.dependencies ?? [];
  const actions = report?.actionCards ?? [];
  const offenders = report?.topOffenders ?? [];
  const metrics = report?.baseline?.metrics ?? {};
  const profile = report?.targetProfile;
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6">
        <section className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <Gauge className="h-4 w-4 shrink-0 text-primary" />
              <h1 className="truncate text-lg font-semibold">Optimization Dashboard</h1>
              <Badge tone="muted" className="shrink-0">
                {report?.versionStage || "0.7.2-beta"}
              </Badge>
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{selectedProjectPath || "No Unity project selected"}</div>
          </div>
          <Badge tone={report?.readOnly && report?.noProjectWrites ? "ok" : "warn"} className="shrink-0">
            {report?.readOnly && report?.noProjectWrites ? "read-only" : "needs review"}
          </Badge>
          <Badge tone={report?.directApplyExposed ? "danger" : "muted"} className="shrink-0">
            {report?.directApplyExposed ? "direct apply exposed" : "no direct apply"}
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
              <div className="truncate text-sm font-semibold">Target profile</div>
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
            <div className="mt-3 flex min-w-0 items-center gap-2">
              <input
                value={avatarPath}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                placeholder="Avatar scene path"
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading} onClick={onRefresh}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Scan
              </Button>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <OptimizationMetric label="PC rank" value={report?.baseline?.performanceHeadline?.pc?.rank || "unknown"} />
              <OptimizationMetric label="Quest rank" value={report?.baseline?.performanceHeadline?.quest?.rank || "unknown"} />
              <OptimizationMetric label="Triangles" value={formatOptimizationMetric(metrics.triangleCount)} />
              <OptimizationMetric label="Parameter bits" value={formatOptimizationMetric(metrics.expressionParameterBits)} />
            </div>
          </div>

          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">Top offenders</div>
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
          <OptimizationMetric label="Texture bytes" value={formatOptimizationMetric(metrics.textureMemoryBytes)} />
          <OptimizationMetric label="Material slots" value={formatOptimizationMetric(metrics.materialSlots)} />
          <OptimizationMetric label="Skinned meshes" value={formatOptimizationMetric(metrics.skinnedMeshCount)} />
          <OptimizationMetric label="PhysBones" value={formatOptimizationMetric(metrics.physBones)} />
          <OptimizationMetric label="Generated residue" value={formatOptimizationMetric(metrics.generatedResidueCount)} />
        </section>

        <section>
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <h2 className="truncate text-sm font-semibold">Dependency status</h2>
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
                    {item.status || "unknown"}
                  </Badge>
                </div>
                <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label="Package" value={item.matchedPackageId || "-"} />
                  <DataLine label="Version" value={item.version || "-"} />
                  <DataLine label="Risk" value={item.riskLevel || "-"} />
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
            <h2 className="truncate text-sm font-semibold">Recommended optimization order</h2>
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
                    {card.riskLevel || "unknown"}
                  </Badge>
                </div>
                <div className="mt-3 flex min-w-0 flex-wrap gap-2">
                  <Badge tone={card.level === "read-only" ? "ok" : "muted"}>{card.level || "plan-only"}</Badge>
                  <Badge tone="muted">{card.dependency || "VRCForge"}</Badge>
                  <Badge tone="muted">{card.recommendedVersionStage || "0.7.2-beta"}</Badge>
                </div>
                <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label="Benefit" value={card.expectedBenefit || "unknown"} />
                  <DataLine label="Why" value={card.whyRecommended || "-"} />
                  <DataLine label="Next" value={card.nextSafeAction || "-"} />
                  {card.requestTool ? <DataLine label="Request" value={card.requestTool} /> : null}
                  {card.blockedReason ? <DataLine label="Blocked" value={card.blockedReason} /> : null}
                </div>
                {card.requestTool ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="mt-3 h-8 px-3 text-xs"
                    disabled={loading || !selectedProjectPath || !avatarPath.trim() || requestingActionId === card.id}
                    onClick={() => onRequestAction(card)}
                  >
                    {requestingActionId === card.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                    Request
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function OptimizationMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function CheckpointWorkspace({
  checkpoints,
  selectedProjectPath,
  preview,
  loading,
  restoringId,
  message,
  onRefresh,
  onPreview,
  onRestore,
}: {
  checkpoints: AgentCheckpoint[];
  selectedProjectPath: string;
  preview: AgentCheckpointPreview | null;
  loading: boolean;
  restoringId: string;
  message: string;
  onRefresh: () => void;
  onPreview: (checkpointId: string) => void;
  onRestore: (checkpointId: string) => void;
}) {
  const selectedId = preview?.checkpoint?.id || "";
  const changedFiles = preview?.changedFiles || [];
  const workingTreeStatus = preview?.workingTreeStatus || [];
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[380px_minmax(0,1fr)]">
        <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-4 flex items-center gap-2">
            <History className="h-4 w-4 shrink-0 text-primary" />
            <div className="truncate text-sm font-semibold">Checkpoints</div>
            <Badge tone="muted" className="ml-auto shrink-0">
              {checkpoints.length}
            </Badge>
            <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onRefresh} disabled={loading}>
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            </Button>
          </div>
          {selectedProjectPath ? <div className="mb-3 truncate text-xs text-muted-foreground">{selectedProjectPath}</div> : null}
          <div className="max-h-[calc(100vh-220px)] space-y-2 overflow-auto pr-1">
            {checkpoints.length === 0 ? (
              <div className="rounded-md border border-dashed border-border px-3 py-6 text-center text-xs text-muted-foreground">
                No pre-write checkpoints yet.
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
                    {checkpoint.status || (checkpoint.ok ? "ready" : "unavailable")}
                  </Badge>
                </div>
                <div className="truncate text-xs text-muted-foreground">{checkpoint.targetTool || "-"}</div>
                <div className="truncate text-xs text-muted-foreground">{formatCheckpointTime(checkpoint.createdAt)}</div>
              </button>
            ))}
          </div>
        </section>

        <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
          <div className="mb-5 flex items-center gap-2">
            <RotateCcw className="h-4 w-4 shrink-0 text-primary" />
            <div className="truncate text-sm font-semibold">Restore Preview</div>
            {preview ? (
              <Badge tone={preview.ok ? "ok" : "danger"} className="ml-auto shrink-0">
                {preview.ok ? "ready" : "blocked"}
              </Badge>
            ) : null}
          </div>

          {!preview ? (
            <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
              Select a checkpoint to inspect the rollback.
            </div>
          ) : (
            <div className="grid gap-4">
              <div className="grid gap-3">
                <DataLine label="Checkpoint" value={preview.checkpoint?.id || "-"} mono />
                <DataLine label="Target" value={preview.checkpoint?.targetTool || "-"} />
                <DataLine label="Project" value={preview.checkpoint?.projectRoot || "-"} />
                <DataLine label="Git ref" value={shortRef(preview.checkpoint?.checkpointRef)} mono />
                {preview.error ? <DataLine label="Error" value={preview.error} /> : null}
              </div>
              <OutputBlock label="Changed files" value={changedFiles.join("\n")} />
              <OutputBlock label="Working tree" value={workingTreeStatus.join("\n")} />
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
  onRefreshPackages,
  onPreflightPackage,
  onImportPackage,
  onExportPackage,
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
  onRefreshPackages: () => void;
  onPreflightPackage: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImportPackage: (packagePath: string) => Promise<unknown>;
  onExportPackage: (skillName: string, outputPath: string, release: boolean) => Promise<unknown>;
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
            <div className="truncate text-sm font-semibold">Skills</div>
            <Badge tone="muted" className="ml-auto shrink-0">
              {skillCount}
            </Badge>
            <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onCheck} disabled={saving}>
              Check
            </Button>
          </div>
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={skillQuery}
              onChange={(event) => setSkillQuery(event.target.value)}
              placeholder="搜索能力…"
              className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
            />
          </div>
          <div className="max-h-[calc(100vh-230px)] space-y-2 overflow-auto pr-1">
            {groupedSkills.length === 0 ? (
              <div className="px-3 py-4 text-xs text-muted-foreground">没有匹配的能力。</div>
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
            <div className="truncate text-sm font-semibold">{editable ? "User Skill" : "Read Only Skill"}</div>
            <Badge tone={checkTone} className="ml-auto shrink-0">
              {selectedCheck?.status || draft.permissionMode || "instruction_only"}
            </Badge>
          </div>
          <div className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label="Name">
                <input
                  value={draft.name || ""}
                  onChange={(event) => onDraftChange({ ...draft, name: event.target.value })}
                  disabled={!editable || userSkillSelected}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Title">
                <input
                  value={draft.title || ""}
                  onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-4">
              <FieldLabel label="Category">
                <input
                  value={draft.category || ""}
                  onChange={(event) => onDraftChange({ ...draft, category: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Type">
                <input
                  value={draft.skillType || "package"}
                  onChange={(event) => onDraftChange({ ...draft, skillType: event.target.value })}
                  disabled
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Permission">
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
              <FieldLabel label="Risk">
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
                <span className="truncate">Enabled</span>
              </label>
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={draft.userInvocable !== false}
                  onChange={(event) => onDraftChange({ ...draft, userInvocable: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">Slash callable</span>
              </label>
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={Boolean(draft.disableModelInvocation)}
                  onChange={(event) => onDraftChange({ ...draft, disableModelInvocation: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">Manual only</span>
              </label>
            </div>
            <FieldLabel label="When To Use">
              <textarea
                value={draft.whenToUse || ""}
                onChange={(event) => onDraftChange({ ...draft, whenToUse: event.target.value })}
                disabled={!editable}
                className="min-h-20 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </FieldLabel>
            <FieldLabel label="Description">
              <textarea
                value={draft.description || ""}
                onChange={(event) => onDraftChange({ ...draft, description: event.target.value })}
                disabled={!editable}
                className="min-h-16 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </FieldLabel>
            <div className="grid gap-4 md:grid-cols-3">
              <FieldLabel label="Allowed Tools">
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
              <FieldLabel label="Disallowed Tools">
                <input
                  value={(draft.disallowedTools || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, disallowedTools: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Entrypoint">
                <input
                  value={draft.entrypointTool || ""}
                  onChange={(event) => onDraftChange({ ...draft, entrypointTool: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label="Argument Hint">
                <input
                  value={draft.argumentHint || ""}
                  onChange={(event) => onDraftChange({ ...draft, argumentHint: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Test Command">
                <input
                  value={draft.testCommand || ""}
                  onChange={(event) => onDraftChange({ ...draft, testCommand: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label="Inputs">
                <textarea
                  value={(draft.inputs || []).join("\n")}
                  onChange={(event) => onDraftChange({ ...draft, inputs: splitLines(event.target.value) })}
                  disabled={!editable}
                  className="min-h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Outputs">
                <textarea
                  value={(draft.outputs || []).join("\n")}
                  onChange={(event) => onDraftChange({ ...draft, outputs: splitLines(event.target.value) })}
                  disabled={!editable}
                  className="min-h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label="Side Effects">
                <input
                  value={draft.sideEffects || ""}
                  onChange={(event) => onDraftChange({ ...draft, sideEffects: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Backup Restore">
                <input
                  value={draft.backupRestore || ""}
                  onChange={(event) => onDraftChange({ ...draft, backupRestore: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <FieldLabel label="Requires Env">
                <input
                  value={(draft.requiresEnv || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, requiresEnv: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Requires Binaries">
                <input
                  value={(draft.requiresBinaries || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, requiresBinaries: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Supported OS">
                <input
                  value={(draft.supportedOs || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, supportedOs: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <FieldLabel label="Support Files">
                <input
                  value={(draft.supportFiles || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, supportFiles: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
              <FieldLabel label="Tags">
                <input
                  value={(draft.tags || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, tags: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </FieldLabel>
            </div>
            <FieldLabel label="Instructions">
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
              New
            </Button>
            {userSkillSelected ? (
              <Button type="button" variant="danger" onClick={onDelete} disabled={saving}>
                Delete
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
          onRefresh={onRefreshPackages}
          onPreflight={onPreflightPackage}
          onImport={onImportPackage}
          onExport={onExportPackage}
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
  onRefresh,
  onPreflight,
  onImport,
  onExport,
}: {
  packages: SkillPackageEntry[];
  packageStore: string;
  loading: boolean;
  message: string;
  error: string;
  onRefresh: () => void;
  onPreflight: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImport: (packagePath: string) => Promise<unknown>;
  onExport: (skillName: string, outputPath: string, release: boolean) => Promise<unknown>;
}) {
  const [packagePath, setPackagePath] = useState("");
  const [exportSkillName, setExportSkillName] = useState("");
  const [exportPath, setExportPath] = useState("");
  const [releaseExport, setReleaseExport] = useState(false);
  const [preflight, setPreflight] = useState<SkillPackagePreflight | null>(null);
  const [localMessage, setLocalMessage] = useState("");
  const [localError, setLocalError] = useState("");
  const preview = normalizeSkillPackagePreview(preflight);
  async function runPreflight() {
    if (!packagePath.trim()) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      const payload = await onPreflight(packagePath.trim());
      setPreflight(payload);
      setLocalMessage("Preflight complete");
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
      setLocalMessage("Package imported");
      setPreflight(null);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  async function runExport() {
    if (!exportSkillName.trim() || !exportPath.trim()) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      await onExport(exportSkillName.trim(), exportPath.trim(), releaseExport);
      setLocalMessage(releaseExport ? "Release package exported" : "Dev package exported");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  const displayMessage = localMessage || message;
  const displayError = localError || error;
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
      <div className="mb-5 flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">.vsk Package Manager</div>
        <Badge tone="muted" className="shrink-0">
          {packages.length}
        </Badge>
        <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onRefresh} disabled={loading}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
      </div>

      <div className="grid gap-4">
        <div className="grid gap-3">
          <DataLine label="Store" value={packageStore || "-"} />
          {displayMessage ? <Badge tone="ok" className="w-fit">{displayMessage}</Badge> : null}
          {displayError ? <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">{displayError}</div> : null}
        </div>

        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto]">
          <FieldLabel label="Package Path">
            <input
              value={packagePath}
              onChange={(event) => setPackagePath(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </FieldLabel>
          <Button type="button" variant="outline" className="self-end" disabled={loading || !packagePath.trim()} onClick={() => void runPreflight()}>
            <Eye className="h-4 w-4" />
            Preflight
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
              <DataLine label="Risk" value={skillPackageRisk(preview)} />
              <DataLine label="Signer" value={skillPackageSigner(preview)} mono />
            </div>
            <OutputBlock label="Permissions" value={skillPackagePermissions(preview).join("\n")} />
            {preview.manifest ? <OutputBlock label="Manifest" value={formatPayload(preview.manifest)} /> : null}
          </div>
        ) : null}

        <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <FieldLabel label="Skill Name">
              <input
                value={exportSkillName}
                onChange={(event) => setExportSkillName(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
            <FieldLabel label="Output Path">
              <input
                value={exportPath}
                onChange={(event) => setExportPath(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </FieldLabel>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <label className="mr-auto flex h-9 min-w-0 items-center gap-2 rounded-md border border-border px-3 text-sm text-muted-foreground">
              <input type="checkbox" checked={releaseExport} onChange={(event) => setReleaseExport(event.target.checked)} />
              <span className="truncate">Signed release</span>
            </label>
            <Button type="button" variant="outline" disabled={loading || !exportSkillName.trim() || !exportPath.trim()} onClick={() => void runExport()}>
              <Copy className="h-4 w-4" />
              Export
            </Button>
          </div>
        </div>

        <div className="overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-[minmax(0,1fr)_100px_160px] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span className="truncate">Package</span>
            <span className="truncate">Risk</span>
            <span className="truncate">Status</span>
          </div>
          {packages.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">No installed .vsk packages.</div>
          ) : null}
          {packages.map((pkg, index) => (
            <div key={`${skillPackageId(pkg)}-${index}`} className="grid grid-cols-[minmax(0,1fr)_100px_160px] gap-2 border-b border-border/60 px-3 py-2 text-xs last:border-b-0">
              <div className="min-w-0">
                <div className="truncate font-medium">{skillPackageTitle(pkg)}</div>
                <div className="truncate text-muted-foreground">{skillPackageId(pkg)}</div>
              </div>
              <span className="truncate">{skillPackageRisk(pkg)}</span>
              <div className="flex min-w-0 flex-wrap gap-1">
                {skillPackageLabels(pkg).map((label) => (
                  <Badge key={label} tone={skillPackageLabelTone(label)} className="h-5 px-1.5 text-[10px]">
                    {label}
                  </Badge>
                ))}
              </div>
            </div>
          ))}
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
  if (pkg.enabled === false || pkg.available === false || errorText.includes("blocked")) {
    labels.push("Blocked");
  }
  return [...new Set(labels)];
}

function skillPackageLabelTone(label: string): "ok" | "warn" | "danger" | "muted" {
  if (label === "Signed" || label === "Built-in") {
    return "ok";
  }
  if (label === "Signature mismatch" || label === "Blocked") {
    return "danger";
  }
  if (label === "Unsigned" || label === "Dev") {
    return "warn";
  }
  return "muted";
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
              <h1 className="truncate text-lg font-semibold">Startup Doctor</h1>
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="truncate">{report?.version || "runtime"}</span>
                {report?.scope ? <span className="truncate">{report.scope}</span> : null}
                {report?.selectedUnityEnvironment?.label ? <span className="truncate">{report.selectedUnityEnvironment.label}</span> : null}
              </div>
            </div>
            <Badge tone={report?.ok ? "ok" : "warn"} className="shrink-0">
              {report?.ok ? "Ready" : "Needs attention"}
            </Badge>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <DoctorSummaryTile label="OK" value={summary?.okCount ?? 0} tone="ok" />
            <DoctorSummaryTile label="Warning" value={summary?.warningCount ?? 0} tone="warn" />
            <DoctorSummaryTile label="Error" value={summary?.errorCount ?? 0} tone="danger" />
            <DoctorSummaryTile label="Unknown" value={summary?.unknownCount ?? 0} tone="muted" />
          </div>
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            {message ? (
              <Badge tone="ok" className="mr-auto shrink-0">
                {message}
              </Badge>
            ) : null}
            <Button type="button" variant="outline" onClick={onOpenSettings}>
              <Settings className="h-4 w-4" />
              Settings
            </Button>
            <Button type="button" variant="outline" onClick={onExportSupportBundle} disabled={exportingSupportBundle}>
              {exportingSupportBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              Support Bundle
            </Button>
            <Button type="button" variant="outline" onClick={onCopy} disabled={!report}>
              <Copy className="h-4 w-4" />
              Copy
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
              <h2 className="truncate text-sm font-semibold">Suggested fixes</h2>
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
              {loading ? "正在运行诊断…" : "暂无诊断结果。"}
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
          <DataLine label="What failed" value={check.whatFailed || (check.status === "ok" ? "-" : check.message)} />
          <DataLine label="Why" value={check.whyItMatters || "-"} />
          <DataLine label="How to fix" value={check.howToFix || "-"} />
          {check.fixCommand ? <DataLine label="Fix" value={check.fixCommand} /> : null}
          {canRepairUnityBridge ? (
            <div className="flex justify-end">
              <Button type="button" variant="outline" className="h-8 px-3 text-xs" onClick={onRepairUnityBridge} disabled={repairingUnityBridge}>
                {repairingUnityBridge ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
                Repair bridge
              </Button>
            </div>
          ) : null}
          <DataLine label="Message" value={check.message || "-"} />
          {check.detail !== undefined ? <OutputBlock label="Detail" value={formatPayload(check.detail)} /> : null}
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
    return <ShellResultCard title={item.error === "rejected" ? "已驳回" : "执行结果"} result={item.result} error={item.error} />;
  }

  if (item.type === "compact") {
    return (
      <div className="rounded-xl border border-dashed border-border bg-muted/40 px-4 py-3">
        <div className="mb-2 text-xs font-medium text-muted-foreground">已压缩的历史</div>
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
              {task.displayName || "后台任务"} · {subAgentRoleLabel(task.role)}
            </span>
            <Badge tone={subAgentStatusTone(task.status)} className="shrink-0">
              {task.status}
            </Badge>
          </div>
          <p className="whitespace-pre-wrap break-words leading-relaxed text-muted-foreground">
            {task.summary || task.error || task.task || "No summary was returned."}
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
            这句话我还没办法直接理解——当前在用本地关键词规划，只认识「日志、截图、表情、材质、健康检查」这类固定指令。
          </p>
          <p className="text-muted-foreground">
            在设置里绑定模型供应商后，就能用自然语言交流并自动规划工具调用；如果已绑定密钥，说明 AI 规划还没启用，重启核心或检查供应商配置即可。
          </p>
          <Button type="button" variant="outline" className="h-8 px-3 text-xs" onClick={() => onOpenSettings?.()}>
            <Settings className="mr-1 h-3.5 w-3.5" />
            打开设置
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
                我接下来会{displayStep(nextStep)}
                {response.plan.skillTool ? `：${response.plan.skillTool}` : ""}
              </span>
            </p>
          ) : null}
          <div className="mt-2 flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>{response.plan.plannerLabel || displayPlanner(response.plan.planner)}</span>
            {item.elapsedSeconds ? <span>· 已运行 {formatDuration(item.elapsedSeconds)}</span> : null}
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
                ? `退出码 ${shell.result.exitCode} · ${formatDuration(shell.result.durationSeconds)}`
                : awaitingApproval
                  ? "等待确认"
                  : `风险 ${shell.classification.risk}`
            }
          >
            <DataLine label="目录" value={shell.classification.cwd} />
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
                <DataLine label="耗时" value={formatDuration(shell.result.durationSeconds)} />
                <OutputBlock label="输出" value={shell.result.stdout} />
                {shell.result.stderr ? <OutputBlock label="错误输出" value={shell.result.stderr} danger /> : null}
              </>
            ) : null}
          </RunRow>
        ) : null}

        {skill ? (
          <RunRow icon="skill" title={skill.tool || "能力调用"} statusTone={skillTone(skill)} statusLabel={displaySkillStatus(skill.status)}>
            <DataLine label="工具" value={skill.tool || "-"} mono />
            {skill.category ? <DataLine label="类别" value={skill.category} /> : null}
            {skill.error ? <DataLine label="错误" value={skill.error} /> : null}
            {skill.result !== undefined ? <OutputBlock label="数据" value={formatPayload(skill.result)} /> : null}
          </RunRow>
        ) : null}

        {awaitingApproval ? (
          <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span>等待确认 — 请在下方输入框上方的审批区处理</span>
          </div>
        ) : null}
        {shell?.error ? (
          <RunRow icon="shell" title="执行错误" statusTone="danger" statusLabel="失败">
            <DataLine label="错误" value={shell.error} />
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
              label={item.title || item.kind || "Reasoning"}
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
  return (
    <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 shadow-panel">
      <div className="flex min-w-0 items-center gap-2">
        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
        <div className="truncate text-sm font-semibold">待确认</div>
        <Badge tone="warn" className="ml-auto shrink-0">
          {approval.riskLevel || "high"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-3">
        <DataLine label="命令" value={approval.preview?.command || "-"} mono />
        <DataLine label="目录" value={approval.preview?.cwd || "-"} />
        <DataLine label="原因" value={approval.reason || "-"} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4" />
          驳回
        </Button>
        <Button variant="primary" disabled={loading} onClick={() => onApprove(approval.id)}>
          <Check className="h-4 w-4" />
          同意执行
        </Button>
      </div>
    </section>
  );
}

function ShellResultCard({ title, result, error }: { title: string; result?: AgentShellResult; error?: string }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 items-center gap-2">
        <TerminalSquare className="h-4 w-4 shrink-0 text-primary" />
        <div className="truncate text-sm font-semibold">{title}</div>
        {result ? (
          <Badge tone={result.ok ? "ok" : "danger"} className="ml-auto shrink-0">
            退出码 {result.exitCode}
          </Badge>
        ) : null}
      </div>
      {error ? <DataLine label="错误" value={error} /> : null}
      {result ? (
        <div className="grid gap-3">
          <DataLine label="耗时" value={`${result.durationSeconds}s`} />
          <OutputBlock label="输出" value={result.stdout} />
          {result.stderr ? <OutputBlock label="错误输出" value={result.stderr} danger /> : null}
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
  return (
    <section className="mt-8 min-w-0">
      {onToggleCollapse ? (
        <button
          type="button"
          onClick={onToggleCollapse}
          title={collapsed ? "展开" : "折叠"}
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
          title="项目菜单"
          onClick={onOpenMenu}
          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 hover:bg-background hover:text-foreground group-hover:opacity-100"
        >
          <MoreHorizontal className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onToggleCollapse ? (
        <button
          type="button"
          title={collapsed ? "展开对话" : "折叠对话"}
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
          title={pinned ? "取消置顶" : "置顶"}
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
          title="永久删除"
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
  { label: "Roslyn 高级", pattern: /roslyn/i },
  { label: "捏脸与表情", pattern: /blendshape|face|expression/i },
  { label: "材质与外观", pattern: /material|shader|texture/i },
  { label: "衣柜与 FX", pattern: /clothing|outfit|wardrobe|gesture|\bfx\b|fx_/i },
  { label: "参数优化", pattern: /parameter|param_/i },
  { label: "截图与视觉", pattern: /screenshot|capture|scene_view|vision|game_view/i },
  { label: "包管理", pattern: /package|vpm|addon|modular/i },
  { label: "审批与备份", pattern: /approval|approve|backup|restore|rollback/i },
  { label: "Shell 与调试", pattern: /shell|command|console|debug/i },
  { label: "诊断与状态", pattern: /\blog|health|diagno|status|check/i },
  { label: "Avatar 扫描", pattern: /scan|avatar|inventory|control|animation|toggle/i },
];
const SKILL_DOMAIN_FALLBACK = "其他";
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
    { label: "text", tone: "muted" },
    { label: "structured JSON", tone: "muted" },
  ];
  if (["gemini", "openai", "openrouter", "vertexai"].includes(provider)) {
    capabilities.push({ label: "vision", tone: "muted" });
  }
  if (local) {
    capabilities.push({ label: "local", tone: "ok" }, { label: "offline", tone: "ok" }, { label: "free/local", tone: "ok" });
  }
  if (paid) {
    capabilities.push({ label: "paid API", tone: "warn" });
  }
  if (["gemini", "anthropic", "openai", "openrouter", "vertexai"].includes(provider)) {
    capabilities.push({ label: "long context", tone: "muted" });
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
    return "本地规划";
  }
  if (planner === "llm") {
    return "AI 规划";
  }
  return planner || "规划";
}

function displayStep(step: string): string {
  const labels: Record<string, string> = {
    classify_shell: "检查命令风险",
    execute_shell: "执行命令",
    call_skill: "调用能力",
    request_approval: "等待确认",
    await_user_instruction: "等待输入",
    done: "完成",
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
    executed: "已运行",
    loaded: "已加载",
    failed: "失败",
    blocked: "已阻止",
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
    return `${seconds}秒`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) {
    return rest > 0 ? `${minutes}分${rest}秒` : `${minutes}分钟`;
  }
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes > 0 ? `${hours}小时${restMinutes}分` : `${hours}小时`;
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
        `后台任务 ${task.displayName || task.id} (${subAgentRoleLabel(task.role)}) ${task.status}`,
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
    (entry) => `${entry.role === "user" ? "用户" : "助手"}: ${clipText(entry.text.replace(/\s+/g, " ").trim(), COMPACT_ENTRY_MAX_CHARS)}`,
  );
  let lines = entries;
  if (entries.length > COMPACT_HEAD_ENTRIES + COMPACT_TAIL_ENTRIES) {
    const omitted = entries.length - COMPACT_HEAD_ENTRIES - COMPACT_TAIL_ENTRIES;
    lines = [
      ...entries.slice(0, COMPACT_HEAD_ENTRIES),
      `（中间已省略 ${omitted} 条消息）`,
      ...entries.slice(entries.length - COMPACT_TAIL_ENTRIES),
    ];
  }
  return `（历史压缩摘要，共 ${entries.length} 条消息）\n${lines.join("\n")}`;
}
