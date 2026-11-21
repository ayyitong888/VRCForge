import { Check, ChevronDown, ChevronRight, CircleAlert, ListTodo, Minus, Pause } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AgentProgress } from "../../lib/api";
import { cn } from "../../lib/utils";

const todoMarkers = new Map([
  ["completed", { icon: Check, label: "workspace.progressCompleted" }],
  ["blocked", { icon: Pause, label: "workspace.progressBlocked" }],
  ["failed", { icon: CircleAlert, label: "skillStatus.failed" }],
  ["cancelled", { icon: Minus, label: "workspace.progressCancelled" }],
]);

export type TodoReveal = {
  progressId: string;
  sessionId: string;
  onRevealed: () => void;
};

type TodoPanelProps = {
  progress: AgentProgress[];
  activeTodoIds?: ReadonlySet<string>;
  onOpenTodo?: (progress: AgentProgress) => void;
  todoReveal?: TodoReveal;
};

function TodoItems({ progress, activeTodoIds, onOpenTodo, todoReveal }: TodoPanelProps) {
  const { t } = useTranslation();
  const targetRow = useRef<HTMLLIElement>(null);
  const targetIndex = todoReveal ? progress.findIndex((item) =>
    item.progressId === todoReveal.progressId && item.sessionId?.trim() === todoReveal.sessionId,
  ) : -1;
  useLayoutEffect(() => {
    const row = targetRow.current;
    if (!todoReveal || !row) return;
    row.scrollIntoView({ block: "nearest", inline: "nearest" });
    (row.querySelector("button") ?? row).focus({ preventScroll: true });
    todoReveal.onRevealed();
  }, [todoReveal, targetIndex]);

  return (
    <ol className="m-0 grid list-none gap-2 p-0">
      {progress.map((item, index) => {
        const status = (item.status || "pending").toLowerCase();
        const active = status === "in_progress" || status === "running";
        const marker = todoMarkers.get(status);
        const Icon = marker?.icon;
        const label = t(marker?.label ?? (active ? "workspace.progressInProgress" : "workspace.progressPending"));
        const title = item.title || item.progressId;
        const titleClassName = cn("min-w-0 break-words text-sm leading-6",
          active ? "font-medium text-foreground" : "text-muted-foreground",
          status === "completed" && "line-through",
          (status === "blocked" || status === "failed") && "text-foreground",
        );
        return (
          <li
            key={item.progressId}
            ref={index === targetIndex ? targetRow : undefined}
            tabIndex={onOpenTodo && item.sessionId ? undefined : -1}
            className="grid min-w-0 grid-cols-[1.5rem_minmax(0,1fr)] items-start gap-3 px-1 py-0.5"
            data-vrcforge-agent-todo-item={item.progressId}
            data-vrcforge-agent-todo-status={status}
            aria-current={active ? "step" : undefined}
          >
            <span role="img" aria-label={label} data-app-tooltip={label} className={cn(
              "flex h-6 w-6 items-center justify-center rounded-full text-xs tabular-nums text-muted-foreground",
              !active && !marker && "border border-primary/30",
              (active || status === "completed") && "bg-primary text-primary-foreground",
              activeTodoIds?.has(item.progressId) && "motion-safe:animate-pulse",
              status === "blocked" && "border border-muted-foreground/40 bg-muted/60",
              status === "failed" && "text-destructive",
            )}>
              {Icon ? (
                <Icon
                  className={cn("h-3.5 w-3.5", status === "blocked" && "fill-current")}
                  strokeWidth={status === "completed" ? 2.5 : 2}
                  // The check path spans y=6..17, so its center is 11.5.
                  viewBox={status === "completed" ? "0 -0.5 24 24" : "0 0 24 24"}
                  aria-hidden="true"
                />
              ) : <span className="[text-box:trim-both_cap_alphabetic]">{index + 1}</span>}
            </span>
            {onOpenTodo && item.sessionId ? (
              <button type="button" className={cn(titleClassName, "rounded-sm text-left focus:outline-none focus:ring-2 focus:ring-ring")}
                onClick={() => onOpenTodo(item)}>
                {title}
              </button>
            ) : <span className={titleClassName}>{title}</span>}
          </li>
        );
      })}
    </ol>
  );
}

export function AgentTodoPanel({ progress, activeTodoIds, onOpenTodo, todoReveal }: TodoPanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => {
    if (todoReveal) setCollapsed(false);
  }, [todoReveal]);
  if (!progress.length) return null;

  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-border/80 bg-background/80 shadow-sm"
      data-vrcforge-agent-todo
    >
      <h2 className={cn("text-xs font-semibold", !collapsed && "border-b border-border/70")}>
        <button type="button" aria-expanded={!collapsed} aria-label={t("workspace.todo")}
          className="flex w-full min-w-0 items-center gap-2 px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          onClick={() => setCollapsed((current) => !current)}>
          <ListTodo className="h-4 w-4 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate">{t("workspace.todo")}</span>
          <span className="shrink-0 font-normal tabular-nums text-muted-foreground">{progress.length}</span>
          {collapsed ? <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            : <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />}
        </button>
      </h2>
      {!collapsed ? (
        <div className="app-scrollbar max-h-72 space-y-0.5 overflow-y-auto px-2 py-2">
          <TodoItems progress={progress} activeTodoIds={activeTodoIds} onOpenTodo={onOpenTodo} todoReveal={todoReveal} />
        </div>
      ) : null}
    </section>
  );
}

export function AgentTodoPanelEmbedded({ progress, activeTodoIds, onOpenTodo, todoReveal }: TodoPanelProps) {
  if (!progress.length) return null;

  return (
    <div
      className="space-y-0.5"
      data-vrcforge-agent-todo
    >
      <TodoItems progress={progress} activeTodoIds={activeTodoIds} onOpenTodo={onOpenTodo} todoReveal={todoReveal} />
    </div>
  );
}
