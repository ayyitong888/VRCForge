import type { AgentApproval, AgentGoalDelivery, AgentRuntimeRun } from "./api/types";
import type { ChatThread } from "./chat-types";
import { chatSidebarActivity } from "./sidebar-view";

export type TaskbarAttention = {
  chats: ChatThread[];
  deliveries?: AgentGoalDelivery[];
  approvals?: AgentApproval[];
  externalConfirmationCount?: number;
  pendingQuestionIds: ReadonlySet<string>;
  runs?: AgentRuntimeRun[];
};

export function countTaskbarAttention({
  chats, deliveries = [], approvals = [], pendingQuestionIds, runs, externalConfirmationCount = 0,
}: TaskbarAttention): number {
  const pendingApprovals = new Set(approvals.filter((item) => item.status === "pending").map((item) => item.id));
  const unreadGoalChats = new Set(deliveries
    .filter((item) => !pendingApprovals.has(item.approvalId || "") && !pendingQuestionIds.has(item.questionId || ""))
    .map((item) => item.chatId));
  const unreadChats = chats.filter((chat) => !chat.archived
    && (unreadGoalChats.has(chat.id) || chatSidebarActivity(chat, "", runs) === "completed"));
  return unreadChats.length + pendingApprovals.size + pendingQuestionIds.size + externalConfirmationCount;
}
