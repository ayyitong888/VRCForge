import { invoke } from "@tauri-apps/api/core";
import type { AgentApproval } from "./api";

export type ApprovalNotificationAction = "approve" | "reject";
export type SubAgentReviewNotificationAction = "open";

export type ApprovalNotificationActionPayload = {
  approvalId: string;
  action: ApprovalNotificationAction;
};

export const SUB_AGENT_REVIEW_NOTIFICATION_ACTION_EVENT = "vrcforge-sub-agent-review-notification-action";

export type SubAgentReviewNotificationPayload = {
  taskId: string;
  revision: number;
  parentChatId: string;
};

export type SubAgentReviewNotificationActionPayload = SubAgentReviewNotificationPayload & {
  action: SubAgentReviewNotificationAction;
};

export async function showApprovalNotification(
  approval: AgentApproval,
  title: string,
  body: string,
  approveLabel: string,
  rejectLabel: string,
): Promise<void> {
  await invoke("show_approval_notification", {
    request: {
      approvalId: approval.id,
      title,
      body,
      approveLabel,
      rejectLabel,
    },
  });
}

export async function showSubAgentReviewNotification(
  request: SubAgentReviewNotificationPayload & {
    title: string;
    body: string;
    openLabel: string;
  },
): Promise<void> {
  await invoke("show_sub_agent_review_notification", {
    request: {
      taskId: request.taskId,
      revision: request.revision,
      parentChatId: request.parentChatId,
      title: request.title,
      body: request.body,
      openLabel: request.openLabel,
    },
  });
}

export function parseSubAgentReviewNotificationAction(value: unknown): SubAgentReviewNotificationActionPayload | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const taskId = typeof payload.taskId === "string" ? payload.taskId : "";
  const parentChatId = typeof payload.parentChatId === "string" ? payload.parentChatId : "";
  const revisionValue = payload.revision;
  const action = payload.action;
  if (
    !taskId
    || !parentChatId
    || typeof revisionValue !== "number"
    || !Number.isInteger(revisionValue)
    || revisionValue <= 0
    || action !== "open"
  ) {
    return null;
  }
  return { taskId, parentChatId, revision: revisionValue, action: "open" };
}

export function parseApprovalNotificationAction(value: unknown): ApprovalNotificationActionPayload | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const approvalId = typeof payload.approvalId === "string" ? payload.approvalId : "";
  const action = payload.action;
  if (!approvalId || (action !== "approve" && action !== "reject")) {
    return null;
  }
  return { approvalId, action };
}
