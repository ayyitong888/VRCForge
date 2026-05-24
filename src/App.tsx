import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  Clock3,
  Folder,
  History,
  Loader2,
  Moon,
  Plus,
  Search,
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
  AgentShellResult,
  ApiError,
  AppBootstrap,
  approveAgentApproval,
  fetchBootstrap,
  PermissionState,
  rejectAgentApproval,
  sendAgentMessage,
  updatePermission,
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

const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";

const navItems = [
  { label: "新对话", icon: Plus },
  { label: "搜索", icon: Search },
  { label: "Skills", icon: Wrench },
  { label: "自动化", icon: Clock3 },
  { label: "审批", icon: Shield },
  { label: "日志", icon: TerminalSquare },
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

  const permission = bootstrap?.permission;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const pendingApprovals = bootstrap?.agentHealth.pendingApprovalCount ?? 0;
  const toolCount = bootstrap?.agentManifest.toolCount ?? 0;
  const projects = bootstrap?.health.projects?.projects ?? [];
  const activeProject = bootstrap?.health.projects?.selectedProjectPath || bootstrap?.health.projectRoot || "VRCForge";

  const projectItems = useMemo(() => projects.slice(0, 6), [projects]);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  useEffect(() => {
    void startRuntime();
  }, []);

  async function startRuntime() {
    setLoading(true);
    setError("");
    try {
      if (isTauriRuntime()) {
        await invoke("ensure_agent_notes_file");
        const result = await invoke<BackendStartResult>("start_backend");
        setEndpoint(result.endpoint);
        setBackendMessage(result.message);
        await refresh(result.endpoint);
      } else {
        setBackendMessage("dev");
        await refresh(endpoint);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function refresh(target = endpoint) {
    setError("");
    const payload = await fetchBootstrap(target);
    setBootstrap(payload);
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
    setInput("");
    setError("");
    setSending(true);
    const userItem: ConversationItem = { id: `user-${Date.now()}`, type: "user", text: message };
    setConversation((items) => [...items, userItem]);
    try {
      const response = await sendAgentMessage(endpoint, message, sessionId || undefined);
      setSessionId(response.sessionId || response.session_id);
      setConversation((items) => [...items, { id: response.turnId || response.turn_id, type: "agent", response }]);
      await refresh();
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
    setConversation([]);
    setSessionId("");
    setError("");
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
            {navItems.map(({ label, icon: Icon }, index) => (
              <button
                key={label}
                onClick={index === 0 ? newConversation : undefined}
                className={cn(
                  "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                  index === 0 ? "bg-muted text-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground",
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
                  meta={project.unityVersion || (project.sources ?? []).join("+")}
                  active={index === 0}
                />
              ))
            ) : (
              <SidebarProject name={shortPath(activeProject)} meta="local" active />
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
            <button className="flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground">
              <Settings className="h-4 w-4 shrink-0" />
              <span className="truncate">设置</span>
            </button>
          </div>
        </aside>

        <section className="flex min-w-0 flex-col bg-workspace">
          <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-6">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="truncate text-muted-foreground">{shortPath(activeProject)}</span>
              <span className="text-muted-foreground">/</span>
              <span className="truncate font-medium">{sessionId ? "Agent Session" : "New Task"}</span>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <StatusChip ok={runtimeConnected} label={runtimeConnected ? "runtime ready" : "runtime offline"} />
              <Badge tone={pendingApprovals > 0 ? "warn" : "muted"}>{formatCount(pendingApprovals)} approvals</Badge>
              <Button variant="ghost" className="h-9 w-9 px-0" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>
          </header>

          {error ? (
            <div className="mx-auto mt-3 w-full max-w-4xl px-4">
              <div className="rounded-md border border-destructive/15 bg-destructive/5 px-3 py-2 text-xs text-destructive/75">
                <span className="break-words">{error}</span>
              </div>
            </div>
          ) : null}

          {conversation.length === 0 ? (
            <div className="flex min-h-0 flex-1 items-center justify-center p-8">
              <div className="w-full max-w-4xl">
                <h1 className="mb-8 text-center text-3xl font-semibold tracking-normal">我们应该在 VRCForge 中构建什么？</h1>
                <Composer
                  input={input}
                  setInput={setInput}
                  sending={sending}
                  permission={permission}
                  backendMessage={backendMessage}
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
                    backendMessage={backendMessage}
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
              <h2 className="truncate text-lg font-semibold">Roslyn full-auto</h2>
            </div>
            <div className="mt-5 grid gap-3 text-sm">
              <DataLine label="risk_acknowledged" value={permission?.roslynRiskAcknowledged ? "true" : "false"} />
              <DataLine label="mode" value="roslyn_full_auto" />
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
  backendMessage,
  onSubmit,
  onSwitchMode,
  compact = false,
}: {
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  permission?: PermissionState;
  backendMessage: string;
  onSubmit: (event?: FormEvent) => void;
  onSwitchMode: (mode: PermissionState["executionMode"]) => void;
  compact?: boolean;
}) {
  return (
    <form onSubmit={onSubmit} className={cn("rounded-2xl border border-border bg-card shadow-composer", compact ? "p-3" : "p-4")}>
      <textarea
        value={input}
        onChange={(event) => setInput(event.target.value)}
        className="min-h-[86px] w-full resize-none bg-transparent px-1 text-base outline-none placeholder:text-muted-foreground"
        placeholder="尽管问"
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            onSubmit();
          }
        }}
      />
      <div className="mt-3 flex min-w-0 items-center justify-between gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge tone={permission?.roslynFullAuto ? "danger" : "warn"} className="max-w-full">
            <Shield className="mr-1 h-3.5 w-3.5 shrink-0" />
            <button
              type="button"
              className="truncate"
              onClick={() => onSwitchMode(permission?.roslynFullAuto ? "approval" : "roslyn_full_auto")}
            >
              {permission?.roslynFullAuto ? "Roslyn full-auto" : "Ask before changes"}
            </button>
            <ChevronDown className="ml-1 h-3.5 w-3.5 shrink-0" />
          </Badge>
          <Badge tone="muted" className="max-w-[220px] truncate">
            {backendMessage}
          </Badge>
        </div>
        <Button className="h-10 w-10 rounded-full px-0" disabled={sending || !input.trim()} type="submit">
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>
    </form>
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
    return <ShellResultCard title={item.error === "rejected" ? "Rejected" : "Shell result"} result={item.result} error={item.error} />;
  }

  const response = item.response;
  const shell = response.shell;
  const approval = shell?.approval;

  return (
    <div className="space-y-3">
      <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
        <div className="flex min-w-0 items-center gap-2">
          <Sparkles className="h-4 w-4 shrink-0 text-primary" />
          <div className="truncate text-sm font-semibold">Plan</div>
          <Badge tone="muted" className="ml-auto shrink-0">
            {response.plan.planner}
          </Badge>
        </div>
        <div className="mt-4 grid gap-3">
          <DataLine label="summary" value={response.plan.summary} />
          <DataLine label="next" value={response.plan.nextStep || "-"} />
          {response.plan.shellCommand ? <DataLine label="command" value={response.plan.shellCommand} mono /> : null}
        </div>
      </section>

      {shell?.classification ? (
        <section className="rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-3 flex items-center gap-2">
            <TerminalSquare className="h-4 w-4 text-primary" />
            <div className="truncate text-sm font-semibold">Shell</div>
            <Badge tone={riskTone(shell.classification.risk)} className="ml-auto shrink-0">
              {shell.classification.risk}
            </Badge>
          </div>
          <DataLine label="cwd" value={shell.classification.cwd} />
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

      {shell?.result ? <ShellResultCard title="Shell result" result={shell.result} /> : null}
      {approval ? <ApprovalCard approval={approval} loading={loading} onApprove={onApprove} onReject={onReject} /> : null}
      {shell?.error ? <ShellResultCard title="Shell error" error={shell.error} /> : null}
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
        <div className="truncate text-sm font-semibold">Approval</div>
        <Badge tone="warn" className="ml-auto shrink-0">
          {approval.riskLevel || "high"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-3">
        <DataLine label="command" value={approval.preview?.command || "-"} mono />
        <DataLine label="cwd" value={approval.preview?.cwd || "-"} />
        <DataLine label="reason" value={approval.reason || "-"} />
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" disabled={loading} onClick={() => onReject(approval.id)}>
          <X className="h-4 w-4" />
          Reject
        </Button>
        <Button variant="primary" disabled={loading} onClick={() => onApprove(approval.id)}>
          <Check className="h-4 w-4" />
          Approve
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
            exit {result.exitCode}
          </Badge>
        ) : null}
      </div>
      {error ? <DataLine label="error" value={error} /> : null}
      {result ? (
        <div className="grid gap-3">
          <DataLine label="duration" value={`${result.durationSeconds}s`} />
          <OutputBlock label="stdout" value={result.stdout} />
          {result.stderr ? <OutputBlock label="stderr" value={result.stderr} danger /> : null}
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

function riskTone(risk: string): "ok" | "warn" | "danger" | "muted" {
  if (risk === "low") return "ok";
  if (risk === "high") return "warn";
  if (risk === "reject") return "danger";
  return "muted";
}

function shortPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).slice(-1)[0] || path;
}
