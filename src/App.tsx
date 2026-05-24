import { invoke } from "@tauri-apps/api/core";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDot,
  ClipboardCheck,
  Code2,
  Moon,
  Play,
  RefreshCw,
  ShieldCheck,
  Sun,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "./components/ui/badge";
import { Button } from "./components/ui/button";
import { ApiError, AppBootstrap, fetchBootstrap, PermissionState, updatePermission } from "./lib/api";
import { cn, formatCount } from "./lib/utils";

type BackendStartResult = {
  endpoint: string;
  started: boolean;
  already_running: boolean;
  mode: string;
  message: string;
};

type MetricTone = "default" | "success" | "warning" | "error" | "info";

const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";
const navItems = [
  { label: "工作台", icon: Bot },
  { label: "Skills", icon: Wrench },
  { label: "审批", icon: ClipboardCheck },
  { label: "诊断", icon: Activity },
  { label: "Roslyn", icon: Code2 },
];

function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

export default function App() {
  const [endpoint, setEndpoint] = useState(FALLBACK_ENDPOINT);
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(null);
  const [backendMessage, setBackendMessage] = useState("等待启动");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [showRoslynWarning, setShowRoslynWarning] = useState(false);
  const [pendingMode, setPendingMode] = useState<PermissionState["executionMode"] | null>(null);

  const permission = bootstrap?.permission;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;
  const runtimeConnected = Boolean(bootstrap?.ok);
  const pendingApprovals = bootstrap?.agentHealth.pendingApprovalCount ?? 0;
  const toolCount = bootstrap?.agentManifest.toolCount ?? 0;

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
        const result = await invoke<BackendStartResult>("start_backend");
        setEndpoint(result.endpoint);
        setBackendMessage(result.message);
        await refresh(result.endpoint);
      } else {
        setBackendMessage("开发预览");
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

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="grid min-h-screen grid-cols-[240px_minmax(0,1fr)]">
        <aside className="border-r border-border bg-card px-4 py-5">
          <div className="flex h-10 items-center gap-3 px-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Bot className="h-4 w-4" />
            </div>
            <div className="truncate text-base font-semibold">VRCForge</div>
          </div>

          <nav className="mt-8 space-y-1">
            {navItems.map(({ label, icon: Icon }, index) => (
              <button
                key={label}
                className={cn(
                  "flex h-10 w-full min-w-0 items-center gap-3 rounded-md px-3 text-left text-sm transition-colors",
                  index === 0 ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate">{label}</span>
              </button>
            ))}
          </nav>
        </aside>

        <section className="flex min-w-0 flex-col">
          <header className="flex h-16 items-center justify-between border-b border-border bg-background px-8">
            <div className="min-w-0">
              <h1 className="truncate text-xl font-semibold">工作台</h1>
              <div className="truncate text-xs text-muted-foreground">{backendMessage}</div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {permission?.roslynFullAuto ? <Badge tone="danger">Roslyn 全自动</Badge> : <Badge tone="ok">逐条审批</Badge>}
              <Button variant="outline" className="h-9 w-9 px-0" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>
          </header>

          <div className="flex-1 overflow-auto p-8">
            <div className="mx-auto grid max-w-6xl grid-cols-[minmax(0,1fr)_340px] gap-8">
              <div className="min-w-0 space-y-8">
                {error ? (
                  <section className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive/80">
                    <span className="break-words">{error}</span>
                  </section>
                ) : null}

                <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                  <div className="flex min-w-0 items-center justify-between gap-4">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">Runtime</div>
                      <div className="mt-1 flex min-w-0 items-center gap-2 text-sm">
                        <CircleDot className={cn("h-4 w-4 shrink-0", runtimeConnected ? "text-emerald-600" : "text-amber-600")} />
                        <span className="truncate">{runtimeConnected ? "connected" : "starting"}</span>
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button variant="outline" className="h-9 w-9 px-0" onClick={() => refresh()} disabled={loading}>
                        <RefreshCw className="h-4 w-4" />
                      </Button>
                      <Button variant="primary" className="h-9 px-3" onClick={startRuntime} disabled={loading}>
                        <Play className="h-4 w-4" />
                        <span className="whitespace-nowrap">启动</span>
                      </Button>
                    </div>
                  </div>
                  <div className="mt-6 grid grid-cols-2 gap-4">
                    <Value label="Endpoint" value={endpoint} />
                    <Value label="Gateway" value={bootstrap?.agentHealth.enabled ? "enabled" : "disabled"} />
                    <Value label="Version" value={bootstrap?.health.version ?? "-"} />
                    <Value label="Mode" value={permission?.executionMode ?? "-"} />
                  </div>
                </section>

                <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">Execution</div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        <Button
                          variant={permission?.executionMode === "approval" ? "primary" : "outline"}
                          onClick={() => switchMode("approval")}
                          disabled={loading}
                        >
                          <ShieldCheck className="h-4 w-4" />
                          <span className="whitespace-nowrap">逐条审批</span>
                        </Button>
                        <Button
                          variant={permission?.executionMode === "roslyn_full_auto" ? "danger" : "outline"}
                          onClick={() => switchMode("roslyn_full_auto")}
                          disabled={loading}
                        >
                          <Code2 className="h-4 w-4" />
                          <span className="whitespace-nowrap">Roslyn 全自动</span>
                        </Button>
                      </div>
                    </div>
                    {permission?.roslynFullAuto ? (
                      <Badge tone="danger" className="shrink-0">
                        active
                      </Badge>
                    ) : (
                      <Badge tone="ok" className="shrink-0">
                        guarded
                      </Badge>
                    )}
                  </div>
                </section>

                <section className="grid grid-cols-2 gap-8">
                  <DataCard title="Skills" value={toolCount} tone="info" />
                  <DataCard title="待审批" value={pendingApprovals} tone={pendingApprovals > 0 ? "warning" : "default"} />
                </section>

                <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                  <div className="mb-5 flex items-center gap-2 text-sm font-semibold">
                    <Activity className="h-4 w-4" />
                    运行诊断
                  </div>
                  <div className="grid grid-cols-4 gap-4">
                    <Metric label="errors" value={healthErrors} tone={healthErrors > 0 ? "error" : "default"} />
                    <Metric label="warnings" value={healthWarnings} tone={healthWarnings > 0 ? "warning" : "default"} />
                    <Metric label="approvals" value={pendingApprovals} tone={pendingApprovals > 0 ? "warning" : "default"} />
                    <Metric label="skills" value={toolCount} tone="info" />
                  </div>
                </section>
              </div>

              <aside className="min-w-0 space-y-8">
                <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                  <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
                    <ClipboardCheck className="h-4 w-4" />
                    待审批队列
                  </div>
                  <div className="rounded-md border border-border bg-background px-3 py-3 text-sm">
                    <span className="block truncate">{pendingApprovals > 0 ? `${pendingApprovals} pending` : "empty"}</span>
                  </div>
                </section>

                <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                  <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
                    <TerminalSquare className="h-4 w-4" />
                    桌面闭环
                  </div>
                  <div className="space-y-3">
                    <LoopItem label="Runtime" ok={runtimeConnected} />
                    <LoopItem label="Manifest" ok={toolCount > 0} />
                    <LoopItem label="Permission" ok={Boolean(permission)} />
                  </div>
                </section>

                <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                  <div className="mb-4 text-sm font-semibold">Components</div>
                  <div className="space-y-2">
                    {Object.entries(healthComponents)
                      .slice(0, 7)
                      .map(([name, component]) => (
                        <div key={name} className="flex min-w-0 items-center justify-between gap-3 rounded-sm bg-muted/60 px-2 py-2">
                          <span className="min-w-0 truncate text-xs font-medium">{name}</span>
                          <Badge tone={statusBadgeTone(component.status)} className="shrink-0">
                            {component.status}
                          </Badge>
                        </div>
                      ))}
                  </div>
                </section>
              </aside>
            </div>
          </div>
        </section>
      </div>

      {showRoslynWarning ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
          <section className="w-full max-w-lg rounded-md border border-destructive/40 bg-card p-6 shadow-panel">
            <div className="flex items-center gap-3 text-destructive">
              <AlertTriangle className="h-5 w-5 shrink-0" />
              <h2 className="truncate text-lg font-semibold">Roslyn 全自动</h2>
            </div>
            <div className="mt-5 grid gap-3 text-sm text-muted-foreground">
              <Value label="risk_acknowledged" value={permission?.roslynRiskAcknowledged ? "true" : "false"} />
              <Value label="mode" value="roslyn_full_auto" />
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

function Value({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-background px-3 py-2">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 break-words text-sm font-medium">{value}</div>
    </div>
  );
}

function DataCard({ title, value, tone }: { title: string; value: number; tone: MetricTone }) {
  return (
    <section className="rounded-md border border-border bg-card p-5 shadow-panel">
      <div className="truncate text-sm font-semibold">{title}</div>
      <div className={cn("mt-5 text-4xl font-semibold", metricToneClass(tone))}>{formatCount(value)}</div>
    </section>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: MetricTone }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-background p-3">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-2 truncate text-2xl font-semibold", metricToneClass(tone))}>{formatCount(value)}</div>
    </div>
  );
}

function LoopItem({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 rounded-sm bg-muted/60 px-2 py-2 text-sm">
      <span className="truncate">{label}</span>
      {ok ? <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" /> : <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />}
    </div>
  );
}

function metricToneClass(tone: MetricTone) {
  if (tone === "success") return "text-emerald-600";
  if (tone === "warning") return "text-amber-600";
  if (tone === "error") return "text-destructive";
  if (tone === "info") return "text-blue-600";
  return "text-foreground";
}

function statusBadgeTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "ok") return "ok";
  if (status === "warning") return "warn";
  if (status === "error") return "danger";
  return "muted";
}
