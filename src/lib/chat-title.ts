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

/** Failed, cancelled, or planner-rejected turns must not create a title request. */
export function chatTitleRunSucceeded(response: {
  ok?: boolean;
  status?: string;
  plan?: { plannerFailure?: unknown };
}): boolean {
  return response.ok === true
    && !response.plan?.plannerFailure
    && !["failed", "cancelled", "blocked", "needs_user_action"].includes(response.status || "");
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
