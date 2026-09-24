import type { TFunction } from "i18next";
import type { ChatThread, ConversationItem } from "./chat-types";
import { formatChatSidebarTime, groupSidebarChats, type ChatSidebarGroups } from "./chat-thread";

export type SidebarChatActivity = "running" | "completed";

export type SidebarChatGroupsWithTimes = ChatSidebarGroups & {
  times: Map<string, string>;
  activityByChat: Map<string, SidebarChatActivity>;
};

export type SidebarEmptyProjectState = {
  name: string;
  meta: string;
} | null;

export function buildChatSidebarView(
  chats: ChatThread[],
  language: string,
  normalizeProjectPath: (value: string) => string,
  nowMs = Date.now(),
  visibleChatId = "",
): SidebarChatGroupsWithTimes {
  return {
    ...groupSidebarChats(chats, normalizeProjectPath),
    times: new Map(chats.map((chat) => [chat.id, formatChatSidebarTime(chat, nowMs, language)])),
    activityByChat: new Map(
      chats
        .map((chat) => [chat.id, chatSidebarActivity(chat, visibleChatId)] as const)
        .filter((entry): entry is readonly [string, SidebarChatActivity] => Boolean(entry[1])),
    ),
  };
}

export function chatLatestAgentMarker(chat: ChatThread): string | undefined {
  let latestUserIndex = -1;
  for (let index = chat.items.length - 1; index >= 0; index -= 1) {
    if (chat.items[index]?.type === "user") {
      latestUserIndex = index;
      break;
    }
  }
  if (latestUserIndex < 0) return undefined;
  for (let index = chat.items.length - 1; index > latestUserIndex; index -= 1) {
    const item = chat.items[index];
    if (item?.type === "agent") return item.createdAt || item.id;
  }
  return undefined;
}

export function chatSidebarActivity(chat: ChatThread, activeChatId = ""): SidebarChatActivity | undefined {
  if (chat.items.some((item) => item.type === "streaming")) {
    return "running";
  }
  if (chat.id === activeChatId) return undefined;

  let latestUserIndex = -1;
  for (let index = chat.items.length - 1; index >= 0; index -= 1) {
    if (chat.items[index]?.type === "user") {
      latestUserIndex = index;
      break;
    }
  }
  if (latestUserIndex < 0) {
    return undefined;
  }

  let latestAgent: Extract<ConversationItem, { type: "agent" }> | undefined;
  for (let index = chat.items.length - 1; index > latestUserIndex; index -= 1) {
    const item = chat.items[index];
    if (item?.type === "agent") {
      latestAgent = item;
      break;
    }
  }
  if (!latestAgent) {
    return undefined;
  }

  const response = latestAgent.response;
  const responseStatus = String(response.status || "").trim().toLowerCase();
  const nextStep = String(response.plan?.nextStep || "").trim().toLowerCase();
  const pending = response.shell?.status === "pending_approval"
    || Boolean(response.choicePrompt)
    || ["pending_approval", "needs_user_action", "pending", "waiting", "question", "approval_pending"].includes(responseStatus)
    || ["pending_approval", "needs_user_action", "pending", "waiting", "question", "approval_pending"].includes(nextStep);
  const failed = ["failed", "error", "cancelled", "rejected", "denied", "interrupted"].includes(responseStatus)
    || ["failed", "error", "cancelled", "rejected", "denied", "interrupted"].includes(nextStep);
  const completed = responseStatus === "completed" || ["done", "completed"].includes(nextStep);
  if (pending || failed || response.ok === false || !completed) {
    return undefined;
  }
  const marker = chatLatestAgentMarker(chat);
  if (marker && marker === chat.lastViewedAt) return undefined;
  const viewedAt = Date.parse(chat.lastViewedAt || "");
  const markerAt = Date.parse(marker || "");
  if (Number.isFinite(viewedAt) && Number.isFinite(markerAt) && markerAt <= viewedAt) return undefined;
  return "completed";
}

export function buildEmptyProjectState({
  t,
  projectCount,
  loading,
  error,
  hasStartupIssue,
  runtimeConnected,
}: {
  t: TFunction;
  projectCount: number;
  loading: boolean;
  error: string;
  hasStartupIssue: boolean;
  runtimeConnected: boolean;
}): SidebarEmptyProjectState {
  if (projectCount > 0) {
    return null;
  }
  if (loading && !error) {
    return { name: t("agent.emptyProjectState.scanning"), meta: "wait" };
  }
  if (hasStartupIssue || !runtimeConnected) {
    return { name: t("agent.modeLabel.notConnected"), meta: "retry" };
  }
  if (error) {
    return { name: t("agent.emptyProjectState.refreshFailed"), meta: "retry" };
  }
  return { name: t("agent.emptyProjectState.noUnityProject"), meta: "empty" };
}
