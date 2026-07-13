import i18n from "../i18n";

export function subAgentRoleLabel(role: string): string {
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
  if (status === "queued" || status === "running" || status === "cancelling") {
    return "warn";
  }
  return "muted";
}

export function displaySubAgentStatus(status: string): string {
  switch (status) {
    case "queued":
      return i18n.t("subagent.statusQueued");
    case "running":
      return i18n.t("subagent.statusRunningOne");
    case "cancelling":
      return i18n.t("subagent.statusCancelling");
    case "completed":
      return i18n.t("subagent.statusCompleted");
    case "failed":
      return i18n.t("subagent.statusFailed");
    default:
      return status || i18n.t("subagent.statusReady");
  }
}
