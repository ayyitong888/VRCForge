import { AlertCircle, CheckCircle2, Clock4, Dot, Menu, MonitorPlay } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SubAgentRole, SubAgentTask } from "../../lib/api";
import { groupSubAgentThreads, type SubAgentThreadGroup } from "../../lib/subagent-threads";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "../../lib/subagent-ui";
import { Button } from "../ui/button";

const RUNNING_STATUSES = new Set(["queued", "running", "cancelling"]);

function isTaskRunning(status: string): boolean {
  return RUNNING_STATUSES.has(status);
}

function taskErrors(thread: SubAgentThreadGroup): string[] {
  return thread.task.error?.trim() ? [thread.task.error.trim()] : [];
}

function ThreadRow({
  thread,
  onOpen,
  roles,
}: {
  thread: SubAgentThreadGroup;
  onOpen: (taskId?: string) => void;
  roles: SubAgentRole[];
}) {
  const { t } = useTranslation();
  const { task } = thread;
  const errors = taskErrors(thread);
  const tone = subAgentStatusTone(task.status);
  const statusClass = tone === "ok"
    ? "text-emerald-600 dark:text-emerald-400"
    : tone === "warn"
      ? "text-amber-600 dark:text-amber-400"
      : tone === "danger"
        ? "text-destructive"
        : "text-muted-foreground";

  return (
    <Button
      type="button"
      variant="ghost"
      className="h-auto min-h-12 w-full justify-start gap-2 px-2 py-2 text-left"
      onClick={() => onOpen(task.id)}
      title={task.displayName || task.role || task.id}
      data-vrcforge-open-subagent-thread={thread.id}
    >
      <span className={`mt-0.5 inline-flex h-2 w-2 shrink-0 rounded-full ${tone === "ok" ? "bg-emerald-500" : tone === "warn" ? "bg-amber-500" : tone === "danger" ? "bg-destructive" : "bg-muted-foreground"}`} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate text-xs font-medium text-foreground">
            {task.displayName || subAgentRoleLabel(task.role, roles) || task.id}
          </span>
          <span className={`shrink-0 text-[10px] ${statusClass}`}>{displaySubAgentStatus(task.status)}</span>
        </span>
        <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
          {task.task || subAgentRoleLabel(task.role, roles)}
        </span>
        {errors.map((message) => (
          <span key={message} className="mt-1 flex items-start gap-1 text-[11px] text-destructive" title={message}>
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            <span className="line-clamp-2 break-words">{message}</span>
          </span>
        ))}
      </span>
    </Button>
  );
}

function ThreadSection({
  title,
  threads,
  onOpen,
  roles,
  done,
}: {
  title: string;
  threads: SubAgentThreadGroup[];
  onOpen: (taskId?: string) => void;
  roles: SubAgentRole[];
  done?: boolean;
}) {
  const { t } = useTranslation();
  if (threads.length === 0) return null;

  return (
    <div>
      <div className="mb-1 flex items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
        {done ? <CheckCircle2 className="h-3 w-3 text-emerald-500" /> : <Dot className="h-4 w-4 text-amber-500" />}
        <span>{title}</span>
        <span className="ml-auto">{threads.length}</span>
      </div>
      <div className="space-y-1">
        {threads.map((thread) => <ThreadRow key={thread.id} thread={thread} onOpen={onOpen} roles={roles} />)}
      </div>
    </div>
  );
}

export function SubAgentPanel({
  tasks,
  loading,
  error,
  onOpen,
  roles = [],
}: {
  tasks: SubAgentTask[];
  loading: boolean;
  error: string;
  onOpen: (taskId?: string) => void;
  roles?: SubAgentRole[];
}) {
  const { t } = useTranslation();
  const threads = groupSubAgentThreads(tasks);
  const active = threads.filter((thread) => isTaskRunning(thread.task.status));
  const done = threads.filter((thread) => !isTaskRunning(thread.task.status));
  if (threads.length === 0 && !loading && !error) {
    return null;
  }

  return (
    <section className="rounded-xl border border-border bg-card px-3 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <MonitorPlay className="h-3.5 w-3.5" />
        <span className="font-medium text-foreground">{t("workspace.subAgents")}</span>
      </div>
      <div className="mt-2 flex items-center justify-between text-xs">
        <span className="inline-flex items-center gap-3 text-muted-foreground">
          <span>{t("subagent.statusRunning", { count: active.length })}</span>
          <span>{t("subagent.statusDone", { count: done.length })}</span>
        </span>
        <Button
          type="button"
          variant="ghost"
          className="h-7 px-2"
          onClick={() => onOpen()}
          title={t("subagent.inspect")}
          data-vrcforge-open-subagent-surface
        >
          <Menu className="h-3.5 w-3.5" />
          {t("subagent.inspect")}
          {loading ? <Clock4 className="ml-1 h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
        </Button>
      </div>
      {error ? <div className="mt-2 flex items-start gap-1.5 text-xs text-destructive"><AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{error}</div> : null}
      <div className="mt-3 max-h-72 space-y-3 overflow-y-auto" data-vrcforge-subagent-threads>
        <ThreadSection title={t("subagent.active")} threads={active} onOpen={onOpen} roles={roles} />
        <ThreadSection title={t("subagent.done")} threads={done} onOpen={onOpen} roles={roles} done />
        {threads.length === 0 && !loading ? <p className="px-1 text-xs text-muted-foreground">{t("subagent.noTasks")}</p> : null}
      </div>
    </section>
  );
}
