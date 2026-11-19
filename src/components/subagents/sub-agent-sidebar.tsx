import { AlertCircle, Loader2, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentRole } from "../../lib/api";
import { elapsedSecondsSince } from "../../lib/chat-streaming";
import { canCancelSubAgentTask } from "../../lib/subagent-merge";
import { groupSubAgentThreads } from "../../lib/subagent-threads";
import { displaySubAgentStatus, isActiveSubAgentStatus, subAgentRoleLabel, subAgentStatusTone, subAgentTaskIssueText } from "../../lib/subagent-ui";
import { SubAgentMark } from "./sub-agent-panel";
import { SubAgentWorkspaceSurface } from "./sub-agent-workspace-surface";
import { Button } from "../ui/button";

type SurfaceProps = ComponentProps<typeof SubAgentWorkspaceSurface>;

export type SubAgentSidebarProps = Omit<SurfaceProps, "onClose" | "onCloseSidebar"> & {
  roles?: SubAgentRole[];
  onSelect: (taskId: string) => void;
  openThread: boolean;
  onBack: () => void;
  onClose: () => void;
  loading: boolean;
  error: string;
};

function compactDuration(seconds: number | undefined, language: string): string {
  if (seconds === undefined) return "";
  const format = (value: number, unit: Intl.NumberFormatOptions["unit"]) =>
    new Intl.NumberFormat(language, { style: "unit", unit, unitDisplay: "narrow" }).format(value);
  if (seconds < 60) return format(seconds, "second");
  if (seconds < 3600) return [format(Math.floor(seconds / 60), "minute"), format(seconds % 60, "second")].join(" ");
  return [format(Math.floor(seconds / 3600), "hour"), format(Math.floor(seconds % 3600 / 60), "minute")].join(" ");
}

