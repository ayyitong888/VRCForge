import { Ban, Check, CornerDownRight, Loader2, RefreshCw, X } from "lucide-react";
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api";
import {
  canAdoptSubAgentResult,
  canCancelSubAgentTask,
  canDismissSubAgentResult,
  canRetrySubAgentTask,
  isAwaitingMergeReview,
  isMergedAdopted,
  isMergedDismissed,
  subAgentProposedNextAction,
} from "../../lib/subagent-merge";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "../../lib/subagent-ui";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

const OPEN_STATUSES = new Set(["queued", "running", "cancelling", "failed", "cancelled", "interrupted"]);

function isOpenTask(task: SubAgentTask): boolean {
  return OPEN_STATUSES.has(task.status) || isAwaitingMergeReview(task);
}

function taskStyleByStatus(task: SubAgentTask): "warn" | "ok" | "muted" | "danger" {
  if (isAwaitingMergeReview(task)) {
    return "warn";
  }
  return subAgentStatusTone(task.status);
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

function formatRevision(value: number | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "-";
}

export function SubAgentWorkspaceSurface({
  tasks,
  selected,
  onSelect,
  onCancel,
  onRetry,
  onMerge,
  onAdoptNextAction,
  busyTaskIds,
  onClose,
}: {
  tasks: SubAgentTask[];
  selected: SubAgentTask | null;
  onSelect: (taskId: string) => void;
  onCancel: (taskId: string) => void;
  onRetry: (taskId: string) => void;
  onMerge: (task: SubAgentTask, decision: "adopted" | "dismissed") => void;
  onAdoptNextAction: (task: SubAgentTask) => void;
  busyTaskIds: ReadonlySet<string>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const openTasks = useMemo(() => tasks.filter((task) => isOpenTask(task)), [tasks]);
  const completedTasks = useMemo(
    () => tasks.filter((task) => task.status === "completed" && !isAwaitingMergeReview(task)),
    [tasks],
  );
  const activeTask = selected && tasks.some((task) => task.id === selected.id) ? selected : tasks[0] || null;
  const activeTaskNextAction = activeTask ? subAgentProposedNextAction(activeTask) : "";
  const canCancel = activeTask ? canCancelSubAgentTask(activeTask) : false;
  const canRetry = activeTask ? canRetrySubAgentTask(activeTask) : false;
  const canAdopt = activeTask ? canAdoptSubAgentResult(activeTask) : false;
  const canDismiss = activeTask ? canDismissSubAgentResult(activeTask) : false;
  const actionBusy = activeTask ? busyTaskIds.has(activeTask.id) : false;
  const hasTasks = tasks.length > 0;
  const selectedTaskStatus = activeTask ? displaySubAgentStatus(activeTask.status) : "";

  if (!hasTasks) {
    return null;
  }

  return (
    <section className="mt-3 flex h-full min-h-0 flex-col overflow-hidden rounded-xl border border-border bg-card" data-vrcforge-subagent-workspace-surface>
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span className="text-xs font-medium text-foreground">{t("workspace.subAgents")}</span>
        <Button type="button" variant="ghost" className="h-7 px-2" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
          {t("common.close")}
        </Button>
      </div>

      <div className="grid min-h-0 flex-1 gap-3 p-3 md:grid-cols-[340px,minmax(0,1fr)]">
        <section className="min-h-0">
          <div className="rounded-lg border border-border/80 bg-background p-2 text-xs text-muted-foreground">{t("subagent.openTasks")}</div>
          <div
            className="app-scrollbar mt-1 h-48 min-h-0 overflow-y-auto rounded-lg border border-border/60 bg-background/65 p-2"
            data-vrcforge-subagent-open-list
          >
            {openTasks.length ? (
              <div className="space-y-2">
                {openTasks.map((task) => (
                  <button
                    type="button"
                    key={task.id}
                    className={`w-full rounded-md border px-2 py-1.5 text-left text-xs transition-colors ${
                      activeTask?.id === task.id
                        ? "border-primary bg-primary/5"
                        : "border-border/80 hover:border-primary/50 hover:bg-muted/50"
                    }`}
                    onClick={() => onSelect(task.id)}
                  >
                    <div className="mb-1 flex items-start gap-2">
                      <Badge tone={taskStyleByStatus(task)} className="shrink-0 px-2 py-0.5 text-[10px]">
                        {displaySubAgentStatus(task.status)}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate">
                        {task.displayName || task.task || t("subagent.taskLabel")}
                      </span>
                    </div>
                    <div className="truncate text-[11px] text-muted-foreground">
                      {subAgentRoleLabel(task.role)} rev {formatRevision(task.revision)}
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-border px-2 py-2 text-muted-foreground">
                {t("subagent.noTasks")}
              </div>
            )}

            <div className="mt-2 rounded-lg border border-border bg-background p-2 text-xs text-muted-foreground">
              {t("subagent.completedTasks", { count: completedTasks.length })}
            </div>
            <div className="app-scrollbar mt-1 h-28 min-h-0 overflow-y-auto rounded-md border border-border/80 bg-background/75 p-2">
              {completedTasks.length ? (
                <div className="space-y-2">
                  {completedTasks.map((task) => (
                    <button
                      type="button"
                      key={task.id}
                      className={`w-full rounded-md border px-2 py-1.5 text-left text-xs transition-colors ${
                        activeTask?.id === task.id
                          ? "border-primary bg-primary/5"
                          : "border-border/80 hover:border-primary/50 hover:bg-muted/50"
                      }`}
                      onClick={() => onSelect(task.id)}
                    >
                      <div className="mb-1 flex items-start gap-2">
                        <Badge tone={taskStyleByStatus(task)} className="shrink-0 px-2 py-0.5 text-[10px]">
                          {displaySubAgentStatus(task.status)}
                        </Badge>
                        <span className="min-w-0 flex-1 truncate">
                          {task.displayName || task.task || t("subagent.taskLabel")}
                        </span>
                      </div>
                      <div className="truncate text-[11px] text-muted-foreground">
                        {subAgentRoleLabel(task.role)} rev {formatRevision(task.revision)}
                      </div>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>
        </section>

        <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-background/65">
          <div className="border-b border-border/80 p-2">
            <div className="flex items-start gap-2 text-xs">
              <span className="min-w-0 flex-1 truncate font-medium">
                {activeTask?.displayName || activeTask?.task || t("subagent.taskLabel")}
              </span>
              <Badge tone={activeTask ? taskStyleByStatus(activeTask) : "muted"}>{selectedTaskStatus}</Badge>
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">
              {activeTask ? subAgentRoleLabel(activeTask.role) : "-"}
            </div>
          </div>
          <div className="app-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto p-2 text-xs">
            {activeTask?.summary ? <DataLine label={t("subagent.review")} value={activeTask.summary} /> : null}
            {activeTask?.resultUnavailable ? (
              <OutputBlock label={t("subagent.result")} value={t("subagent.resultUnavailable")} danger />
            ) : null}
            {activeTask?.error ? <OutputBlock label={t("doctor.error")} value={activeTask.error} danger /> : null}
            {activeTask?.result ? <OutputBlock label={t("subagent.result")} value={formatPayload(activeTask.result)} /> : null}
            {activeTask?.events?.length ? (
              <div>
                <div className="text-[11px] font-medium text-muted-foreground">{t("subagent.eventHistory")}</div>
                <div className="app-scrollbar mt-1 space-y-1 overflow-y-auto rounded-md border border-border/80 p-2">
                  {activeTask.events.map((event, index) => (
                    <div key={index} className="rounded border border-border/50 px-2 py-1.5">
                      <div className="text-[10px] text-muted-foreground">
                        {event.timestamp ? `${event.timestamp}` : `#${index + 1}`}
                      </div>
                      <div className="font-mono break-words text-[11px]">
                        {event.event || "Unknown"}
                        {event.data ? ` : ${formatPayload(event.data)}` : ""}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {activeTaskNextAction ? (
              <div className="rounded-md border border-dashed border-border px-2 py-2">
                <div className="text-xs font-medium text-muted-foreground">{t("subagent.nextAction")}</div>
                <OutputBlock label={t("subagent.nextAction")} value={activeTaskNextAction} />
              </div>
            ) : null}
            {activeTask ? (
              <div className="rounded-md border border-dashed border-border p-2 text-xs">
                {isMergedAdopted(activeTask) ? (
                  <DataLine label={t("subagent.mergedBadge")} value={activeTask.mergedAt || "-"} />
                ) : null}
                {isMergedDismissed(activeTask) ? (
                  <DataLine label={t("subagent.dismissedBadge")} value={activeTask.mergedAt || "-"} />
                ) : null}
                <DataLine label={t("subagent.revision")} value={formatRevision(activeTask.revision)} />
                {activeTask.parentContinuationStatus ? (
                  <DataLine label={t("subagent.parentContinuation")} value={activeTask.parentContinuationStatus} />
                ) : null}
                <DataLine label={t("subagent.lastUpdated")} value={activeTask.updatedAt || activeTask.createdAt || "-"} />
              </div>
            ) : null}
          </div>
          <div className="sticky bottom-0 border-t border-border/80 bg-card p-2">
            <div className="flex flex-wrap justify-end gap-2">
              {canCancel && activeTask ? (
                <Button type="button" variant="ghost" className="h-7 px-2 text-xs" disabled={actionBusy} onClick={() => onCancel(activeTask.id)}>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("subagent.cancel")}
                </Button>
              ) : null}
              {canRetry && activeTask ? (
                <Button type="button" variant="ghost" className="h-7 px-2 text-xs" disabled={actionBusy} onClick={() => onRetry(activeTask.id)}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  {t("doctor.retry")}
                </Button>
              ) : null}
              {canAdopt && activeTask ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    disabled={actionBusy}
                    onClick={() => onMerge(activeTask, "adopted")}
                  >
                    <Check className="h-3.5 w-3.5" />
                    {t("subagent.mergeAdopt")}
                  </Button>
              ) : null}
              {canDismiss && activeTask ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    disabled={actionBusy}
                    onClick={() => onMerge(activeTask, "dismissed")}
                  >
                    <Ban className="h-3.5 w-3.5" />
                    {t("subagent.mergeDismiss")}
                  </Button>
              ) : null}
              {activeTaskNextAction && activeTask ? (
                <Button
                  type="button"
                  variant="ghost"
                  className="h-7 px-2 text-xs"
                  onClick={() => onAdoptNextAction(activeTask)}
                >
                  <CornerDownRight className="h-3.5 w-3.5" />
                  {t("subagent.adoptNextAction")}
                </Button>
              ) : null}
            </div>
          </div>
        </section>
      </div>
    </section>
  );
}
