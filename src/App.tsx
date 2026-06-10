import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  Folder,
  History,
  Loader2,
  Moon,
  Plus,
  Send,
  Settings,
  Shield,
  Sparkles,
  Sun,
  TerminalSquare,
  Wrench,
  X,
} from "lucide-react";
import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
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
  approveAgentApproval,
  checkSkills,
  createSkill,
  deleteSkill,
  fetchBootstrap,
  fetchSkills,
  AgentSkillCheck,
  PermissionState,
  rejectAgentApproval,
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
  | { id: string; type: "error"; text: string };

type ActiveView = "chat" | "skills" | "settings";

const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";

const navItems = [
  { id: "new", label: "新对话", icon: Plus },
  { id: "skills", label: "能力库", icon: Wrench },
];

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
  const [sessionId, setSessionId] = useState("");
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [apiProvider, setApiProvider] = useState("gemini");
  const [apiKey, setApiKey] = useState("");
  const [apiBaseUrl, setApiBaseUrl] = useState("");
  const [apiModel, setApiModel] = useState("gemini-2.5-flash");
  const [savingApiConfig, setSavingApiConfig] = useState(false);
  const [skillRegistry, setSkillRegistry] = useState<AgentSkillRegistry | null>(null);
  const [skillCheck, setSkillCheck] = useState<AgentSkillCheck | null>(null);
  const [selectedSkillName, setSelectedSkillName] = useState("");
  const [skillDraft, setSkillDraft] = useState<Partial<AgentSkill>>(emptySkillDraft());
  const [savingSkill, setSavingSkill] = useState(false);

  const permission = bootstrap?.permission;
  const apiConfig = bootstrap?.apiConfig;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const pendingApprovals = bootstrap?.agentHealth.pendingApprovalCount ?? 0;
  const toolCount = bootstrap?.agentManifest.toolCount ?? 0;
  const skills = skillRegistry?.skills ?? bootstrap?.agentManifest.skills ?? [];
  const skillCount = skillRegistry?.count ?? skills.length;
  const projects = bootstrap?.health.projects?.projects ?? [];
  const vrcForgeToolsCount = getHealthDetailNumber(healthComponents.vrcForgeUnityTools?.detail, "vrcForgeToolsCount");
  const vrcForgeSkillsReady = runtimeConnected && healthComponents.vrcForgeUnityTools?.status === "ok" && vrcForgeToolsCount > 0;
  const agentModeLabel = !runtimeConnected
    ? "核心未连接"
    : vrcForgeSkillsReady
      ? `头像能力 ${vrcForgeToolsCount}`
      : "基础模式";
  const needsApiSetup = runtimeConnected && Boolean(apiConfig?.apiKeyRequired && !apiConfig.apiKeyPresent);

  const projectItems = useMemo(() => projects.slice(0, 6), [projects]);
  const activeProject = bootstrap?.health.projects?.selectedProjectPath || projectItems[0]?.path || projectItems[0]?.name || "Unity Projects";
  const projectPromptTitle = projectItems[0]?.name ? `需要在 ${projectItems[0].name} 里面构建什么？` : "";
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

  async function submitMessage(event?: FormEvent) {
    event?.preventDefault();
    const message = input.trim();
    if (!message || sending) {
      return;
    }
    setError("");
    setSending(true);
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
      setConversation((items) => [...items, userItem]);
      const response = await sendAgentMessage(targetEndpoint, message, sessionId || undefined);
      setSessionId(response.sessionId || response.session_id);
      setConversation((items) => [...items, { id: response.turnId || response.turn_id, type: "agent", response }]);
      await refresh(targetEndpoint);
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      setConversation((items) => [...items, { id: `error-${Date.now()}`, type: "error", text }]);
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
      setConversation((items) => [
        ...items,
        {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          result: payload.execution?.result,
          error: payload.execution?.error,
        },
      ]);
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
      setConversation((items) => [
        ...items,
        {
          id: `result-${approvalId}-${Date.now()}`,
          type: "result",
          approvalId,
          error: "rejected",
        },
      ]);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  function newConversation() {
    setActiveView("chat");
    setConversation([]);
    setSessionId("");
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
    if (!apiProvider || !apiModel || (providerNeedsApiKey(apiProvider) && !apiKey.trim())) {
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
      if (activeView === "settings") {
        setActiveView("chat");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSavingApiConfig(false);
    }
  }

  return (
    <main className="h-screen overflow-hidden bg-background text-foreground">
      <div className="grid h-screen grid-cols-[320px_minmax(0,1fr)]">
        <aside className="flex min-w-0 flex-col border-r border-border bg-sidebar px-4 py-4">
          <div className="flex h-10 items-center gap-3 px-2">
            <Bot className="h-5 w-5 shrink-0 text-primary" />
            <div className="truncate text-base font-semibold">VRCForge</div>
          </div>

          <nav className="mt-5 space-y-1">
            {navItems.map(({ id, label, icon: Icon }) => (
              <button
                key={label}
                onClick={id === "new" ? newConversation : () => void openSkills()}
                className={cn(
                  "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                  (activeView === "chat" && id === "new") || (activeView === "skills" && id === "skills")
                    ? "bg-muted text-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate">{label}</span>
              </button>
            ))}
          </nav>

          <SidebarSection title="项目">
            {projectItems.length > 0 ? (
              projectItems.map((project, index) => (
                <SidebarProject
                  key={`${project.path || project.name || index}`}
                  name={project.name || project.path || "Unity Project"}
                  meta={project.editorVersion || project.unityVersion || (project.sources ?? []).join("+")}
                  active={index === 0}
                />
              ))
            ) : (
              <SidebarProject name={emptyProjectState?.name || "未发现 Unity 项目"} meta={emptyProjectState?.meta} active />
            )}
          </SidebarSection>

          <SidebarSection title="对话">
            {conversation.length > 0 ? (
              <button className="flex h-9 w-full items-center gap-3 rounded-md px-3 text-sm text-foreground">
                <History className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="truncate">{conversation.find((item) => item.type === "user")?.type === "user" ? conversation.find((item) => item.type === "user")?.text : "当前会话"}</span>
              </button>
            ) : (
              <button className="flex h-9 w-full items-center gap-3 rounded-md px-3 text-sm text-muted-foreground">
                <History className="h-4 w-4 shrink-0" />
                <span className="truncate">空会话</span>
              </button>
            )}
          </SidebarSection>

          <div className="mt-auto">
            <button
              onClick={() => setActiveView("settings")}
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

        <section className="flex min-w-0 flex-col bg-workspace">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-6">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="truncate text-muted-foreground">{projectItems[0]?.name || shortPath(activeProject)}</span>
              <span className="text-muted-foreground">/</span>
              <span className="truncate font-medium">
                {activeView === "skills" ? "能力库" : activeView === "settings" ? "设置" : sessionId ? "当前会话" : "新任务"}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {permission?.roslynFullAuto ? (
                <Badge tone="danger">
                  <AlertTriangle className="mr-1 h-3.5 w-3.5 shrink-0" />
                  高级自动
                </Badge>
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
            <div className="flex min-h-0 flex-1 items-center justify-center p-8">
              <div className="w-full max-w-4xl">
                <ProviderSetup
                  provider={apiProvider}
                  apiKey={apiKey}
                  baseUrl={apiBaseUrl}
                  model={apiModel}
                  saving={savingApiConfig}
                  onProviderChange={(provider) => {
                    setApiProvider(provider);
                    setApiModel(defaultModelForProvider(provider));
                    setApiBaseUrl(defaultBaseUrlForProvider(provider));
                  }}
                  onApiKeyChange={setApiKey}
                  onBaseUrlChange={setApiBaseUrl}
                  onModelChange={setApiModel}
                  onSubmit={saveApiProvider}
                />
              </div>
            </div>
          ) : needsApiSetup ? (
            <div className="flex min-h-0 flex-1 items-center justify-center p-8">
              <div className="w-full max-w-4xl">
                <ProviderSetup
                  provider={apiProvider}
                  apiKey={apiKey}
                  baseUrl={apiBaseUrl}
                  model={apiModel}
                  saving={savingApiConfig}
                  onProviderChange={(provider) => {
                    setApiProvider(provider);
                    setApiModel(defaultModelForProvider(provider));
                    setApiBaseUrl(defaultBaseUrlForProvider(provider));
                  }}
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
                  projectLabel={projectItems[0]?.name || ""}
                  onSubmit={submitMessage}
                  onSwitchMode={switchMode}
                />
              </div>
            </div>
          ) : (
            <>
              <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
                <div className="mx-auto max-w-4xl space-y-5">
                  {conversation.map((item) => (
                    <ConversationCard
                      key={item.id}
                      item={item}
                      loading={loading}
                      onApprove={approveShell}
                      onReject={rejectShell}
                    />
                  ))}
                </div>
              </div>
              <div className="shrink-0 border-t border-border bg-workspace/95 px-6 py-4">
                <div className="mx-auto max-w-4xl">
                  <Composer
                    input={input}
                    setInput={setInput}
                    sending={sending}
                    permission={permission}
                    statusLabel={agentModeLabel}
                    projectLabel={projectItems[0]?.name || ""}
                    onSubmit={submitMessage}
                    onSwitchMode={switchMode}
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
              <h2 className="truncate text-lg font-semibold">Roslyn 高级自动</h2>
            </div>
            <div className="mt-5 grid gap-3 text-sm">
              <DataLine label="风险确认" value={permission?.roslynRiskAcknowledged ? "已确认" : "未确认"} />
              <DataLine label="模式" value="高级自动" />
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setShowRoslynWarning(false)}>
                取消
              </Button>
              <Button variant="danger" onClick={confirmRoslynWarning} disabled={loading}>
                确认
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
  compact?: boolean;
}) {
  return (
    <form onSubmit={onSubmit} className="overflow-hidden rounded-3xl bg-muted/70 shadow-composer">
      <div className={cn("rounded-3xl border border-border bg-card", compact ? "p-3" : "p-4")}>
        <textarea
          value={input}
          onChange={(event) => setInput(event.target.value)}
          className="min-h-[76px] w-full resize-none bg-transparent px-1 text-base outline-none placeholder:text-muted-foreground"
          placeholder="尽管问"
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
              onSubmit();
            }
          }}
        />
        <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <button
              type="button"
              className="flex h-8 min-w-0 max-w-full items-center gap-2 rounded-md px-2 text-sm text-amber-700 transition-colors hover:bg-amber-500/10"
              onClick={() => onSwitchMode(permission?.roslynFullAuto ? "approval" : "roslyn_full_auto")}
            >
              <Shield className="h-4 w-4 shrink-0" />
              <span className="truncate">{permission?.roslynFullAuto ? "高级自动" : "逐项确认"}</span>
              <ChevronDown className="h-3.5 w-3.5 shrink-0" />
            </button>
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
        <Folder className="h-4 w-4 shrink-0" />
        <span className="truncate">{projectLabel ? `进入 ${projectLabel} 工作` : "进入项目工作"}</span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0" />
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
  onProviderChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
  onBaseUrlChange: (value: string) => void;
  onModelChange: (value: string) => void;
  onSubmit: (event?: FormEvent) => void;
}) {
  const requiresBaseUrl = provider === "openai" || provider === "openai-compatible" || provider === "ollama" || provider === "vertexai";

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
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              autoComplete="off"
            />
          ) : (
            <input
              value=""
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
          <input
            value={model}
            onChange={(event) => onModelChange(event.target.value)}
            className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
          />
        </FieldLabel>
      </div>
      <div className="mt-5 flex justify-end">
        <Button disabled={saving || (providerNeedsApiKey(provider) && !apiKey.trim()) || !model.trim()} type="submit">
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

function ConversationCard({
  item,
  loading,
  onApprove,
  onReject,
}: {
  item: ConversationItem;
  loading: boolean;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
}) {
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

  const response = item.response;
  const shell = response.shell;
  const skill = response.skill;
  const approval = shell?.approval;

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
      {approval ? <ApprovalCard approval={approval} loading={loading} onApprove={onApprove} onReject={onReject} /> : null}
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

function SidebarProject({ name, meta, active = false }: { name: string; meta?: string; active?: boolean }) {
  return (
    <button
      className={cn(
        "flex h-11 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
        active ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <Folder className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {meta ? <span className="max-w-[78px] shrink-0 truncate text-xs text-muted-foreground">{meta}</span> : null}
    </button>
  );
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
