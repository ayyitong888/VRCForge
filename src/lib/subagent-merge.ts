import type { SubAgentTask } from "./api/sub-agents";
import type { ConversationItem } from "./chat-types";

// A task already routed to the parent runtime has its own delivery lifecycle.
export function canAdoptSubAgentResult(task: SubAgentTask): boolean {
  return task.status === "completed" && !task.mergeDecision && !task.parentContinuationStatus;
}

export function canDismissSubAgentResult(task: SubAgentTask): boolean {
  return (task.status === "completed" || task.status === "failed" || task.status === "paused")
    && !task.mergeDecision && !task.parentContinuationStatus;
}

export function canCancelSubAgentTask(task: SubAgentTask): boolean {
  return task.status === "queued" || task.status === "running";
}

export function canRetrySubAgentTask(task: SubAgentTask): boolean {
  return ["completed", "failed", "cancelled", "interrupted"].includes(task.status);
}

export function canResumeSubAgentTask(task: SubAgentTask): boolean {
  return task.canResume === true && !["queued", "running", "cancelling"].includes(task.status);
}

export function isAwaitingMergeReview(task: SubAgentTask): boolean {
  return task.status !== "paused" && (canAdoptSubAgentResult(task) || canDismissSubAgentResult(task));
}

export function isMergedAdopted(task: SubAgentTask): boolean {
  return task.mergeDecision === "adopted" && !task.parentContinuationStatus;
}

export function isMergedDismissed(task: SubAgentTask): boolean {
  return task.mergeDecision === "dismissed";
}

function resultText(task: SubAgentTask, key: string): string {
  const result = task.result;
  if (!result || typeof result !== "object") {
    return "";
  }
  const value = (result as Record<string, unknown>)[key];
  return typeof value === "string" ? value.trim() : "";
}

// worker envelope 里的建议下一步（sub_agent_delegate 各角色都会带 proposedNextAction）。
export function subAgentProposedNextAction(task: SubAgentTask): string {
  return resultText(task, "proposedNextAction");
}

export function subAgentResultSummaryText(task: SubAgentTask): string {
  return resultText(task, "summaryText") || (task.summary ?? "").trim();
}

const ADOPTED_HISTORY_MAX_CHARS = 8_000;

export function subAgentAdoptedHistoryItem(task: SubAgentTask): Extract<ConversationItem, { type: "subagent" }> | undefined {
  return isMergedAdopted(task) ? { id: `subagent-${task.id}`, type: "subagent", task } : undefined;
}

export function subAgentAdoptedHistoryText(task: SubAgentTask): string {
  if (!isMergedAdopted(task)) {
    return "";
  }
  const identity = task.displayName || task.role || task.id;
  const summary = subAgentResultSummaryText(task);
  const proposedNextAction = subAgentProposedNextAction(task);
  let result = resultText(task, "finalReport");
  if (!result && task.result) {
    try {
      result = JSON.stringify(task.result, null, 2);
    } catch {
      result = String(task.result);
    }
  }
  const text = [
    `[Adopted sub-agent result: ${identity}]`,
    task.task ? `Delegated task:\n${task.task}` : "",
    summary ? `Reviewed summary:\n${summary}` : "",
    result ? `Result:\n${result}` : "",
    proposedNextAction ? `Proposed next action:\n${proposedNextAction}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");
  if (text.length <= ADOPTED_HISTORY_MAX_CHARS) {
    return text;
  }
  return `${text.slice(0, ADOPTED_HISTORY_MAX_CHARS)}\n[Adopted result truncated for chat context]`;
}
