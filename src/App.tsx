import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  EyeOff,
  Folder,
  FolderPlus,
  History,
  Loader2,
  MessageSquare,
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
  AgentSkill,
  AgentSkillRegistry,
  AgentSkillResult,
  AgentShellResult,
  ApiError,
  AppBootstrap,
  ChatHistoryEntry,
  approveAgentApproval,
  checkSkills,
  compactAgentHistory,
  createSkill,
  deleteSkill,
  fetchCheckpoints,
  fetchBootstrap,
  fetchSkills,
  AgentSkillCheck,
  ExecutionMode,
  PermissionState,
  fetchAgentNotes,
  fetchChats,
  fetchProjectPrefs,
  fetchProviderModels,
  ProjectPrefs,
  previewRestoreCheckpoint,
  rejectAgentApproval,
  requestRestoreCheckpoint,
  saveChats,
  saveProjectPrefs,
  saveAgentNotes,
  sendAgentMessage,
  setAppSessionToken,
  updateApiConfig,
  updatePermission,
  updateSkill,
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

type ConversationItem =
  | { id: string; type: "user"; text: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse; elapsedSeconds?: number }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string }
  | { id: string; type: "error"; text: string }
  | { id: string; type: "compact"; text: string };

type ActiveView = "chat" | "skills" | "checkpoints" | "settings";

type ChatThread = {
  id: string;
  sessionId: string;
  title: string;
  projectPath: string;
  pinned?: boolean;
  items: ConversationItem[];
};

const ONBOARDING_FLAG_KEY = "vrcforge_onboarded";
const COLLAPSED_PROJECTS_KEY = "vrcforge_collapsed_projects";
// 临时对话区折叠状态复用 collapsedProjects 存储；保留 key 不会与真实项目路径冲突。
const TEMP_CHATS_COLLAPSE_KEY = "__temp_chats__";

const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";

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


