import i18n from "../i18n";
import type { SubAgentRole, SubAgentTask } from "./api/sub-agents";
import { subAgentLifecycleItems } from "./chat-timeline-presentation";

const BUILT_IN_ROLES = new Set([
  "project_index_review",
  "outfit_package_inspection",
  "validation_triage",
  "selected_context_review",
  "package_install_diagnosis",
  "outfit_import_plan_review",
  "skill_delegate",
]);

export function subAgentRoleLabel(role: string, configuredRoles: SubAgentRole[] = []): string {
  if (!BUILT_IN_ROLES.has(role)) {
    const configuredTitle = configuredRoles.find((item) => item.id === role)?.title?.trim();
    if (configuredTitle) return configuredTitle;
  }
  switch (role) {
    case "project_index_review":
      return i18n.t("subagent.roles.projectIndexReview");
    case "outfit_package_inspection":
      return i18n.t("subagent.roles.outfitPackageInspection");
    case "validation_triage":
      return i18n.t("subagent.roles.validationTriage");
    case "selected_context_review":
      return i18n.t("contextMenu.askInNewSession");
    case "package_install_diagnosis":
      return i18n.t("subagent.roles.packageInstallDiagnosis");
    case "outfit_import_plan_review":
      return i18n.t("subagent.roles.outfitImportPlanReview");
    case "skill_delegate":
      return i18n.t("subagent.roles.skillDelegate");
    default:
      return role || i18n.t("subagent.roles.fallback");
  }
}

export function subAgentStatusTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "completed") {
    return "ok";
  }
  if (status === "failed") {
    return "danger";
  }
  if (isActiveSubAgentStatus(status)) {
    return "warn";
  }
  return "muted";
}

export function isActiveSubAgentStatus(status: string): boolean {
  return status === "queued" || status === "running" || status === "cancelling";
}

export function projectSubAgentRegistry(tasks: SubAgentTask[], fallbackLabel: string) {
  const activeEvents = new Map<string, string>();
  const items = tasks.flatMap((task) => {
    if (!task.parentClientTurnId && !task.parentTurnId) return [];
    const lifecycle = subAgentLifecycleItems(task, fallbackLabel);
    const latest = lifecycle.at(-1);
    if (latest && isActiveSubAgentStatus(task.status)) activeEvents.set(latest.id, task.status);
    return lifecycle;
  });
  return { items, activeEvents };
}

export function displaySubAgentStatus(status: string): string {
  switch (status) {
    case "created":
      return i18n.t("subagent.statusCreated");
    case "started":
      return i18n.t("subagent.statusStarted");
    case "cancel_requested":
      return i18n.t("subagent.statusCancelRequested");
    case "queued":
      return i18n.t("subagent.statusQueued");
    case "running":
      return i18n.t("subagent.statusRunningOne");
    case "cancelling":
      return i18n.t("subagent.statusCancelling");
    case "completed":
      return i18n.t("subagent.statusCompleted");
    case "paused":
      return i18n.t("subagent.statusPaused");
    case "failed":
      return i18n.t("subagent.statusFailed");
    case "cancelled":
      return i18n.t("subagent.statusCancelled");
    case "interrupted":
      return i18n.t("subagent.statusInterrupted");
    default:
      return status || i18n.t("subagent.statusReady");
  }
}

export function subAgentTaskIssueText(task: Pick<SubAgentTask, "status" | "errorCode" | "error" | "resultUnavailable">): string {
  if (task.resultUnavailable) return i18n.t("subagent.resultUnavailable");
  if (task.status === "cancelled") return i18n.t("subagent.errors.sub_agent_task_cancelled");
  if (task.status === "interrupted") return displaySubAgentStatus(task.status);
  if (task.errorCode === "sub_agent_task_timeout") return i18n.t("subagent.errors.sub_agent_task_timeout");
  if (task.status === "failed" || task.error) return i18n.t("subagent.errors.sub_agent_task_failed");
  return "";
}
