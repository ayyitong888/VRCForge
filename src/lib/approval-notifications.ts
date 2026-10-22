import { invoke } from "@tauri-apps/api/core";
import type { AgentApproval } from "./api";
import { playNotificationSoundOnce } from "./notification-sound-delivery";

/** Native notifications are detail-first: clicking only wakes/deep-links the app. */
export type ApprovalNotificationAction = "open";
export type SubAgentReviewNotificationAction = "open";

export type ApprovalNotificationActionPayload = {
  approvalId: string;
  action: ApprovalNotificationAction;
};

export const SUB_AGENT_REVIEW_NOTIFICATION_ACTION_EVENT = "vrcforge-sub-agent-review-notification-action";
const SUB_AGENT_REVIEW_NOTIFICATION_STORAGE_KEY = "vrcforge.subagent.review.notifications.v1";

export type SubAgentReviewNotificationPayload = {
  taskId: string;
  revision: number;
  parentChatId: string;
};

export type SubAgentReviewNotificationActionPayload = SubAgentReviewNotificationPayload & {
  action: SubAgentReviewNotificationAction;
};

/** Persist delivery identity so reconnects/restarts do not re-emit one pending revision. */
export function loadSubAgentReviewNotificationIds(): Set<string> {
  if (typeof window === "undefined") return new Set<string>();
  try {
    const raw = window.localStorage.getItem(SUB_AGENT_REVIEW_NOTIFICATION_STORAGE_KEY);
    const values = raw ? JSON.parse(raw) : [];
    return new Set(
      Array.isArray(values)
        ? values.filter((value): value is string => typeof value === "string" && value.length > 0)
        : [],
    );
  } catch {
    return new Set<string>();
  }
}

export function saveSubAgentReviewNotificationIds(ids: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SUB_AGENT_REVIEW_NOTIFICATION_STORAGE_KEY, JSON.stringify([...ids]));
  } catch {
    // Browser storage can be unavailable; the in-memory set still covers this app lifetime.
  }
}

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

export async function showSubAgentReviewNotification(
  request: SubAgentReviewNotificationPayload & {
    title: string;
    body: string;
    openLabel: string;
  },
): Promise<void> {
  const silent = await playNotificationSoundOnce(`review:${request.taskId}:${request.revision}`, "ordinary");
  await invoke("show_sub_agent_review_notification", {
    request: {
      taskId: request.taskId,
      revision: request.revision,
      parentChatId: request.parentChatId,
      title: request.title,
      body: request.body,
      openLabel: request.openLabel,
      silent,
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
  if (!approvalId || payload.action !== "open") {
    return null;
  }
  return { approvalId, action: "open" };
}
