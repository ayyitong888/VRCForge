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
  Sparkles,
  Sun,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
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

const FALLBACK_ENDPOINT = "http://127.0.0.1:8757";
const navItems = [
  { label: "工作台", icon: Bot },
  { label: "技能", icon: Wrench },
  { label: "审批", icon: ClipboardCheck },
  { label: "运行诊断", icon: Activity },
  { label: "Roslyn", icon: Code2 },
];

function isTauriRuntime() {
  return "__TAURI_INTERNALS__" in window;
}

function groupedSkills(bootstrap: AppBootstrap | null) {
  const groups = new Map<string, AppBootstrap["agentManifest"]["tools"]>();
  for (const tool of bootstrap?.agentManifest.tools ?? []) {
    const items = groups.get(tool.category) ?? [];
    items.push(tool);
    groups.set(tool.category, items);
  }
  return Array.from(groups.entries()).map(([category, tools]) => ({ category, tools }));
}

export default function App() {
  const [endpoint, setEndpoint] = useState(FALLBACK_ENDPOINT);
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(null);
  const [backendMessage, setBackendMessage] = useState("等待启动本地 runtime");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [showRoslynWarning, setShowRoslynWarning] = useState(false);
  const [pendingMode, setPendingMode] = useState<PermissionState["executionMode"] | null>(null);

  const skillsByCategory = useMemo(() => groupedSkills(bootstrap), [bootstrap]);
  const permission = bootstrap?.permission;
  const healthComponents = bootstrap?.health.components ?? {};
  const healthErrors = Object.values(healthComponents).filter((item) => item.status === "error").length;
  const healthWarnings = Object.values(healthComponents).filter((item) => item.status === "warning").length;

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
        setBackendMessage("开发预览模式：请用 Tauri 打开正式桌面壳");
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
      <div className="grid min-h-screen grid-cols-[280px_1fr]">
        <aside className="border-r border-border bg-card px-5 py-5">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-md bg-primary text-primary-foreground">
              <Sparkles className="h-5 w-5" />
            </div>
            <div>
              <div className="text-lg font-semibold">VRCForge</div>
              <div className="text-xs text-muted-foreground">Agentic Avatar Studio</div>
            </div>
          </div>

          <nav className="mt-8 space-y-2">
            {navItems.map(({ label, icon: Icon }) => (
              <button
                key={label}
                className="flex h-10 w-full items-center gap-3 rounded-md px-3 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            ))}
          </nav>

          <div className="mt-8 rounded-md border border-border bg-background p-3">
            <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Runtime</div>
            <div className="mt-2 flex items-center gap-2 text-sm">
              <CircleDot className={cn("h-4 w-4", bootstrap?.ok ? "text-emerald-500" : "text-amber-500")} />
              <span>{bootstrap?.ok ? "已连接" : "未连接"}</span>
            </div>
            <div className="mt-2 break-all text-xs text-muted-foreground">{endpoint}</div>
            <div className="mt-3 flex gap-2">
              <Button variant="outline" className="h-8 px-2" onClick={() => refresh()} disabled={loading}>
                <RefreshCw className="h-4 w-4" />
              </Button>
              <Button variant="outline" className="h-8 px-2" onClick={startRuntime} disabled={loading}>
                <Play className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </aside>

        <section className="flex min-w-0 flex-col">
          <header className="flex h-16 items-center justify-between border-b border-border px-6">
            <div>
              <h1 className="text-xl font-semibold">桌面 Agent 工作台</h1>
              <p className="text-sm text-muted-foreground">{backendMessage}</p>
            </div>
            <div className="flex items-center gap-3">
              {permission?.roslynFullAuto ? (
                <Badge tone="danger">Roslyn 全自动</Badge>
              ) : (
                <Badge tone="ok">逐条审批</Badge>
              )}
              <Button variant="outline" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </Button>
            </div>
          </header>

          <div className="grid flex-1 grid-cols-[1fr_360px] gap-5 overflow-auto p-6">
            <div className="space-y-5">
              {error ? (
                <section className="rounded-md border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
                  {error}
                </section>
              ) : null}

              {permission?.roslynFullAuto ? (
                <section className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
                  <div className="flex items-center gap-2 font-semibold text-destructive">
                    <AlertTriangle className="h-4 w-4" />
                    高级全自动模式已开启
                  </div>
                  <p className="mt-2 text-sm text-destructive/90">
                    Agent 可发起 Roslyn 修复链路。每次动作仍会写入审计日志，建议只在需要完整自动调试闭环时开启。
                  </p>
                </section>
              ) : null}

              <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 text-sm font-medium text-primary">
                      <Bot className="h-4 w-4" />
                      Agent Runtime
                    </div>
                    <h2 className="mt-2 text-2xl font-semibold">观察、规划、预览、审批、执行</h2>
                    <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
                      所有能力以技能形式通过本地 Agent Gateway 暴露。桌面 App 负责展示状态、权限和审批，写入操作继续走备份与受监督流程。
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant={permission?.executionMode === "approval" ? "primary" : "outline"}
                      onClick={() => switchMode("approval")}
                      disabled={loading}
                    >
                      <ShieldCheck className="h-4 w-4" />
                      逐条审批
                    </Button>
                    <Button
                      variant={permission?.executionMode === "roslyn_full_auto" ? "danger" : "outline"}
                      onClick={() => switchMode("roslyn_full_auto")}
                      disabled={loading}
                    >
                      <Code2 className="h-4 w-4" />
                      Roslyn 全自动
                    </Button>
                  </div>
                </div>
              </section>

              <section className="rounded-md border border-border bg-card p-5 shadow-panel">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-lg font-semibold">Skills</h2>
                    <p className="text-sm text-muted-foreground">
                      当前可调用 {formatCount(bootstrap?.agentManifest.toolCount)} 个技能，写入目标{" "}
                      {formatCount(bootstrap?.agentManifest.writeTargets.length)} 个。
                    </p>
                  </div>
                  <Badge tone={bootstrap?.agentHealth.enabled ? "ok" : "warn"}>
                    Gateway {bootstrap?.agentHealth.enabled ? "enabled" : "disabled"}
                  </Badge>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-3">
                  {skillsByCategory.map(({ category, tools }) => (
                    <div key={category} className="rounded-md border border-border bg-background p-4">
                      <div className="mb-3 flex items-center justify-between">
                        <div className="text-sm font-semibold">{category}</div>
                        <Badge tone="muted">{tools.length}</Badge>
                      </div>
                      <div className="space-y-2">
                        {tools.slice(0, 6).map((tool) => (
                          <div key={tool.name} className="flex min-h-9 items-center justify-between gap-2 rounded-sm bg-muted/60 px-2 py-1.5">
                            <span className="truncate text-xs font-medium">{tool.name}</span>
                            {tool.advanced ? <Badge tone="danger">Advanced</Badge> : tool.write ? <Badge tone="warn">Write</Badge> : null}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            </div>

            <aside className="space-y-5">
              <section className="rounded-md border border-border bg-card p-4 shadow-panel">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <Activity className="h-4 w-4" />
                  运行诊断
                </div>
                <div className="mt-4 grid grid-cols-2 gap-3">
                  <Metric label="错误" value={healthErrors} tone={healthErrors ? "danger" : "ok"} />
                  <Metric label="警告" value={healthWarnings} tone={healthWarnings ? "warn" : "ok"} />
                  <Metric label="审批" value={bootstrap?.agentHealth.pendingApprovalCount ?? 0} tone="default" />
                  <Metric label="技能" value={bootstrap?.agentManifest.toolCount ?? 0} tone="default" />
                </div>
                <div className="mt-4 space-y-2">
                  {Object.entries(healthComponents)
                    .slice(0, 8)
                    .map(([name, component]) => (
                      <div key={name} className="flex items-start justify-between gap-3 rounded-sm bg-muted/60 px-2 py-2">
                        <div className="min-w-0">
                          <div className="truncate text-xs font-medium">{name}</div>
                          <div className="truncate text-xs text-muted-foreground">{component.message}</div>
                        </div>
                        <Badge tone={component.status === "ok" ? "ok" : component.status === "warning" ? "warn" : "danger"}>
                          {component.status}
                        </Badge>
                      </div>
                    ))}
                </div>
              </section>

              <section className="rounded-md border border-border bg-card p-4 shadow-panel">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <ClipboardCheck className="h-4 w-4" />
                  待审批队列
                </div>
                <div className="mt-4 rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted-foreground">
                  {bootstrap?.approvals.length ? `${bootstrap.approvals.length} 个待处理请求` : "暂无待审批写入请求"}
                </div>
              </section>

              <section className="rounded-md border border-border bg-card p-4 shadow-panel">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <TerminalSquare className="h-4 w-4" />
                  桌面闭环
                </div>
                <ul className="mt-3 space-y-2 text-sm text-muted-foreground">
                  <li className="flex gap-2">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-500" />
                    本地启动 FastAPI runtime
                  </li>
                  <li className="flex gap-2">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-500" />
                    读取 Agent Gateway 技能清单
                  </li>
                  <li className="flex gap-2">
                    <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-500" />
                    权限模式由桌面 App 管理
                  </li>
                </ul>
              </section>
            </aside>
          </div>
        </section>
      </div>

      {showRoslynWarning ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-6">
          <section className="w-full max-w-lg rounded-md border border-destructive/50 bg-card p-6 shadow-panel">
            <div className="flex items-center gap-3 text-destructive">
              <AlertTriangle className="h-6 w-6" />
              <h2 className="text-lg font-semibold">首次开启 Roslyn 全自动</h2>
            </div>
            <p className="mt-4 text-sm leading-6 text-muted-foreground">
              该模式允许 Agent 通过 Roslyn 高级能力修复和调试 Unity 侧问题，风险确认位会永久记录为 true，
              之后切换模式不会重置。请只在你明确需要完整自动调试闭环时开启。
            </p>
            <div className="mt-6 flex justify-end gap-3">
              <Button variant="outline" onClick={() => setShowRoslynWarning(false)}>
                取消
              </Button>
              <Button variant="danger" onClick={confirmRoslynWarning} disabled={loading}>
                我已知晓风险并开启
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone: "default" | "ok" | "warn" | "danger" }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold", tone === "danger" && "text-destructive", tone === "warn" && "text-amber-600", tone === "ok" && "text-emerald-600")}>
        {formatCount(value)}
      </div>
    </div>
  );
}
