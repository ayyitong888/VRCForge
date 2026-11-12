import type { AgentApproval, AgentQuestion, AgentRuntimeRun } from "./api";
import type { ChatThread } from "./chat-types";
import { normalizeProjectPathKey } from "./project-path";
import { approvalIdFromResponse } from "./runtime-parsing";
import { isBlockingAgentQuestion } from "./agent-question-notifications";

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

/** A shared session requires durable Question or origin-turn evidence before projecting activity. */
export function questionContinuationOwner(
  chats: ChatThread[],
  question: Pick<AgentQuestion, "questionId" | "sessionId" | "originClientTurnId" | "originTurnId" | "projectRoot" | "runtimeContinuation">,
): ChatThread | undefined {
  const sessionId = question.runtimeContinuation?.sessionId || question.sessionId;
  if (!sessionId) return undefined;
  const candidates = chats.filter((chat) => chat.sessionId === sessionId
    && (!question.projectRoot || sameProject(chat.projectPath, question.projectRoot)));
  if (candidates.length <= 1) return candidates[0];
  const answered = candidates.filter((chat) => chat.items.some((item) =>
    item.type === "question_answer" && item.questionId === question.questionId));
  if (answered.length) return answered.length === 1 ? answered[0] : undefined;
  const { originClientTurnId, originTurnId } = question;
  const origins = candidates.filter((chat) => chat.items.some((item) => {
    if (item.type === "user") return Boolean(originClientTurnId && item.clientTurnId === originClientTurnId);
    if (item.type !== "agent" || !(originClientTurnId || originTurnId)) return false;
    const response = item.response;
    const responseSessionId = response.sessionId || response.session_id;
    return (!responseSessionId || responseSessionId === sessionId)
      && (!originClientTurnId || response.clientTurnId === originClientTurnId)
      && (!originTurnId || (response.turnId || response.turn_id) === originTurnId);
  }));
  if (origins.length) return origins.length === 1 ? origins[0] : undefined;
  const clientTurnId = question.runtimeContinuation?.clientTurnId;
  if (!clientTurnId) return undefined;
  const streamOwners = candidates.filter((chat) => chat.items.some((item) =>
    item.type === "streaming" && item.clientTurnId === clientTurnId));
  return streamOwners.length === 1 ? streamOwners[0] : undefined;
}

export function pendingQuestionReviewTargets(chats: ChatThread[], questions: AgentQuestion[], activeChatId: string) {
  const targets = questions.filter(isBlockingAgentQuestion).flatMap((question) => {
    const chat = questionContinuationOwner(chats, question);
    if (!chat || chat.archived || chat.items.some((item) => item.type === "question_answer" && item.questionId === question.questionId)) return [];
    return [{ chat, question }];
  });
  return { count: targets.length, target: targets.find(({ chat }) => chat.id === activeChatId) || targets[0] };
}

export function runtimeReviewItemForChat(
  chat: ChatThread | undefined,
  runtimeAttention: AgentRuntimeRun[],
  questions: AgentQuestion[] = [],
) {
  return chat?.items.find((item) => {
    if (item.type !== "agent" || !runtimeAttention.some((run) =>
      (run.clientTurnId && item.response.clientTurnId === run.clientTurnId)
      || (run.turnId && (item.response.turnId || item.response.turn_id) === run.turnId))) return false;
    const reason = item.response.plan?.completionGate?.reason;
    if (reason === "visual_checkpoint_pending") return false;
    if (reason !== "question_pending") {
      if (reason) return true;
      const originTurnId = item.response.turnId || item.response.turn_id;
      // Older continuation receipts lost their completion gate. The exact
      // Question record still owns its pending/answered state.
      return !questions.some((question) => (
        (Boolean(question.originClientTurnId) && question.originClientTurnId === item.response.clientTurnId)
        || (Boolean(question.originTurnId) && question.originTurnId === originTurnId)
      ) && questionBelongsToRuntimeScope(
        question, { sessionId: chat.sessionId, projectRoot: chat.projectPath }, { requireSession: true },
      ));
    }
    // Channel project selection uses the same gate but has no Question card.
    return item.response.steps?.some((step) => step.tool === "vrcforge_request_project_selection"
      && !("historical" in step && step.historical === true)
      && step.result !== null && typeof step.result === "object"
      && "projectSelectionRequested" in step.result && step.result.projectSelectionRequested === true);
  });
}
