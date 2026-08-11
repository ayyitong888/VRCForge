import { ListTodo } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentProgress } from "../../lib/api";
import { cn } from "../../lib/utils";

function todoTone(status?: string): string {
  const normalized = (status || "pending").toLowerCase();
  if (normalized === "completed") return "border-emerald-500 bg-emerald-500";
  if (normalized === "blocked" || normalized === "cancelled") return "border-destructive bg-destructive";
  if (normalized === "in_progress" || normalized === "running") return "border-primary bg-primary";
  return "border-muted-foreground/60 bg-transparent";
}

function todoStatusLabel(status: string | undefined, t: (key: string) => string): string {
  const normalized = (status || "pending").toLowerCase();
  if (normalized === "completed") return t("workspace.progressCompleted");
  if (normalized === "blocked") return t("workspace.progressBlocked");
  if (normalized === "cancelled") return t("workspace.progressCancelled");
  if (normalized === "in_progress" || normalized === "running") return t("workspace.progressInProgress");
  return t("workspace.progressPending");
}

export function AgentTodoPanel({ progress }: { progress: AgentProgress[] }) {
  const { t } = useTranslation();
  if (!progress.length) return null;

  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-border/80 bg-background/80 shadow-sm"
      data-vrcforge-agent-todo
    >
      <div className="flex min-w-0 items-center gap-2 border-b border-border/70 px-3 py-2.5">
        <ListTodo className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-xs font-semibold">{t("workspace.todo")}</h2>
        <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{progress.length}</span>
      </div>
      <div className="app-scrollbar max-h-72 space-y-0.5 overflow-y-auto px-2 py-2">
        {progress.map((item) => (
          <div
            key={item.progressId}
            className="grid min-w-0 grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2 rounded-md px-1.5 py-1.5 text-xs"
            data-vrcforge-agent-todo-item={item.progressId}
          >
            <span className={cn("mt-0.5 h-3.5 w-3.5 rounded-full border", todoTone(item.status))} />
            <span className="min-w-0">
              <span className="block break-words font-medium leading-5" title={item.title || item.progressId}>
                {item.title || item.progressId}
              </span>
              {item.summary ? <span className="mt-0.5 block break-words text-muted-foreground">{item.summary}</span> : null}
            </span>
            <span className="shrink-0 pt-0.5 text-muted-foreground">{todoStatusLabel(item.status, t)}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
