import type { AgentContextUsage } from "./api";
import type { ChatThread, ConversationItem } from "./chat-types";

export type ChatSidebarGroups = {
  temporaryChats: ChatThread[];
  projectChatsByPath: Map<string, ChatThread[]>;
};

export function sortChatsByPin(list: ChatThread[]): ChatThread[] {
  return [...list].sort(compareChatsForSidebar);
}

export function groupSidebarChats(
  list: ChatThread[],
  normalizeProjectPath: (value: string) => string,
): ChatSidebarGroups {
  const temporaryChats: ChatThread[] = [];
  const projectChatsByPath = new Map<string, ChatThread[]>();
  for (const chat of list) {
    if (chat.archived) {
      continue;
    }
    if (isUnstartedChat(chat)) {
      continue;
    }
    const key = normalizeProjectPath(chat.projectPath || "");
    if (!key) {
      temporaryChats.push(chat);
      continue;
    }
    const group = projectChatsByPath.get(key);
    if (group) {
      group.push(chat);
    } else {
      projectChatsByPath.set(key, [chat]);
    }
  }
  temporaryChats.sort(compareChatsForSidebar);
  for (const group of projectChatsByPath.values()) {
    group.sort(compareChatsForSidebar);
  }
  return { temporaryChats, projectChatsByPath };
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

export function cacheChatContextUsageFast(chat: ChatThread): ChatThread {
  const latestUsage = latestExactAgentContextUsage(chat.items);
  if (!latestUsage) {
    if (chat.contextUsageCache) {
      const { contextUsageCache: _contextUsageCache, ...rest } = chat;
      return rest;
    }
    return chat;
  }
  return { ...chat, contextUsageCache: latestUsage };
}

export function filterPersistableChats(list: ChatThread[]): ChatThread[] {
  return list
    .map((chat) => {
      const items = stripTransientConversationItems(chat.items);
      return items.length === chat.items.length ? chat : { ...chat, items };
    })
    .filter((chat) => !isUnstartedChat(chat));
}

export function stripTransientConversationItems(items: ConversationItem[]): ConversationItem[] {
  return items.filter((item) => item.type !== "streaming");
}

export function normalizeChatContextUsage(value: unknown): AgentContextUsage | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const usage = value as AgentContextUsage;
  if (usage.exact !== true) {
    return undefined;
  }
  const hasTokenCount =
    typeof usage.inputTokens === "number" ||
    typeof usage.totalTokens === "number" ||
    typeof usage.outputTokens === "number";
  return hasTokenCount ? usage : undefined;
}

function latestExactAgentContextUsage(items: ConversationItem[]): AgentContextUsage | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type === "agent") {
      const usage = normalizeChatContextUsage(item.response.contextUsage);
      if (usage) {
        return usage;
      }
    }
  }
  return undefined;
}

function isUnstartedChat(chat: ChatThread): boolean {
  return (
    chat.items.length === 0 &&
    !(chat.title || "").trim() &&
    !(chat.sessionId || "").trim()
  );
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

function compareChatsForSidebar(a: ChatThread, b: ChatThread): number {
  const pinDelta = Number(b.pinned ?? false) - Number(a.pinned ?? false);
  if (pinDelta !== 0) {
    return pinDelta;
  }
  return chatTimeMs(b) - chatTimeMs(a);
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
