import { ChevronDown, ChevronRight, ListTodo } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AgentProgress, AgentRuntimeRun } from "../../lib/api";
import type { PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { cn } from "../../lib/utils";
import { RuntimeRunRow } from "./runtime-sidebar-ui";

function progressTone(status?: string): string {
  const normalized = (status || "pending").toLowerCase();
  if (normalized === "completed") return "bg-emerald-500";
  if (normalized === "blocked" || normalized === "cancelled") return "bg-destructive";
  if (normalized === "in_progress" || normalized === "running") return "bg-primary animate-pulse";
  return "border border-muted-foreground/60 bg-transparent";
}

function progressLabel(status: string | undefined, t: (key: string) => string): string {
  const normalized = (status || "pending").toLowerCase();
  if (normalized === "completed") return t("workspace.progressCompleted");
  if (normalized === "blocked") return t("workspace.progressBlocked");
  if (normalized === "cancelled") return t("workspace.progressCancelled");
  if (normalized === "in_progress" || normalized === "running") return t("workspace.progressInProgress");
  return t("workspace.progressPending");
}

export function RuntimeActivityPanel({
  progress,
  runs,
  error,
  onSaveOperationAsSkill,
}: {
  progress: AgentProgress[];
  runs: AgentRuntimeRun[];
  error?: string;
  onSaveOperationAsSkill: (summary: PathToSkillOperationSummary) => void;
}) {
  const { t } = useTranslation();
  const hasActiveProgress = progress.some((item) => ["in_progress", "running", "blocked"].includes((item.status || "").toLowerCase()));
  const [open, setOpen] = useState(hasActiveProgress);
  const hasActivity = progress.length > 0 || runs.length > 0 || Boolean(error);

  useEffect(() => {
    if (hasActiveProgress || error) {
      setOpen(true);
    }
  }, [error, hasActiveProgress]);

  if (!hasActivity) {
    return null;
  }

  return (
    <section
      className="mb-3 overflow-hidden rounded-xl border border-border bg-card shadow-panel"
      data-vrcforge-runtime-activity-panel
    >
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" /> : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
        <ListTodo className="h-3.5 w-3.5 text-primary" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("workspace.progress")}</span>
        <span className="text-xs text-muted-foreground">{progress.length + runs.length}</span>
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <div className="text-xs text-destructive">{error}</div> : null}
          {progress.length ? (
            <div className="space-y-1" data-vrcforge-current-progress>
              {progress.map((item) => (
                <div key={item.progressId} className="grid min-w-0 grid-cols-[12px_minmax(0,1fr)_auto] items-center gap-2 py-1 text-xs">
                  <span className={cn("h-2.5 w-2.5 rounded-full", progressTone(item.status))} />
                  <span className="min-w-0 truncate font-medium" title={item.title || item.summary || item.progressId}>
                    {item.title || item.summary || item.progressId}
                  </span>
                  <span className="text-muted-foreground">{progressLabel(item.status, t)}</span>
                </div>
              ))}
            </div>
          ) : null}
          {runs.length ? (
            <div className="space-y-0.5 border-t border-border pt-2" data-vrcforge-current-run-ledger>
              {runs.slice(0, 8).map((run, index) => (
                <RuntimeRunRow
                  key={run.id || `${run.targetTool || run.writeTool || "run"}-${index}`}
                  run={run}
                  onSaveAsSkill={onSaveOperationAsSkill}
                />
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
