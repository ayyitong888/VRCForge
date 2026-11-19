import type { ChatThread } from "./chat-types";

type TitleChatState = Pick<ChatThread, "title" | "titleSource">;

/** The independent title request is eligible only for an untouched first turn. */
export function shouldGenerateChatTitle(
  chat: TitleChatState | undefined,
  baseItemCount: number,
  background: boolean,
): boolean {
  return !background
    && baseItemCount === 0
    && !chat?.title
    && chat?.titleSource !== "manual";
}

/** Title delivery belongs to submitted input and is independent of the Agent result. */
export function canRequestChatTitleAfterTurn(response: {
  status?: string;
  turnStatus?: { status?: string };
  dispatchStopped?: boolean;
}): boolean {
  const untitledStatuses = ["cancelled", "canceled", "interrupted", "blocked", "needs_user_action"];
  return !response.dispatchStopped
    && !untitledStatuses.includes(response.status || "")
    && !untitledStatuses.includes(response.turnStatus?.status || "");
}

/** Apply a result only while the auto fallback still owns the title. */
export function applyGeneratedChatTitle<T extends TitleChatState>(
  chat: T,
  fallbackTitle: string,
  generatedTitle: string,
): T {
  if (!generatedTitle.trim() || chat.titleSource === "manual" || chat.title !== fallbackTitle) {
    return chat;
  }
  return { ...chat, title: generatedTitle.trim(), titleSource: "auto" };
}
