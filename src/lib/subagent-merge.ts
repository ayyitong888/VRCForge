import type { SubAgentTask } from "./api/sub-agents";

// 合并审查是终态语义：completed 且尚未落 adopted/dismissed 的任务等待用户审查。
export function isAwaitingMergeReview(task: SubAgentTask): boolean {
  return task.status === "completed" && !task.mergeDecision;
}

export function isMergedAdopted(task: SubAgentTask): boolean {
  return task.mergeDecision === "adopted";
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

export function subAgentAdoptedHistoryText(task: SubAgentTask): string {
  if (!isMergedAdopted(task)) {
    return "";
  }
  const identity = task.displayName || task.role || task.id;
  const summary = subAgentResultSummaryText(task);
  const proposedNextAction = subAgentProposedNextAction(task);
  let result = "";
  if (task.result) {
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
    result ? `Result payload:\n${result}` : "",
    proposedNextAction ? `Proposed next action:\n${proposedNextAction}` : "",
  ]
    .filter(Boolean)
    .join("\n\n");
  if (text.length <= ADOPTED_HISTORY_MAX_CHARS) {
    return text;
  }
  return `${text.slice(0, ADOPTED_HISTORY_MAX_CHARS)}\n[Adopted result truncated for chat context]`;
}
