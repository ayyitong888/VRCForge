import { ListTodo } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { AgentProgress } from "../../lib/api";
import { cn } from "../../lib/utils";

function todoTextClass(itemStatus?: string): string {
  const normalized = (itemStatus || "pending").toLowerCase();
  if (normalized === "completed") {
    return "text-muted-foreground line-through";
  }
  if (normalized === "failed" || normalized === "blocked") {
    return "text-red-600 dark:text-red-400";
  }
  if (normalized === "in_progress" || normalized === "running") {
    return "text-foreground";
  }
  return "text-muted-foreground";
}

function isActiveTodo(itemStatus?: string): boolean {
  const normalized = (itemStatus || "pending").toLowerCase();
  return normalized === "in_progress" || normalized === "running";
}

function todoMarkerClass(itemStatus?: string): string {
  const normalized = (itemStatus || "pending").toLowerCase();
  if (normalized === "completed") return "border-primary bg-primary text-background";
  if (normalized === "in_progress" || normalized === "running") return "border-primary bg-primary/15 text-primary";
  if (normalized === "failed" || normalized === "blocked") return "border-red-600 text-red-600 dark:border-red-400 dark:text-red-400";
  return "border-border text-muted-foreground";
}

function TodoItems({
  progress,
  rowClassName,
}: {
  progress: AgentProgress[];
  rowClassName: string;
}) {
  return (
    <ol className="m-0 list-none space-y-0.5 p-0">
      {progress.map((item, index) => (
        <li
          key={item.progressId}
          className={rowClassName}
          data-vrcforge-agent-todo-item={item.progressId}
          data-vrcforge-agent-todo-status={item.status || "pending"}
          aria-current={isActiveTodo(item.status) ? "step" : undefined}
        >
          <span
            className={cn(
              "flex h-6 w-6 items-center justify-center rounded-full border text-xs font-semibold tabular-nums",
              todoMarkerClass(item.status),
            )}
            aria-hidden="true"
          >
            {index + 1}
          </span>
          <span
            className={cn(
              "min-w-0 break-words font-medium leading-6",
              todoTextClass(item.status),
              isActiveTodo(item.status) ? "animate-pulse motion-reduce:animate-none" : "",
            )}
            title={item.title || item.progressId}
          >
            {item.title || item.progressId}
          </span>
        </li>
      ))}
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
        <TodoItems progress={progress} rowClassName="grid min-w-0 grid-cols-[28px_minmax(0,1fr)] items-start gap-3 rounded-md px-1.5 py-2 text-sm" />
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
      <TodoItems progress={progress} rowClassName="grid min-w-0 grid-cols-[28px_minmax(0,1fr)] items-start gap-3 rounded-md px-1.5 py-2 text-sm" />
    </div>
  );
}