function ThreadListRow({
  thread,
  onSelect,
  onCancel,
  busy,
  roles,
  now,
}: {
  thread: ReturnType<typeof groupSubAgentThreads>[number];
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  busy: boolean;
  roles: SubAgentRole[];
  now: number;
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
  const active = isActiveSubAgentStatus(task.status);
  const end = active ? now : Date.parse(task.stoppedAt || task.updatedAt || "");
  const elapsed = Number.isFinite(end) ? elapsedSecondsSince(task.startedAt, end) : undefined;
  const timeLabel = compactDuration(elapsed, i18n.language);
  const name = task.displayName || subAgentRoleLabel(task.role, roles) || task.id;
  const errorText = subAgentTaskIssueText(task);
  const summary = (errorText || task.summary || task.task || "").trim().split(/\r?\n/).filter((line) => line.trim()).at(-1)?.trim();

  return (
    <div className="flex min-w-0 items-stretch gap-1" data-vrcforge-subagent-thread-row={thread.id}>
      <Button
        type="button"
        variant="ghost"
        className="h-auto min-h-12 min-w-0 flex-1 justify-start gap-2.5 px-2 py-1.5 text-left"
        onClick={() => onSelect(task.id)}
        data-app-tooltip={summary || name}
        data-vrcforge-open-subagent-thread={thread.id}
      >
        <SubAgentMark threadId={thread.id} />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">{name}</span>
            {timeLabel ? <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{timeLabel}</span> : null}
          </span>
          <span className="mt-0.5 flex min-w-0 items-center gap-2 text-xs">
            <span className={`shrink-0 ${statusClass}`}>{displaySubAgentStatus(task.status)}</span>
            {summary ? <span className="min-w-0 truncate text-muted-foreground">{summary}</span> : null}
          </span>
        </span>
      </Button>
      {canCancelSubAgentTask(task) ? (
        <Button type="button" variant="ghost" className="h-auto min-h-12 w-9 shrink-0 px-0 text-muted-foreground hover:text-destructive"
          onClick={(event) => { event.stopPropagation(); onCancel(task.id); }} disabled={busy}
          data-app-tooltip={t("subagent.cancel")} aria-label={t("subagent.cancel")} data-vrcforge-stop-subagent={thread.id}>
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
  now,
}: {
  title: string;
  threads: ReturnType<typeof groupSubAgentThreads>;
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  busyTaskIds: ReadonlySet<string>;
  roles: SubAgentRole[];
  done?: boolean;
  now: number;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const visibleThreads = expanded ? threads : threads.slice(0, 10);
  const remaining = threads.length - visibleThreads.length;
  if (!threads.length) return null;

  return (
    <section className="min-w-0" data-vrcforge-subagent-section={done ? "done" : "active"}>
      <h2 className="mb-1 flex shrink-0 items-center gap-1.5 px-2 text-xs font-medium text-muted-foreground">
        <span>{title}</span>
        <span className="ml-auto">{threads.length}</span>
      </h2>
      <div className="space-y-1">
        <div className="space-y-1">
          {visibleThreads.map((thread) => <ThreadListRow key={thread.id} thread={thread} onSelect={onSelect}
            onCancel={onCancel} busy={busyTaskIds.has(thread.task.id)} roles={roles} now={now} />)}
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
  const active = threads.filter(({ task }) => isActiveSubAgentStatus(task.status));
  const done = threads.filter(({ task }) => !isActiveSubAgentStatus(task.status));
  const listTitleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!openThread) listTitleRef.current?.focus({ preventScroll: true });
  }, [openThread]);
  const [now, setNow] = useState(Date.now);
  const hasLiveElapsed = active.some(({ task }) => task.startedAt);
  useEffect(() => {
    if (openThread || !hasLiveElapsed) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [openThread, hasLiveElapsed]);

  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col border-l border-border bg-transparent" aria-label={t("workspace.subAgents")} data-vrcforge-subagent-sidebar>
      {!openThread ? <header className="flex h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3">
        <h2 ref={listTitleRef} tabIndex={-1} className="truncate text-sm font-medium text-foreground outline-none">{t("workspace.subAgents")}</h2>
        <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" onClick={onClose}
          data-app-tooltip={t("common.close")} aria-label={t("common.close")} data-vrcforge-close-subagent-sidebar>
          <X className="h-4 w-4" />
        </Button>
      </header> : null}

      {error ? <div className="flex shrink-0 items-start gap-1.5 px-3 py-2 text-xs text-destructive" role="alert">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0 break-words">{error}</span>
      </div> : null}
      {openThread ? (
        <div className="min-h-0 flex-1 overflow-hidden" data-vrcforge-subagent-sidebar-thread>
          <SubAgentWorkspaceSurface key={surfaceProps.endpoint} {...surfaceProps} tasks={tasks} selected={surfaceProps.selected}
            emptyMessage={error ? "" : t("common.loading")} onClose={onBack} onCloseSidebar={onClose} />
        </div>
      ) : null}
        <div style={{ display: openThread ? "none" : undefined }} className="app-scrollbar min-h-0 flex-1 space-y-5 overflow-y-auto p-3" data-vrcforge-subagent-sidebar-list>
          {loading && !threads.length && !error ? <div className="flex shrink-0 items-center gap-2 px-1 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            <span>{t("common.loading")}</span>
          </div> : null}
          <ThreadSection title={t("subagent.active")} threads={active} onSelect={onSelect} onCancel={surfaceProps.onCancel}
            busyTaskIds={surfaceProps.busyTaskIds} roles={roles} now={now} />
          <ThreadSection title={t("subagent.done")} threads={done} onSelect={onSelect} onCancel={surfaceProps.onCancel}
            busyTaskIds={surfaceProps.busyTaskIds} roles={roles} now={now} done />
          {!loading && !error && !threads.length ? <p className="px-2 py-2 text-xs text-muted-foreground">{t("subagent.noTasks")}</p> : null}
        </div>
    </aside>
  );
}
