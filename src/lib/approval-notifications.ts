import { invoke } from "@tauri-apps/api/core";
import type { AgentApproval } from "./api";

export type ApprovalNotificationAction = "approve" | "reject";

export type ApprovalNotificationActionPayload = {
  approvalId: string;
  action: ApprovalNotificationAction;
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
