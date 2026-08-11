import { ChevronDown, ChevronRight, ListChecks } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { AgentRuntimeRun } from "../../lib/api";
import type { PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { RuntimeRunRow } from "./runtime-sidebar-ui";

export function RuntimeActivityPanel({
  runs,
  error,
  onSaveOperationAsSkill,
}: {
  runs: AgentRuntimeRun[];
  error?: string;
  onSaveOperationAsSkill: (summary: PathToSkillOperationSummary) => void;
}) {
  const { t } = useTranslation();
  const hasActiveRun = runs.some((run) => ["in_progress", "running", "applying", "dispatching"].includes((run.status || "").toLowerCase()));
  const [open, setOpen] = useState(hasActiveRun);
  const hasActivity = runs.length > 0 || Boolean(error);

  useEffect(() => {
    if (hasActiveRun || error) {
      setOpen(true);
    }
  }, [error, hasActiveRun]);

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
        <ListChecks className="h-3.5 w-3.5 text-primary" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("workspace.runLedger")}</span>
        <span className="text-xs text-muted-foreground">{runs.length}</span>
      </button>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <div className="text-xs text-destructive">{error}</div> : null}
          {runs.length ? (
            <div className="space-y-0.5" data-vrcforge-current-run-ledger>
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
