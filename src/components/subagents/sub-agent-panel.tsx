import { CheckCircle2, Clock4, Dot, Menu, MonitorPlay } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api";
import { Button } from "../ui/button";

const RUNNING_STATUSES = new Set(["queued", "running", "cancelling"]);

function isTaskRunning(status: string): boolean {
  return RUNNING_STATUSES.has(status);
}

export function SubAgentPanel({
  tasks,
  loading,
  error: _error,
  onOpen,
}: {
  tasks: SubAgentTask[];
  loading: boolean;
  error: string;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const running = tasks.filter((task) => isTaskRunning(task.status)).length;
  const completed = tasks.filter((task) => task.status === "completed").length;
  if (running === 0 && completed === 0 && !loading) {
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
          <span className="inline-flex items-center gap-1">
            <span className="inline-flex h-2 w-2 items-center justify-center rounded-full bg-amber-500">
              <Dot className="h-1.5 w-1.5 text-white" />
            </span>
            {t("subagent.statusRunning", { count: running })}
          </span>
          <span className="inline-flex items-center gap-1">
            <span className="inline-flex h-2 w-2 items-center justify-center rounded-full bg-emerald-500">
              <CheckCircle2 className="h-1.5 w-1.5 text-white" />
            </span>
            {t("subagent.statusDone", { count: completed })}
          </span>
        </span>
        <Button
          type="button"
          variant="ghost"
          className="h-7 px-2"
          onClick={onOpen}
          title={t("subagent.inspect")}
          data-vrcforge-open-subagent-surface
        >
          <Menu className="h-3.5 w-3.5" />
          {t("subagent.inspect")}
          {loading ? <Clock4 className="ml-1 h-3.5 w-3.5 animate-spin text-muted-foreground" /> : null}
        </Button>
      </div>
    </section>
  );
}
