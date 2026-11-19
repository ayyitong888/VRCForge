import { invoke } from "@tauri-apps/api/core";
import type { AgentApproval } from "./api";
import { playNotificationSoundOnce } from "./notification-sound-delivery";

/** Native notifications are detail-first: clicking only wakes/deep-links the app. */
export type ApprovalNotificationAction = "open";

export type ApprovalNotificationActionPayload = {
  approvalId: string;
  action: ApprovalNotificationAction;
};

export async function showApprovalNotification(
  approval: AgentApproval,
  title: string,
  body: string,
): Promise<void> {
  const silent = await playNotificationSoundOnce(`approval:${approval.id}`, "blocking");
  await invoke("show_approval_notification", {
    request: {
      approvalId: approval.id,
      title,
      body,
      silent,
    },
  });
}

export function parseApprovalNotificationAction(value: unknown): ApprovalNotificationActionPayload | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const payload = value as Record<string, unknown>;
  const approvalId = typeof payload.approvalId === "string" ? payload.approvalId : "";
  if (!approvalId || payload.action !== "open") {
    return null;
  }
  return { approvalId, action: "open" };
}
