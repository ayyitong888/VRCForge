import type { AgentApproval, AgentQuestion } from "./api";
import { normalizeProjectPathKey } from "./project-path";

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
