import { invoke } from "@tauri-apps/api/core";

/** Native question notifications only wake the app; they never answer a question. */
export const AGENT_QUESTION_NOTIFICATION_ACTION_EVENT =
  "vrcforge-agent-question-notification-action";
const STORAGE_KEY = "vrcforge.agent-question.notifications.v1";
const MAX_NOTIFICATION_BODY_LENGTH = 512;

export type AgentQuestionNotificationRequest = {
  questionId: string;
  chatId: string;
  title: string;
  body: string;
};

export type AgentQuestionNotificationActionPayload = {
  questionId: string;
  chatId: string;
  action: "open";
};

export function isCallUserQuestion(question: { kind?: unknown; sourceTool?: unknown; tool?: unknown }): boolean {
  const sourceTool = String(question.sourceTool || question.tool || "").trim().toLowerCase();
  return sourceTool === "call_user";
}

export function questionNotificationKey(questionId: string): string {
  return questionId.trim();
}

/** Toasts use one line; the full question remains available in the chat card. */
export function normalizeAgentQuestionNotificationBody(value: unknown): string {
  return String(value || "").replace(/\s+/g, " ").trim().slice(0, MAX_NOTIFICATION_BODY_LENGTH);
}

export function loadAgentQuestionNotificationIds(): Set<string> {
  if (typeof window === "undefined") return new Set<string>();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
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

export function saveAgentQuestionNotificationIds(ids: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...ids]));
  } catch {
    // In-memory callers still suppress duplicate delivery for this app lifetime.
  }
}

/** Keep one delivery per pending question identity; callers remove resolved IDs. */
export function shouldNotifyAgentQuestion(
  questionId: string,
  deliveredIds: Set<string>,
): boolean {
  const key = questionNotificationKey(questionId);
  return Boolean(key && !deliveredIds.has(key));
}

/** Start only one native delivery at a time for each question identity. */
export function shouldStartAgentQuestionNotification(
  questionId: string,
  deliveredIds: Set<string>,
  inflightIds: Set<string>,
): boolean {
  const key = questionNotificationKey(questionId);
  return Boolean(key && shouldNotifyAgentQuestion(key, deliveredIds) && !inflightIds.has(key));
}

export function markAgentQuestionNotificationDelivered(
  questionId: string,
  deliveredIds: Set<string>,
): void {
  const key = questionNotificationKey(questionId);
  if (!key) return;
  deliveredIds.add(key);
  saveAgentQuestionNotificationIds(deliveredIds);
}

export async function showAgentQuestionNotification(
  request: AgentQuestionNotificationRequest,
): Promise<void> {
  await invoke("show_agent_question_notification", { request });
}

export function parseAgentQuestionNotificationAction(
  value: unknown,
): AgentQuestionNotificationActionPayload | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const questionId = typeof payload.questionId === "string" ? payload.questionId.trim() : "";
  const chatId = typeof payload.chatId === "string" ? payload.chatId.trim() : "";
  if (!questionId || !chatId || payload.action !== "open") return null;
  return { questionId, chatId, action: "open" };
}
