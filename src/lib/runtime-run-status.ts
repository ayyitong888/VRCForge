import type { AgentRuntimeResponse, AgentRuntimeRun } from "./api/types";
import type { SubAgentTask } from "./api/sub-agents";

/** Runtime lifecycle states shared by transcript recovery and the run ledger. */
const ACTIVE_RUNTIME_RUN_STATUSES = new Set([
  "running", "starting", "queued", "claimed", "cancelling", "cancel_requested",
  "waiting_for_model", "waiting_for_tool", "in_progress", "applying", "dispatching",
]);

export function isActiveRuntimeRunStatus(status?: string): boolean {
  return ACTIVE_RUNTIME_RUN_STATUSES.has(String(status || "").trim().toLowerCase());
}

/** Older restart receipts share a source for interrupted work and pending decisions. */
export function isInterruptedRuntimeTurn(response: {
  continuationSource?: string; status?: string; plan?: { taskCompletion?: Record<string, unknown> };
}): boolean {
  if (response.plan?.taskCompletion?.status === "waiting_user") return false;
  return response.status === "interrupted" || response.continuationSource === "foreground_interrupted";
}

export function isWaitingForSubAgent(state: { nextStep?: string; reason?: string }): boolean {
  return state.nextStep === "waiting_for_tool" && state.reason === "sub_agent_pending";
}

export function isAutomaticToolWait(state: { nextStep?: string; reason?: string }, shellStatus?: string): boolean {
  return isWaitingForSubAgent(state) || (state.nextStep === "waiting_for_tool"
    && (!state.reason || state.reason === "action_not_terminal") && shellStatus === "running");
}

export function matchesParentTurn(owner: { clientTurnId?: string; turnId?: string }, client?: string, turn?: string) {
  if (client && owner.clientTurnId && client !== owner.clientTurnId) return false;
  if (turn && owner.turnId && turn !== owner.turnId) return false;
  return Boolean((turn && turn === owner.turnId) || (client && client === owner.clientTurnId));
}

export type AutomaticWaitStage = "waiting" | "handoff" | "shell";

export function automaticTurnWaitStage(
  response: AgentRuntimeResponse, tasks: readonly SubAgentTask[], runs: readonly AgentRuntimeRun[],
): AutomaticWaitStage | null {
  if (response.plan.nextStep !== "waiting_for_tool" || response.dispatchStopped) return null;
  const owner = { clientTurnId: response.clientTurnId, turnId: response.turnId || response.turn_id };
  const session = response.sessionId || response.session_id;
  // Older shell receipts kept the process status only on the exact ledger run.
  const ledger = runs.find(run => session && run.sessionId === session && matchesParentTurn(owner, run.clientTurnId, run.turnId));
  const plan = { nextStep: response.plan.nextStep, reason: response.plan.reason || ledger?.reason };
  if (!isAutomaticToolWait(plan, response.shell?.status || ledger?.shellStatus)) return null;
  if (!isWaitingForSubAgent(plan)) return "shell";
  const children = tasks.filter(task => (!task.parentSessionId || !session || task.parentSessionId === session)
    && matchesParentTurn(owner, task.parentClientTurnId, task.parentTurnId));
  if (!children.length || children.some(task => ["queued", "running", "cancelling"].includes(task.status))) return "waiting";
  if (children.every(task => task.parentContinuationStatus === "interrupted")) return null;
  return "handoff";
}

/** Resolve activity only when this session/CID identifies one ledger run. */
export function runtimeTurnActivityStatus(
  runs: ReadonlyArray<{ sessionId?: string; clientTurnId?: string; status?: string }>,
  sessionId?: string,
  clientTurnId?: string,
): boolean | undefined {
  if (!sessionId || !clientTurnId) return undefined;
  const matching = runs.filter((run) => run.sessionId === sessionId && run.clientTurnId === clientTurnId);
  return matching.length === 1 ? isActiveRuntimeRunStatus(matching[0].status) : undefined;
}