export default function App() {
  const [endpoint, setEndpoint] = useState(FALLBACK_ENDPOINT);
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(null);
  const [backendMessage, setBackendMessage] = useState("starting");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
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
  const [skillRegistry, setSkillRegistry] = useState<AgentSkillRegistry | null>(null);
  const [skillCheck, setSkillCheck] = useState<AgentSkillCheck | null>(null);
  const [selectedSkillName, setSelectedSkillName] = useState("");
  const [skillDraft, setSkillDraft] = useState<Partial<AgentSkill>>(emptySkillDraft());
  const [savingSkill, setSavingSkill] = useState(false);
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
  const needsApiSetup = runtimeConnected && Boolean(apiConfig?.apiKeyRequired && !apiConfig.apiKeyPresent);
  const apiKeySaved = Boolean(apiConfig?.apiKeyPresent && (apiConfig?.provider || "") === apiProvider);

  const hiddenPathSet = useMemo(
    () => new Set(projectPrefs.hiddenPaths.map((path) => path.toLowerCase())),
    [projectPrefs.hiddenPaths],
  );
  const customPathSet = useMemo(
    () => new Set(projectPrefs.customPaths.map((path) => path.toLowerCase())),
    [projectPrefs.customPaths],
  );
  const projectItems = useMemo(
    () => projects.filter((project) => !hiddenPathSet.has((project.path || "").toLowerCase())).slice(0, 24),
    [projects, hiddenPathSet],
  );
  const hiddenProjects = useMemo(
    () => projects.filter((project) => hiddenPathSet.has((project.path || "").toLowerCase())),
    [projects, hiddenPathSet],
  );
  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const conversation = activeChat?.items ?? [];
  const sessionId = activeChat?.sessionId ?? "";
  const activeProjectName =
    projectItems.find((project) => projectKey(project) === activeProjectPath)?.name ||
    (activeProjectPath ? shortPath(activeProjectPath) : "");
  const temporaryChats = sortChatsByPin(chats.filter((chat) => !chat.projectPath));
  const projectPromptTitle = activeProjectPath && activeProjectName ? `我们应该在 ${activeProjectName} 中构建什么？` : "随心聊点什么？";
  const emptyProjectState = useMemo(() => {
    if (projectItems.length > 0) {
      return null;
    }
    if (loading && !error) {
      return { name: "扫描中", meta: "wait" };
    }
    if (error) {
      return { name: "刷新失败", meta: "retry" };
    }
    if (!runtimeConnected) {
      return { name: "核心未连接", meta: "retry" };
    }
    return { name: "未发现 Unity 项目", meta: "empty" };
  }, [error, loading, projectItems.length, runtimeConnected]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
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
          pinned: chat.pinned === true,
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
    if (activeView === "checkpoints" && runtimeConnected) {
      void loadCheckpoints();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

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
      setError(cause instanceof Error ? cause.message : String(cause));
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
  }

  async function refreshSilently(target = endpoint) {
    try {
      const payload = await fetchBootstrap(target);
      setBootstrap(payload);
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
      const response = await sendAgentMessage(targetEndpoint, message, chatSessionId || undefined, history);
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
    const existingEmpty = chats.find((chat) => !chat.projectPath && chat.items.length === 0);
    if (existingEmpty) {
      setActiveChatId(existingEmpty.id);
      return;
    }
    const id = `chat-${Date.now()}`;
    setChats((list) => [{ id, sessionId: "", title: "", projectPath: "", items: [] }, ...list]);
    setActiveChatId(id);
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
    setChats((list) => [{ id, sessionId: "", title: "", projectPath, items: [] }, ...list]);
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
      customPaths: projectPrefs.customPaths.filter((item) => item.toLowerCase() !== path.toLowerCase()),
    });
  }

  function hideProject(path: string) {
    if (!path) {
      return;
    }
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => item.toLowerCase() !== path.toLowerCase()), path],
    });
    if (activeProjectPath.toLowerCase() === path.toLowerCase()) {
      newConversation("");
    }
  }

  function unhideProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: projectPrefs.hiddenPaths.filter((item) => item.toLowerCase() !== path.toLowerCase()),
    });
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
    const latest = chats.find((chat) => chat.projectPath === projectPath);
    setActiveChatId(latest ? latest.id : "");
    setError("");
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
      const payload = await fetchSkills(targetEndpoint);
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

  async function openSettings() {
    setActiveView("settings");
    setError("");
    setNotesMessage("");
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
    } catch (cause) {
      setAgentNotesLoaded(false);
      setError(cause instanceof Error ? cause.message : String(cause));
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

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground">
      <div className="grid h-screen grid-cols-[320px_minmax(0,1fr)]">
        <aside className="flex h-screen min-w-0 flex-col overflow-y-auto border-r border-border bg-sidebar px-4 py-4">
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
                const projectChats = sortChatsByPin(chats.filter((chat) => chat.projectPath === key));
                const collapsed = Boolean(collapsedProjects[key]);
                return (
                  <div key={key} className="min-w-0">
                    <SidebarProject
                      name={project.name || project.path || "Unity Project"}
                      meta={project.editorVersion || project.unityVersion || (project.sources ?? []).join("+")}
                      active={activeView === "chat" && key === activeProjectPath}
                      collapsed={collapsed}
                      hasChats={projectChats.length > 0}
                      onToggleCollapse={() => toggleProjectCollapse(key)}
                      onClick={() => selectProject(key)}
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
                {activeView === "skills" ? "能力库" : activeView === "settings" ? "设置" : activeChat ? activeChat.title || "当前会话" : "新任务"}
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

          {error ? (
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

          {activeView === "skills" ? (
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
          ) : activeView === "settings" ? (
            <div className="min-h-0 flex-1 overflow-y-auto px-6 py-10">
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
                      keySaved={apiKeySaved}
                      onLoadModels={() => void loadModels()}
                      onProviderChange={handleProviderChange}
                      onApiKeyChange={setApiKey}
                      onBaseUrlChange={setApiBaseUrl}
                      onModelChange={setApiModel}
                      onSubmit={saveApiProvider}
                    />
                  </div>
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
          ) : needsApiSetup ? (
            <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto p-8">
              <div className="w-full max-w-2xl">
                <h1 className="text-2xl font-semibold tracking-tight">连接模型供应商</h1>
                <p className="mt-1 mb-6 text-sm text-muted-foreground">首次使用需要配置 API 供应商与模型，保存后即可开始对话。</p>
                <ProviderSetup
                  provider={apiProvider}
                  apiKey={apiKey}
                  baseUrl={apiBaseUrl}
                  model={apiModel}
                  saving={savingApiConfig}
                  models={modelOptions}
                  loadingModels={loadingModels}
                  modelsError={modelsError}
                  keySaved={apiKeySaved}
                  onLoadModels={() => void loadModels()}
                  onProviderChange={handleProviderChange}
                  onApiKeyChange={setApiKey}
                  onBaseUrlChange={setApiBaseUrl}
                  onModelChange={setApiModel}
                  onSubmit={saveApiProvider}
                />
              </div>
            </div>
          ) : conversation.length === 0 ? (
            <div className="flex min-h-0 flex-1 items-center justify-center p-8">
              <div className="w-full max-w-4xl">
                {projectPromptTitle ? <h1 className="mb-8 text-center text-3xl font-semibold tracking-normal">{projectPromptTitle}</h1> : null}
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
                  {conversation.map((item) => (
                    <ConversationCard key={item.id} item={item} onOpenSettings={() => void openSettings()} />
                  ))}
                  {sending && currentTurn ? <RunningIndicator startedAt={currentTurn.startedAt} text={currentTurn.text} /> : null}
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
            <div className="mt-4 min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
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
            const menuKey = menuPath.toLowerCase();
            const isCustom = customPathSet.has(menuKey);
            const collapsed = Boolean(collapsedProjects[menuPath]);
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
                  className="fixed z-50 w-44 rounded-lg border border-border bg-card p-1.5 shadow-panel"
                  style={{
                    left: Math.min(projectMenu.x, window.innerWidth - 190),
                    top: Math.min(projectMenu.y, window.innerHeight - 190),
                  }}
                >
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
                      toggleProjectCollapse(menuPath);
                      setProjectMenu(null);
                    }}
                  >
                    {collapsed ? <ChevronDown className="h-4 w-4 shrink-0" /> : <ChevronRight className="h-4 w-4 shrink-0" />}
                    {collapsed ? "展开对话" : "折叠对话"}
                  </button>
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

function ProviderSetup({
  provider,
  apiKey,
  baseUrl,
  model,
  saving,
  models,
  loadingModels,
  modelsError,
  keySaved = false,
  onLoadModels,
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
  keySaved?: boolean;
  onLoadModels: () => void;
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onSubmit: (event?: FormEvent) => void;
}) {
  const requiresBaseUrl = provider === "openai" || provider === "openai-compatible" || provider === "ollama" || provider === "vertexai";
  const hasModelList = models.length > 0;

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
            <option value="openai">兼容接口</option>
            <option value="ollama">Ollama</option>
            <option value="vertexai">Vertex AI</option>
          </select>
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
          {hasModelList && !modelsError ? (
            <div className="mt-1.5 text-xs text-muted-foreground">已拉取 {models.length} 个可用模型</div>
          ) : null}
        </FieldLabel>
      </div>
      <div className="mt-5 flex justify-end">
        <Button disabled={saving || (providerNeedsApiKey(provider) && !apiKey.trim() && !keySaved) || !model.trim()} type="submit">
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          保存
        </Button>
      </div>
    </form>
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
      </div>
    </div>
  );
}
function FieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-2 text-sm">
      <span className="truncate font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
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
        <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">{item.text}</pre>
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

function RunningIndicator({ startedAt, text }: { startedAt: number; text: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return (
    <div className="flex justify-start">
      <div className="flex max-w-[85%] min-w-0 items-center gap-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm text-muted-foreground shadow-panel">
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-primary" />
        <span className="min-w-0 truncate">正在处理「{text}」</span>
        <span className="shrink-0 font-mono text-xs">已运行 {formatDuration(seconds)}</span>
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
  onClick,
  onToggleCollapse,
  onContextMenu,
}: {
  name: string;
  meta?: string;
  active?: boolean;
  collapsed?: boolean;
  hasChats?: boolean;
  onClick?: () => void;
  onToggleCollapse?: () => void;
  onContextMenu?: (event: ReactMouseEvent) => void;
}) {
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
        {meta ? <span className="max-w-[78px] shrink-0 truncate text-xs text-muted-foreground">{meta}</span> : null}
      </button>
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
      return "claude-3-5-sonnet-latest";
    case "openai":
    case "openai-compatible":
      return "gpt-4o-mini";
    case "ollama":
      return "llava";
    case "vertexai":
      return "gemini-2.5-flash";
    case "gemini":
    default:
      return "gemini-2.5-flash";
  }
}

function defaultBaseUrlForProvider(provider: string): string {
  switch (provider) {
    case "openai":
      return "https://api.openai.com/v1";
    case "ollama":
      return "http://127.0.0.1:11434/v1";
    default:
      return "";
  }
}

function providerNeedsApiKey(provider: string): boolean {
  return provider !== "ollama" && provider !== "vertexai";
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
