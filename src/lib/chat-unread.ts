import type { AgentGoalDelivery } from "./api/types";
import type { ChatThread, ConversationItem } from "./chat-types";

type AgentItem = Extract<ConversationItem, { type: "agent" }>;

export function chatReplyMarker(item: AgentItem): string {
  return item.completedAt || item.response.completedAt || item.createdAt || item.id;
}

function isChatReply(item: ConversationItem): item is AgentItem {
  if (item.type !== "agent") return false;
  const { plan, status } = item.response;
  return Boolean(plan?.reply?.trim() || plan?.publicMessages?.some((message) => message.text.trim())
    || status === "completed" || plan?.nextStep === "done" || plan?.nextStep === "completed");
}

export function latestChatReplyMarker(chat: ChatThread): string | undefined {
  for (let index = chat.items.length - 1; index >= 0; index--) {
    const item = chat.items[index];
    if (isChatReply(item)) return chatReplyMarker(item);
  }
}

function markerTime(marker: string): number {
  return /^\d{4}-\d{2}-\d{2}T/.test(marker) ? Date.parse(marker) : NaN;
}

export function countUnreadChatReplies(chats: ChatThread[], deliveries: AgentGoalDelivery[] = []): number {
  const backgroundItemIds = new Map<string, Set<string>>();
  const deliveryIds = new Set<string>();
  for (const { deliveryId, chatId, agentItemId } of deliveries) {
    deliveryIds.add(deliveryId);
    const items = backgroundItemIds.get(chatId) || new Set<string>();
    items.add(agentItemId);
    backgroundItemIds.set(chatId, items);
  }
  let count = deliveryIds.size;
  for (const chat of chats) {
    const viewed = chat.lastViewedAt || "";
    const viewedTime = markerTime(viewed);
    let viewedIndex = viewed ? chat.items.length - 1 : -1;
    while (viewedIndex >= 0) {
      const item = chat.items[viewedIndex];
      if (item.type === "agent" && chatReplyMarker(item) === viewed) break;
      viewedIndex--;
    }
    const backgroundItems = backgroundItemIds.get(chat.id);
    for (let index = viewedIndex + 1; index < chat.items.length; index++) {
      const item = chat.items[index];
      if (isChatReply(item) && !backgroundItems?.has(item.id)
        && !(markerTime(chatReplyMarker(item)) <= viewedTime)) count++;
    }
  }
  return count;
}
