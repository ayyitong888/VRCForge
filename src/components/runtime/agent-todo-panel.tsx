import { ListTodo } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentProgress } from "../../lib/api";
import { cn } from "../../lib/utils";

function todoTone(status?: string): string {
  const normalized = (status || "pending").toLowerCase();
  if (normalized === "completed") return "border-emerald-500 bg-emerald-500";
  if (normalized === "failed" || normalized === "rejected") return "border-destructive bg-destructive";
  if (normalized === "blocked" || normalized === "cancelled") return "border-amber-500 bg-amber-500";
  if (normalized === "in_progress" || normalized === "running") return "border-primary bg-primary";
  return "border-muted-foreground/45 bg-transparent";
}

function todoTextClass(itemStatus?: string): string {
  const normalized = (itemStatus || "pending").toLowerCase();
  if (normalized === "completed") {
    return "text-muted-foreground line-through";
  }
  return "text-foreground";
}

function isActiveTodo(itemStatus?: string): boolean {
  const normalized = (itemStatus || "pending").toLowerCase();
  return normalized === "in_progress" || normalized === "running";
}

function todoStatusLabel(status: string | undefined, t: (key: string) => string): string {
  const normalized = (status || "pending").toLowerCase();
  if (normalized === "completed") return t("workspace.progressCompleted");
  if (normalized === "failed" || normalized === "rejected") return t("workspace.runStatusFailed");
  if (normalized === "blocked") return t("workspace.progressBlocked");
  if (normalized === "cancelled") return t("workspace.progressCancelled");
  if (normalized === "in_progress" || normalized === "running") return t("workspace.progressInProgress");
  return t("workspace.progressPending");
}

function TodoItems({
  progress,
  t,
  rowClassName,
}: {
  progress: AgentProgress[];
  t: (key: string) => string;
  rowClassName: string;
}) {
  return (
    <>
      {progress.map((item) => (
        <div
          key={item.progressId}
          className={rowClassName}
          data-vrcforge-agent-todo-item={item.progressId}
        >
          <span
            className={cn(
              "mt-0.5 h-3.5 w-3.5 rounded-full border",
              todoTone(item.status),
              isActiveTodo(item.status) ? "animate-pulse motion-reduce:animate-none" : "",
            )}
          />
          <span className="min-w-0">
            <span className={cn("block break-words font-medium leading-5", todoTextClass(item.status))} title={item.title || item.progressId}>
              {item.title || item.progressId}
            </span>
            {item.summary ? (
              <span className={cn("mt-0.5 block break-words", todoTextClass(item.status))}>{item.summary}</span>
            ) : null}
          </span>
          <span className="shrink-0 pt-0.5 text-muted-foreground">{todoStatusLabel(item.status, t)}</span>
        </div>
      ))}
    </>
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
        <TodoItems progress={progress} t={t} rowClassName="grid min-w-0 grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2 rounded-md px-1.5 py-1.5 text-xs" />
      </div>
    </section>
  );
}

export function AgentTodoPanelEmbedded({ progress }: { progress: AgentProgress[] }) {
  const { t } = useTranslation();
  if (!progress.length) return null;

  return (
    <div
      className="space-y-0.5"
      data-vrcforge-agent-todo
      >
      <TodoItems progress={progress} t={t} rowClassName="grid min-w-0 grid-cols-[16px_minmax(0,1fr)_auto] items-start gap-2 rounded-md px-1.5 py-1.5 text-xs" />
    </div>
  );
}
