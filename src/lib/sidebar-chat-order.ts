import type { ChatThread } from "./chat-types";

export const SIDEBAR_CHAT_ORDER_KEY = "vrcforge_sidebar_chat_order";

export function loadSidebarChatOrder(): string[] {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(SIDEBAR_CHAT_ORDER_KEY) || "[]");
    return Array.isArray(value)
      ? [...new Set(value.filter((id): id is string => typeof id === "string" && id.trim().length > 0))]
      : [];
  } catch {
    return [];
  }
}

// The input is already grouped and sorted by conversation time. Display order
// must not edit conversation timestamps, project membership or pin state.
export function orderSidebarChats(chats: ChatThread[], order: readonly string[]): ChatThread[] {
  const ranks = new Map(order.map((id, index) => [id, index]));
  return [...chats].sort((a, b) => {
    const pinned = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
    if (pinned) return pinned;
    const aRank = ranks.get(a.id);
    const bRank = ranks.get(b.id);
    if (aRank === undefined && bRank === undefined) return 0;
    return (aRank ?? Number.MAX_SAFE_INTEGER) - (bRank ?? Number.MAX_SAFE_INTEGER);
  });
}

export function mergeSidebarChatOrder(previous: readonly string[], ids: string[]): string[] {
  const moved = [...new Set(ids)];
  const movedSet = new Set(moved);
  return [...moved, ...previous.filter((id) => !movedSet.has(id))];
}
