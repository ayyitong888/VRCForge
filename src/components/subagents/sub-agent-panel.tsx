import { Bot, Check, ChevronDown, ChevronRight, Eye, Loader2, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "../../lib/subagent-ui";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

export function SubAgentPanel({
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
  const statusLabel = error ? t("subagent.statusNeedsAction") : running ? `${running} running` : completed ? `${completed} completed` : t("subagent.statusReady");
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
                      {displaySubAgentStatus(task.status)}
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
                  {displaySubAgentStatus(selected.status)}
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
