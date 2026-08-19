import type { TFunction } from "i18next";
import type { AgentApproval } from "./api";

export type ApprovalPresentation = {
  title: string;
  summary: string;
  notificationSummary: string;
  project: string;
  rollback: string;
  technicalDetails: Record<string, unknown>;
};

export function presentApproval(approval: AgentApproval, t: TFunction): ApprovalPresentation {
  const argumentsValue = approval.arguments || approval.paramsSummary || {};
  const projectRoot =
    approval.projectRoot ||
    readString(argumentsValue, "projectRoot", "project_root") ||
    approval.preview?.workspaceRoot ||
    approval.preview?.cwd ||
    "";
  const project = pathLeaf(projectRoot) || t("approval.presentation.currentProject");

  let title: string;
  let summary: string;
  let notificationSummary: string;
  let rollback = t("approval.presentation.rollbackDepends");

  if (approval.targetTool === "vrcforge_capture_multi_screenshot") {
    title = t("approval.presentation.captureMultiTitle");
    summary = t("approval.presentation.captureMultiSummary");
    notificationSummary = t("approval.presentation.captureMultiNotificationSummary");
    rollback = t("approval.presentation.rollbackAvailable");
  } else if (approval.targetTool === "vrcforge_capture_screenshot") {
    title = t("approval.presentation.captureSingleTitle");
    summary = t("approval.presentation.captureSingleSummary");
    notificationSummary = t("approval.presentation.captureSingleNotificationSummary");
    rollback = t("approval.presentation.rollbackAvailable");
  } else if (approval.targetTool === "vrcforge_create_gameobject") {
    const name = readString(argumentsValue, "name") || t("approval.presentation.newObject");
    const parent =
      readString(argumentsValue, "parentPath", "parent_path") ||
      t("approval.presentation.sceneRoot");
    title = t("approval.presentation.createObjectTitle", { name });
    summary = t("approval.presentation.createObjectSummary", { parent });
    notificationSummary = t("approval.presentation.createObjectNotificationSummary");
    rollback = t("approval.presentation.rollbackAvailable");
  } else if (approval.targetTool === "vrcforge_restore_checkpoint") {
    const checkpoint =
      readString(argumentsValue, "checkpointId", "checkpoint_id") ||
      approval.checkpoint?.id ||
      t("approval.presentation.savedCheckpoint");
    title = t("approval.presentation.restoreTitle");
    summary = t("approval.presentation.restoreSummary", { checkpoint });
    notificationSummary = t("approval.presentation.restoreNotificationSummary");
    rollback = t("approval.presentation.restoreEffect");
  } else if (
    approval.targetTool === "vrcforge_edit_file" ||
    approval.targetTool === "vrcforge_write_file" ||
    approval.targetTool === "vrcforge_delete_path" ||
    approval.targetTool === "vrcforge_move_path" ||
    approval.targetTool === "vrcforge_apply_patch"
  ) {
    const actionKey =
      approval.targetTool === "vrcforge_edit_file"
        ? "edit"
        : approval.targetTool === "vrcforge_write_file"
          ? (readBoolean(argumentsValue, "overwrite") ? "overwrite" : "create")
          : approval.targetTool === "vrcforge_delete_path"
            ? "delete"
            : approval.targetTool === "vrcforge_move_path"
              ? "move"
              : "patch";
    const action = t(`approval.presentation.generalAction.${actionKey}`);
    const source = pathLeaf(readString(argumentsValue, "path", "source"));
    const destination = pathLeaf(readString(argumentsValue, "destination"));
    const target = [source, destination].filter(Boolean).join(" → ") || t("approval.presentation.generalFile");
    title = t("approval.presentation.generalTitle");
    summary = t("approval.presentation.generalSummary", { action, target });
    notificationSummary = t("approval.presentation.generalNotificationSummary", { action });
  } else if (approval.preview?.command) {
    title = t("approval.presentation.commandTitle");
    summary = t("approval.presentation.commandSummary", { project });
    notificationSummary = t("approval.presentation.commandNotificationSummary");
  } else {
    title = t("approval.presentation.genericTitle");
    summary = t("approval.presentation.genericSummary", { project });
    notificationSummary = t("approval.presentation.genericNotificationSummary");
  }

  return {
    title,
    summary,
    notificationSummary,
    project,
    rollback,
    technicalDetails: compactRecord({
      toolId: approval.targetTool,
      arguments: approval.arguments,
      parameterSummary: approval.paramsSummary,
      command: approval.preview?.command,
      workingDirectory: approval.preview?.cwd,
      projectRoot,
      agentReason: approval.reason,
      riskLevel: approval.riskLevel,
      preview: approval.preview,
    }),
  };
}

function readBoolean(value: Record<string, unknown>, key: string): boolean {
  const candidate = value[key];
  return candidate === true || candidate === 1 || candidate === "true";
}

function readString(value: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "string" && candidate.trim()) {
      return candidate.trim();
    }
  }
  return "";
}

function pathLeaf(value: string): string {
  const normalized = value.replace(/[\\/]+$/, "");
  return normalized.split(/[\\/]/).filter(Boolean).pop() || "";
}

function compactRecord(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined && item !== ""));
}
