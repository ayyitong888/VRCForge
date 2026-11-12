import type { AgentApproval, AgentQuestion } from "./api";
import type { ChatThread } from "./chat-types";
import { normalizeProjectPathKey } from "./project-path";
import { approvalIdFromResponse } from "./runtime-parsing";

export type RuntimeScope = {
  sessionId: string;
  projectRoot: string;
};

function sameProject(left?: string, right?: string): boolean {
  return normalizeProjectPathKey(left) === normalizeProjectPathKey(right);
}

export function approvalBelongsToRuntimeScope(
  approval: AgentApproval,
  scope: RuntimeScope,
  options: { requireSession?: boolean } = {},
): boolean {
  const sessionId = scope.sessionId.trim();
  const taskSessionId = approval.taskContext?.sessionId?.trim() || "";
  if (options.requireSession) {
    if (!sessionId || !taskSessionId || taskSessionId !== sessionId) return false;
  }
  if (!sameProject(approval.projectRoot, scope.projectRoot)) return false;
  return true;
}

export function pendingApprovalOwner(approval: AgentApproval, chats: ChatThread[]): ChatThread | undefined {
  if (approval.status !== "pending") return undefined;
  const taskSessionId = approval.taskContext?.sessionId?.trim();
  const candidates = chats.filter((chat) => approvalBelongsToRuntimeScope(
    approval,
    { sessionId: chat.sessionId, projectRoot: chat.projectPath },
    { requireSession: Boolean(taskSessionId) },
  ));
  if (taskSessionId && candidates.length === 1) return candidates[0];

  const receiptOwners = candidates.filter((chat) => chat.items.some((item) => (
    item.type === "agent" && approvalIdFromResponse(item.response) === approval.id
  )));
  return receiptOwners.length === 1 ? receiptOwners[0] : undefined;
}

export function questionBelongsToRuntimeScope(
  question: AgentQuestion,
  scope: RuntimeScope,
  options: { requireSession?: boolean } = {},
): boolean {
  const sessionId = scope.sessionId.trim();
  const questionSessionId = question.sessionId?.trim() || "";
  if (options.requireSession) {
    if (!sessionId || !questionSessionId || questionSessionId !== sessionId) return false;
  }
  return sameProject(question.projectRoot, scope.projectRoot);
}

export function filterQuestionsForRuntimeScope(
  questions: AgentQuestion[],
  scope: RuntimeScope,
): AgentQuestion[] {
  if (!scope.sessionId.trim()) return [];
  return questions.filter((question) => questionBelongsToRuntimeScope(question, scope, { requireSession: true }));
}
