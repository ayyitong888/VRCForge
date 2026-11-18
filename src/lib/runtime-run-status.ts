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
