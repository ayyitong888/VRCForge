import i18n from "../i18n";
import { thinkingStatusForModelLabel } from "./provider-ui";
import type { SubAgentTask } from "./api";
import { subAgentRoleLabel } from "./subagent-ui";
import type { RuntimeScheduleItem } from "./runtime-ui-types";

type RuntimeCurrentTurn = {
  providerLabel: string;
  model: string;
  computerUseRequested?: boolean;
} | null;

type RuntimeQueuedTurn = {
  id: string;
  text: string;
  providerLabel: string;
  model: string;
  computerUseRequested?: boolean;
};

export function buildRuntimeSchedule({
  currentTurn,
  stopRequested,
  queued,
  activeSubAgentTasks,
}: {
  currentTurn: RuntimeCurrentTurn;
  stopRequested: boolean;
  queued: RuntimeQueuedTurn[];
  activeSubAgentTasks: SubAgentTask[];
}): RuntimeScheduleItem[] {
  const items: RuntimeScheduleItem[] = [];
  if (currentTurn) {
    items.push({
      id: "current-turn",
      status: stopRequested ? "cancelling" : "running",
      title: currentTurn.computerUseRequested
        ? `${i18n.t("composerAction.desktop")} · ${thinkingStatusForModelLabel(currentTurn.providerLabel, currentTurn.model)}`
        : thinkingStatusForModelLabel(currentTurn.providerLabel, currentTurn.model),
      meta: `${currentTurn.providerLabel} / ${currentTurn.model}`,
    });
  }
  queued.forEach((turn, index) => {
    items.push({
      id: `queued-${turn.id}`,
      status: "queued",
      title: turn.computerUseRequested
        ? `${i18n.t("composerAction.desktop")} · ${turn.text || i18n.t("attachments.fallbackTitle")}`
        : turn.text || i18n.t("attachments.fallbackTitle"),
      meta: i18n.t("workspace.queueMeta", { index: index + 1, provider: turn.providerLabel, model: turn.model }),
    });
  });
  activeSubAgentTasks
    .filter((task) => ["queued", "running", "cancelling"].includes(task.status))
    .forEach((task) => {
      items.push({
        id: `subagent-${task.id}`,
        status: task.status === "cancelling" ? "cancelling" : task.status === "queued" ? "queued" : "running",
        title: task.displayName || subAgentRoleLabel(task.role),
        meta: task.task || task.status,
      });
    });
  return items;
}
