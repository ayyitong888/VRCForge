import type { ChatThread } from "./chat-types";

export function sortChatsByPin(list: ChatThread[]): ChatThread[] {
  return [...list].sort((a, b) => {
    const pinDelta = Number(b.pinned ?? false) - Number(a.pinned ?? false);
    if (pinDelta !== 0) {
      return pinDelta;
    }
    return chatTimeMs(b) - chatTimeMs(a);
  });
}

export function cacheChatTimestampsFast(chat: ChatThread): ChatThread {
  const fallbackMs = parseChatTimeCandidate(chat.id);
  const createdMs = parseChatTimeCandidate(chat.createdAt) || fallbackMs;
  const updatedMs = parseChatTimeCandidate(chat.updatedAt) || createdMs;
  return {
    ...chat,
    createdAt: chat.createdAt || isoFromTime(createdMs),
    updatedAt: chat.updatedAt || isoFromTime(updatedMs),
  };
}

export function formatChatSidebarTime(chat: ChatThread, nowMs = Date.now(), language = "en"): string {
  const ms = chatTimeMs(chat);
  if (!ms) {
    return "";
  }
  return formatCompactRelativeTime(ms, nowMs, language);
}

export function chatTimeMs(chat: ChatThread): number {
  for (const candidate of [chat.updatedAt, chat.createdAt]) {
    const parsed = parseChatTimeCandidate(candidate);
    if (parsed) {
      return parsed;
    }
  }
  return parseChatTimeCandidate(chat.id);
}

export function isStoredChat(value: unknown): value is ChatThread {
  if (!value || typeof value !== "object") {
    return false;
  }
  const chat = value as Partial<ChatThread>;
  return typeof chat.id === "string" && chat.id.length > 0 && Array.isArray(chat.items);
}

function isoFromTime(timestampMs: number): string {
  return timestampMs ? new Date(timestampMs).toISOString() : "";
}

function parseChatTimeCandidate(value: unknown): number {
  if (typeof value !== "string" || !value.trim()) {
    return 0;
  }
  const dateValue = Date.parse(value);
  if (Number.isFinite(dateValue)) {
    return dateValue;
  }
  const match = value.match(/(?:^|[^0-9])([0-9]{13})(?:[^0-9]|$)/);
  if (!match) {
    return 0;
  }
  const timestamp = Number(match[1]);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

function formatCompactRelativeTime(timestampMs: number, nowMs = Date.now(), language = "en"): string {
  const diffMs = Math.max(0, nowMs - timestampMs);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  const week = 7 * day;
  const month = 30 * day;
  const zh = language.toLowerCase().startsWith("zh");
  if (diffMs < minute) {
    return zh ? "刚刚" : "now";
  }
  if (diffMs < hour) {
    const value = Math.max(1, Math.floor(diffMs / minute));
    return zh ? `${value} 分钟` : `${value}m`;
  }
  if (diffMs < day) {
    const value = Math.max(1, Math.floor(diffMs / hour));
    return zh ? `${value} 小时` : `${value}h`;
  }
  if (diffMs < week) {
    const value = Math.max(1, Math.floor(diffMs / day));
    return zh ? `${value} 天` : `${value}d`;
  }
  if (diffMs < month) {
    const value = Math.max(1, Math.floor(diffMs / week));
    return zh ? `${value} 周` : `${value}w`;
  }
  const value = Math.max(1, Math.floor(diffMs / month));
  return zh ? `${value} 月` : `${value}mo`;
}
