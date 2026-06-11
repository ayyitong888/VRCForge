import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  Folder,
  Loader2,
  MessageSquare,
  Moon,
  Plus,
  RefreshCw,
  Send,
  Settings,
  Shield,
  Sparkles,
  Sun,
  TerminalSquare,
  Wrench,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import {
  AgentApproval,
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
  fetchBootstrap,
  fetchSkills,
  AgentSkillCheck,
  ExecutionMode,
  PermissionState,
  fetchAgentNotes,
  fetchChats,
  fetchProviderModels,
  rejectAgentApproval,
  saveChats,
  saveAgentNotes,
  sendAgentMessage,
  updateApiConfig,
  updatePermission,
  updateSkill,
} from "./lib/api";
import { cn, formatCount } from "./lib/utils";

type BackendStartResult = {
  endpoint: string;
  started: boolean;
  already_running: boolean;
  mode: string;
  message: string;
};

type ConversationItem =
  | { id: string; type: "user"; text: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string }
  | { id: string; type: "error"; text: string }
  | { id: string; type: "compact"; text: string };

type ActiveView = "chat" | "skills" | "settings";

type ChatThread = {
  id: string;
  sessionId: string;
  title: string;
  projectPath: string;
  items: ConversationItem[];
};

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
  const [agentNotes, setAgentNotes] = useState("");
  const [agentNotesPath, setAgentNotesPath] = useState("");
  const [agentNotesLoaded, setAgentNotesLoaded] = useState(false);
  const [savingNotes, setSavingNotes] = useState(false);
  const [notesMessage, setNotesMessage] = useState("");
  const conversationEndRef = useRef<HTMLDivElement | null>(null);
  const projectInitRef = useRef(false);
  const chatsLoadedRef = useRef(false);

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

  const projectItems = useMemo(() => projects.slice(0, 6), [projects]);
  const activeChat = chats.find((chat) => chat.id === activeChatId) || null;
  const conversation = activeChat?.items ?? [];
  const sessionId = activeChat?.sessionId ?? "";
  const activeProjectName =
    projectItems.find((project) => projectKey(project) === activeProjectPath)?.name ||
    (activeProjectPath ? shortPath(activeProjectPath) : "");
  const temporaryChats = chats.filter((chat) => !chat.projectPath);
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

  async function startRuntime(): Promise<string | null> {
    setLoading(true);
    setError("");
    let targetEndpoint = endpoint;
    try {
      if (isTauriRuntime()) {
        await invoke("ensure_agent_notes_file");
        const result = await invoke<BackendStartResult>("start_backend");
        targetEndpoint = result.endpoint;
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
    if (!message || sending) {
      return;
    }
    setError("");
    if (message === "/compact" || message.startsWith("/compact ")) {
      void compactChat();
      setInput("");
      return;
    }
    setSending(true);
    const chatId = ensureActiveChat();
    const chatSessionId = activeChat?.sessionId || "";
    const history = activeChat && activeChat.items.length > 0 ? buildChatHistory(activeChat.items) : [];
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setInput("");
      const userItem: ConversationItem = { id: `user-${Date.now()}`, type: "user", text: message };
      updateChat(chatId, (chat) => ({
        ...chat,
        title: chat.title || (message.length > 24 ? `${message.slice(0, 24)}…` : message),
        items: [...chat.items, userItem],
      }));
      const response = await sendAgentMessage(targetEndpoint, message, chatSessionId || undefined, history);
      updateChat(chatId, (chat) => ({
        ...chat,
        sessionId: response.sessionId || response.session_id || chat.sessionId,
        items: [...chat.items, { id: response.turnId || response.turn_id, type: "agent", response }],
      }));
      await refresh(targetEndpoint);
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      appendToChat(chatId, { id: `error-${Date.now()}`, type: "error", text });
      setError(text);
    } finally {
      setSending(false);
    }
  }

  async function approveShell(approvalId: string) {
    setLoading(true);
    setError("");
    try {
      const payload = await approveAgentApproval(endpoint, approvalId);
      if (activeChatId) {
        appendToChat(activeChatId, {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          result: payload.execution?.result,
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
              onClick={() => newConversation()}
              className={cn(
                "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                activeView === "chat" && !activeChatId
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Plus className="h-4 w-4 shrink-0" />
              <span className="truncate">新对话</span>
            </button>
            <button
              onClick={() => newConversation("")}
              className="flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              <MessageSquare className="h-4 w-4 shrink-0" />
              <span className="truncate">临时对话</span>
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
          </nav>

          <SidebarSection title="项目">
            {projectItems.length > 0 ? (
              projectItems.map((project, index) => {
                const key = projectKey(project) || `project-${index}`;
                const projectChats = chats.filter((chat) => chat.projectPath === key);
                return (
                  <div key={key} className="min-w-0">
                    <SidebarProject
                      name={project.name || project.path || "Unity Project"}
                      meta={project.editorVersion || project.unityVersion || (project.sources ?? []).join("+")}
                      active={activeView === "chat" && key === activeProjectPath}
                      onClick={() => selectProject(key)}
                    />
                    {projectChats.map((chat) => (
                      <SidebarChat
                        key={chat.id}
                        title={chat.title || "新对话"}
                        active={activeView === "chat" && chat.id === activeChatId}
                        indent
                        onClick={() => openChat(chat)}
                      />
                    ))}
                  </div>
                );
              })
            ) : (
              <SidebarProject name={emptyProjectState?.name || "未发现 Unity 项目"} meta={emptyProjectState?.meta} active />
            )}
          </SidebarSection>

          <SidebarSection title="对话">
            {temporaryChats.length > 0 ? (
              temporaryChats.map((chat) => (
                <SidebarChat
                  key={chat.id}
                  title={chat.title || "新对话"}
                  active={activeView === "chat" && chat.id === activeChatId}
                  onClick={() => openChat(chat)}
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
                />
              </div>
            </div>
          ) : (
            <>
              <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
                <div className="mx-auto max-w-4xl space-y-5">
                  {conversation.map((item) => (
                    <ConversationCard key={item.id} item={item} />
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
                  />
                </div>
              </div>
            </>
          )}
        </section>
      </div>

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
}) {
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
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
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
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
          </div>
          <Button className="h-10 w-10 rounded-full px-0" disabled={sending || !input.trim()} type="submit">
            {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          </Button>
        </div>
      </div>
      <div className="flex h-12 min-w-0 items-center gap-2 px-5 text-sm text-muted-foreground">
        {projectLabel ? <Folder className="h-4 w-4 shrink-0" /> : <MessageSquare className="h-4 w-4 shrink-0" />}
        <span className="truncate">{projectLabel ? `在 ${projectLabel} 中工作` : "临时对话 · 不绑定项目"}</span>
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
          <div className="max-h-[calc(100vh-180px)] space-y-1 overflow-auto pr-1">
            {skills.map((skill) => (
              <button
                key={`${skill.source}-${skill.name}`}
                onClick={() => onSelect(skill)}
                className={cn(
                  "grid w-full min-w-0 gap-1 rounded-md px-3 py-2 text-left text-sm transition-colors",
                  selectedSkillName === skill.name ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
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

function ConversationCard({ item }: { item: ConversationItem }) {
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

  return (
    <div className="space-y-3">
      <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
        <div className="flex min-w-0 items-center gap-2">
          <Sparkles className="h-4 w-4 shrink-0 text-primary" />
          <div className="truncate text-sm font-semibold">方案</div>
          <Badge tone="muted" className="ml-auto shrink-0">
            {displayPlanner(response.plan.planner)}
          </Badge>
        </div>
        <div className="mt-4 grid gap-3">
          <DataLine label="摘要" value={response.plan.summary} />
          <DataLine label="下一步" value={displayStep(response.plan.nextStep || "-")} />
          {response.plan.skillTool ? <DataLine label="能力" value={response.plan.skillTool} mono /> : null}
          {response.plan.shellCommand ? <DataLine label="命令" value={response.plan.shellCommand} mono /> : null}
        </div>
      </section>

      {shell?.classification ? (
        <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-3 flex items-center gap-2">
            <TerminalSquare className="h-4 w-4 text-primary" />
            <div className="truncate text-sm font-semibold">命令</div>
            <Badge tone={riskTone(shell.classification.risk)} className="ml-auto shrink-0">
              {shell.classification.risk}
            </Badge>
          </div>
          <DataLine label="目录" value={shell.classification.cwd} />
          <div className="mt-3 overflow-hidden rounded-md border border-border bg-muted/50 p-3 font-mono text-xs">
            <pre className="whitespace-pre-wrap break-words">{shell.classification.command}</pre>
          </div>
          {shell.classification.reasons.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {shell.classification.reasons.map((reason) => (
                <Badge key={reason} tone="muted" className="max-w-full">
                  <span className="truncate">{reason}</span>
                </Badge>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {skill ? <SkillResultCard skill={skill} /> : null}
      {shell?.result ? <ShellResultCard title="执行结果" result={shell.result} /> : null}
      {awaitingApproval ? (
        <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>等待确认 — 请在下方输入框上方的审批区处理</span>
        </div>
      ) : null}
      {shell?.error ? <ShellResultCard title="执行错误" error={shell.error} /> : null}
    </div>
  );
}

function SkillResultCard({ skill }: { skill: AgentSkillResult }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 items-center gap-2">
        <Wrench className="h-4 w-4 shrink-0 text-primary" />
        <div className="truncate text-sm font-semibold">能力结果</div>
        <Badge tone={skillTone(skill)} className="ml-auto shrink-0">
          {displaySkillStatus(skill.status)}
        </Badge>
      </div>
      <div className="grid gap-3">
        <DataLine label="工具" value={skill.tool || "-"} mono />
        {skill.category ? <DataLine label="类别" value={skill.category} /> : null}
        {skill.error ? <DataLine label="错误" value={skill.error} /> : null}
        {skill.result !== undefined ? <OutputBlock label="数据" value={formatPayload(skill.result)} /> : null}
      </div>
    </section>
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

function SidebarSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mt-8 min-w-0">
      <div className="mb-3 px-2 text-xs font-medium text-muted-foreground">{title}</div>
      <div className="space-y-1">{children}</div>
    </section>
  );
}

function SidebarProject({
  name,
  meta,
  active = false,
  onClick,
}: {
  name: string;
  meta?: string;
  active?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={cn(
        "flex h-11 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
        active ? "bg-muted text-foreground" : "text-muted-foreground",
        onClick ? "hover:bg-muted hover:text-foreground" : "cursor-default",
      )}
    >
      <Folder className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {meta ? <span className="max-w-[78px] shrink-0 truncate text-xs text-muted-foreground">{meta}</span> : null}
    </button>
  );
}

function SidebarChat({
  title,
  active = false,
  indent = false,
  onClick,
}: {
  title: string;
  active?: boolean;
  indent?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex h-9 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
        indent ? "pl-9" : "",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {indent ? null : <MessageSquare className="h-4 w-4 shrink-0" />}
      <span className="truncate">{title}</span>
    </button>
  );
}

function projectKey(project: { path?: string; name?: string }): string {
  return project.path || project.name || "";
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

function shortPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).slice(-1)[0] || path;
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
