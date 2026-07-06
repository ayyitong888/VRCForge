import type { TFunction } from "i18next";
import type { ChatThread } from "./chat-types";
import { formatChatSidebarTime, groupSidebarChats, type ChatSidebarGroups } from "./chat-thread";

export type SidebarChatGroupsWithTimes = ChatSidebarGroups & {
  times: Map<string, string>;
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
): SidebarChatGroupsWithTimes {
  return {
    ...groupSidebarChats(chats, normalizeProjectPath),
    times: new Map(chats.map((chat) => [chat.id, formatChatSidebarTime(chat, nowMs, language)])),
  };
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
