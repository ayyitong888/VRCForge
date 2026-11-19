import type { AgentProgress, AgentRuntimeRun } from "./api/types";
import { projectProcessRounds } from "./chat-process-rounds";
import type { ChatThread, ConversationItem } from "./chat-types";
import { isActiveRuntimeRunStatus } from "./runtime-run-status";

/** A saved current step also survives failed turns and unrelated follow-up messages. */
export function activeRuntimeTodoIds(
  progress: AgentProgress[],
  chat: ChatThread | null | undefined,
  runs: AgentRuntimeRun[],
  foregroundClientTurnId?: string,
): Set<string> {
  const active = new Set<string>();
  if (!chat || !progress.length) return active;
  const streams: Extract<ConversationItem, { type: "streaming" }>[] = [];
  const intervals: Array<[number, number]> = [];
  for (const item of chat.items) {
    if (item.type !== "streaming" || item.stopRequested || item.phase === "waiting_for_approval"
      || (item.sessionId && item.sessionId !== chat.sessionId)) continue;
    const run = runs.find(run => run.sessionId === chat.sessionId && run.clientTurnId === item.clientTurnId
      && (!item.turnId || !run.turnId || run.turnId === item.turnId));
    if (!run && item.clientTurnId !== foregroundClientTurnId) continue;
    if (run && (run.dispatchStopped || run.completedAt || !isActiveRuntimeRunStatus(run.status)
      || ["queued", "cancelling", "cancel_requested"].includes(run.status || ""))) continue;
    streams.push(item);
    intervals.push([Date.parse(run?.startedAt || item.createdAt || ""), Infinity]);
  }
  if (!streams.length) return active;

  // A resumed child/approval turn may still be working on a step selected by its parent.
  if (streams.some(stream => stream.parentTurnId || stream.parentClientTurnId || stream.continuationSource)) {
    const { rounds } = projectProcessRounds(chat.items, chat.sessionId);
    const liveRounds = new Set(streams.map(stream => rounds.get(stream)).filter(Boolean));
    for (const item of chat.items) {
      if (item.type !== "agent" || !liveRounds.has(rounds.get(item))) continue;
      intervals.push([
        Date.parse(item.response.startedAt || item.createdAt || ""),
        Date.parse(item.response.completedAt || item.completedAt || ""),
      ]);
    }
  }
  for (const item of progress) {
    if (item.sessionId !== chat.sessionId || !["in_progress", "running"].includes(item.status || "")) continue;
    const selectedAt = Date.parse(item.updatedAt || item.createdAt || "");
    if (intervals.some(([start, end]) => start <= selectedAt && selectedAt <= end)) active.add(item.progressId);
  }
  return active;
}
