import { Check, CircleAlert, ListTodo, Minus, Pause } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentProgress } from "../../lib/api";
import { cn } from "../../lib/utils";

const todoMarkers = new Map([
  ["completed", { icon: Check, label: "workspace.progressCompleted" }],
  ["blocked", { icon: Pause, label: "workspace.progressBlocked" }],
  ["failed", { icon: CircleAlert, label: "skillStatus.failed" }],
  ["cancelled", { icon: Minus, label: "workspace.progressCancelled" }],
]);

function TodoItems({ progress }: { progress: AgentProgress[] }) {
  const { t } = useTranslation();
  return (
    <ol className="m-0 grid list-none gap-2 p-0">
      {progress.map((item, index) => {
        const status = (item.status || "pending").toLowerCase();
        const active = status === "in_progress" || status === "running";
        const marker = todoMarkers.get(status);
        const Icon = marker?.icon;
        const label = t(marker?.label ?? (active ? "workspace.progressInProgress" : "workspace.progressPending"));
        return (
          <li
            key={item.progressId}
            className="grid min-w-0 grid-cols-[1.5rem_minmax(0,1fr)] items-start gap-3 px-1 py-0.5"
            data-vrcforge-agent-todo-item={item.progressId}
            data-vrcforge-agent-todo-status={status}
            aria-current={active ? "step" : undefined}
          >
            <span role="img" aria-label={label} title={label} className={cn(
              "flex h-6 w-6 items-center justify-center rounded-full text-xs tabular-nums text-muted-foreground",
              !Icon && "border border-primary/30",
              active && "border-primary bg-primary/10 text-foreground",
              status === "completed" && "bg-primary text-primary-foreground",
              status === "failed" && "text-destructive",
            )}>
              {Icon ? <Icon className="h-3.5 w-3.5" strokeWidth={status === "completed" ? 2.5 : 2} aria-hidden="true" /> : index + 1}
            </span>
            <span className={cn("min-w-0 break-words text-sm leading-6",
              active ? "font-medium text-foreground" : "text-muted-foreground",
              status === "completed" && "line-through",
              (status === "blocked" || status === "failed") && "text-foreground",
            )} title={item.title || item.progressId}>
              {item.title || item.progressId}
            </span>
          </li>
        );
      })}
    </ol>
  );
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
        <TodoItems progress={progress} />
      </div>
    </section>
  );
}

export function AgentTodoPanelEmbedded({ progress }: { progress: AgentProgress[] }) {
  if (!progress.length) return null;

  return (
    <div
      className="space-y-0.5"
      data-vrcforge-agent-todo
    >
      <TodoItems progress={progress} />
    </div>
  );
}
