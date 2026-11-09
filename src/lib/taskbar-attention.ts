import { isBlockingAgentQuestion } from "./agent-question-notifications";
import type { AgentApproval, AgentGoalDelivery, AgentQuestion, AgentRuntimeRun } from "./api/types";
import type { ChatThread } from "./chat-types";
import { chatSidebarActivity } from "./sidebar-view";

export type TaskbarAttention = {
  chats: ChatThread[];
  deliveries?: AgentGoalDelivery[];
  approvals?: AgentApproval[];
  questions?: AgentQuestion[];
  runs?: AgentRuntimeRun[];
};

export function countTaskbarAttention({ chats, deliveries = [], approvals = [], questions = [], runs }: TaskbarAttention): number {
  const pendingApprovals = new Set(approvals.filter((item) => item.status === "pending").map((item) => item.id));
  const pendingQuestions = new Set(questions.filter(isBlockingAgentQuestion).map((item) => item.questionId));
  const unreadGoalChats = new Set(deliveries
    .filter((item) => !pendingApprovals.has(item.approvalId || "") && !pendingQuestions.has(item.questionId || ""))
    .map((item) => item.chatId));
  const unreadChats = chats.filter((chat) => !chat.archived
    && (unreadGoalChats.has(chat.id) || chatSidebarActivity(chat, "", runs) === "completed"));
  return unreadChats.length + pendingApprovals.size + pendingQuestions.size;
}
