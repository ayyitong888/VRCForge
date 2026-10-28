import { AlertCircle, CheckCircle2, Dot, Loader2, Square, X } from "lucide-react";
import { useMemo, useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { canCancelSubAgentTask } from "../../lib/subagent-merge";
import { groupSubAgentThreads } from "../../lib/subagent-threads";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "../../lib/subagent-ui";
import { formatMessageDateSeparator } from "../../lib/message-time";
import { SubAgentWorkspaceSurface } from "./sub-agent-workspace-surface";
import { Button } from "../ui/button";

type SurfaceProps = ComponentProps<typeof SubAgentWorkspaceSurface>;

export type SubAgentSidebarProps = Omit<SurfaceProps, "onClose"> & {
  onSelect: (taskId: string) => void;
  openThread: boolean;
  onBack: () => void;
  onClose: () => void;
  loading: boolean;
  error: string;
};

const ACTIVE_STATUSES = new Set(["queued", "running", "cancelling"]);

function ThreadListRow({
  thread,
  onSelect,
  onCancel,
  busy,
  roles,
}: {
  thread: ReturnType<typeof groupSubAgentThreads>[number];
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  busy: boolean;
  roles: NonNullable<SurfaceProps["roles"]>;
}) {
  const { t, i18n } = useTranslation();
  const task = thread.task;
  const tone = subAgentStatusTone(task.status);
  const statusClass = tone === "ok"
    ? "text-emerald-600 dark:text-emerald-400"
    : tone === "warn"
      ? "text-amber-600 dark:text-amber-400"
      : tone === "danger"
        ? "text-destructive"
        : "text-muted-foreground";
  const timeLabel = formatMessageDateSeparator(task.createdAt, i18n.language)
    || formatMessageDateSeparator(task.startedAt, i18n.language);

  return (
    <div className="flex min-w-0 items-stretch gap-1" data-vrcforge-subagent-thread-row={thread.id}>
      <Button
        type="button"
        variant="ghost"
        className="h-auto min-h-12 min-w-0 flex-1 justify-start gap-2 px-2 py-2 text-left"
        onClick={() => onSelect(task.id)}
        title={task.displayName || task.task || task.id}
        data-vrcforge-open-subagent-thread={thread.id}
      >
        <span
          className={`mt-0.5 inline-flex h-2 w-2 shrink-0 rounded-full ${tone === "ok" ? "bg-emerald-500" : tone === "warn" ? "bg-amber-500" : tone === "danger" ? "bg-destructive" : "bg-muted-foreground"}`}
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-xs font-medium text-foreground">
              {task.displayName || subAgentRoleLabel(task.role, roles) || task.id}
            </span>
            <span className={`shrink-0 text-[10px] ${statusClass}`}>{displaySubAgentStatus(task.status)}</span>
            {timeLabel ? <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">{timeLabel}</span> : null}
          </span>
          <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
            {task.task || subAgentRoleLabel(task.role, roles)}
          </span>
          {task.error?.trim() ? (
            <span className="mt-1 flex items-start gap-1 text-[11px] text-destructive" title={task.error.trim()}>
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
              <span className="line-clamp-2 break-words">{task.error.trim()}</span>
            </span>
          ) : null}
        </span>
      </Button>
      {canCancelSubAgentTask(task) ? (
        <Button type="button" variant="ghost" className="h-auto min-h-12 w-9 shrink-0 px-0 text-muted-foreground hover:text-destructive"
          onClick={(event) => { event.stopPropagation(); onCancel(task.id); }} disabled={busy}
          title={t("subagent.cancel")} aria-label={t("subagent.cancel")} data-vrcforge-stop-subagent={thread.id}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
        </Button>
      ) : null}
    </div>
  );
}

function ThreadSection({
  title,
  threads,
  onSelect,
  onCancel,
  busyTaskIds,
  roles,
  done = false,
}: {
  title: string;
  threads: ReturnType<typeof groupSubAgentThreads>;
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  busyTaskIds: ReadonlySet<string>;
  roles: NonNullable<SurfaceProps["roles"]>;
  done?: boolean;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const visibleThreads = expanded ? threads : threads.slice(0, 10);
  const remaining = threads.length - visibleThreads.length;

  return (
    <section className="min-w-0" data-vrcforge-subagent-section={done ? "done" : "active"}>
      <h2 className="mb-1 flex shrink-0 items-center gap-1.5 px-1 text-[11px] font-medium text-muted-foreground">
        {done ? <CheckCircle2 className="h-3 w-3 text-emerald-500" aria-hidden="true" /> : <Dot className="h-4 w-4 text-amber-500" aria-hidden="true" />}
        <span>{title}</span>
        <span className="ml-auto">{threads.length}</span>
      </h2>
      <div className="space-y-1">
        {threads.length === 0 ? <p className="px-1 py-2 text-xs text-muted-foreground">{t("subagent.noTasks")}</p> : null}
        <div className="space-y-1">
          {visibleThreads.map((thread) => <ThreadListRow key={thread.id} thread={thread} onSelect={onSelect}
            onCancel={onCancel} busy={busyTaskIds.has(thread.task.id)} roles={roles} />)}
        </div>
        {remaining > 0 ? <Button type="button" variant="ghost" className="mt-1 h-8 w-full justify-start px-2 text-xs text-muted-foreground"
          onClick={() => setExpanded(true)} data-vrcforge-subagent-show-more={done ? "done" : "active"}>
          {t("subagent.showMore", { count: remaining })}
        </Button> : null}
      </div>
    </section>
  );
}

export function SubAgentSidebar({
  openThread,
  onBack,
  onClose,
  loading,
  error,
  tasks,
  roles = [],
  onSelect,
  ...surfaceProps
}: SubAgentSidebarProps) {
  const { t } = useTranslation();
  const threads = useMemo(() => groupSubAgentThreads(tasks), [tasks]);
  const active = threads.filter((thread) => ACTIVE_STATUSES.has(thread.task.status));
  const done = threads.filter((thread) => !ACTIVE_STATUSES.has(thread.task.status));

  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col border-l border-border bg-transparent" data-vrcforge-subagent-sidebar>
      <header className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
        <span className="truncate text-sm font-medium text-foreground">{t("workspace.subAgents")}</span>
        <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" onClick={onClose}
          title={t("common.close")} aria-label={t("common.close")} data-vrcforge-close-subagent-sidebar>
          <X className="h-4 w-4" />
        </Button>
      </header>

      {openThread ? (
        <div className="min-h-0 flex-1 overflow-hidden" data-vrcforge-subagent-sidebar-thread>
          <SubAgentWorkspaceSurface {...surfaceProps} tasks={tasks} roles={roles} selected={surfaceProps.selected}
            onClose={onBack} />
        </div>
      ) : null}
        <div style={{ display: openThread ? "none" : undefined }} className="app-scrollbar min-h-0 flex-1 space-y-5 overflow-y-auto p-3" data-vrcforge-subagent-sidebar-list>
          {error ? <div className="flex shrink-0 items-start gap-1.5 text-xs text-destructive" role="alert">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div> : null}
          {loading ? <div className="flex shrink-0 items-center gap-2 px-1 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span>{t("common.loading")}</span>
          </div> : null}
          <ThreadSection title={t("subagent.active")} threads={active} onSelect={onSelect} onCancel={surfaceProps.onCancel}
            busyTaskIds={surfaceProps.busyTaskIds} roles={roles} />
          <ThreadSection title={t("subagent.done")} threads={done} onSelect={onSelect} onCancel={surfaceProps.onCancel}
            busyTaskIds={surfaceProps.busyTaskIds} roles={roles} done />
        </div>
    </aside>
  );
}
