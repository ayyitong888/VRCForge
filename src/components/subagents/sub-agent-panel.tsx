import { CheckCircle2, Clock4, Dot, Menu, MonitorPlay } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api";
import { Button } from "../ui/button";

const RUNNING_STATUSES = new Set(["queued", "running", "cancelling"]);

function isTaskRunning(status: string): boolean {
  return RUNNING_STATUSES.has(status);
}

function isTaskCompleted(status: string): boolean {
  return status === "completed";
}

export function SubAgentPanel({
  tasks,
  loading,
  error,
  onOpen,
}: {
  tasks: SubAgentTask[];
  loading: boolean;
  error: string;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const running = tasks.filter((task) => isTaskRunning(task.status)).length;
  const completed = tasks.filter((task) => isTaskCompleted(task.status)).length;
  const hasActivity = Boolean(error) || tasks.length > 0;
  const markerTone = error ? "danger" : running > 0 ? "warn" : completed > 0 ? "ok" : tasks.length > 0 ? "muted" : "muted";

  if (!hasActivity) {
    return null;
  }

  return (
    <section className="rounded-xl border border-border bg-card px-3 py-3">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <MonitorPlay className="h-3.5 w-3.5" />
        <span className="font-medium text-foreground">{t("workspace.subAgents")}</span>
        <span
          className={`ml-auto inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${markerTone === "warn"
            ? "border-amber-500/60 text-amber-600"
            : markerTone === "ok"
              ? "border-emerald-500/60 text-emerald-600"
              : markerTone === "danger"
                ? "border-destructive/60 text-destructive"
                : "border-border text-muted-foreground"}`}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              markerTone === "warn"
                ? "bg-amber-500"
                : markerTone === "ok"
                  ? "bg-emerald-500"
                  : markerTone === "danger"
                    ? "bg-destructive"
                    : "bg-muted-foreground"
            }`}
          />
          {t("subagent.statusDone", { count: completed })} {t("subagent.statusRunning", { count: running })}
        </span>
      </div>

      {error ? (
        <div className="mt-2 rounded-lg border border-dashed border-destructive/50 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">
          {error}
        </div>
      ) : null}

      <div className="mt-2 flex items-center gap-2 text-[11px] text-muted-foreground">
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
      </div>

      <div className="mt-2 flex justify-between text-xs">
        <span className="text-muted-foreground">
          {t("subagent.taskLabel")} {tasks.length > 0 ? `(${tasks.length})` : ""}
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
