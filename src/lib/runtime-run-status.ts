/** Runtime lifecycle states shared by transcript recovery and the run ledger. */
const ACTIVE_RUNTIME_RUN_STATUSES = new Set([
  "running", "starting", "queued", "claimed", "cancelling", "cancel_requested",
  "waiting_for_model", "waiting_for_tool", "in_progress", "applying", "dispatching",
]);

export function isActiveRuntimeRunStatus(status?: string): boolean {
  return ACTIVE_RUNTIME_RUN_STATUSES.has(String(status || "").trim().toLowerCase());
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
