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
