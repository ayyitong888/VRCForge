/** Runtime lifecycle states shared by transcript recovery and the run ledger. */
const ACTIVE_RUNTIME_RUN_STATUSES = new Set([
  "running", "starting", "queued", "claimed", "cancelling", "cancel_requested",
  "waiting_for_model", "waiting_for_tool", "in_progress", "applying", "dispatching",
]);

export function isActiveRuntimeRunStatus(status?: string): boolean {
  return ACTIVE_RUNTIME_RUN_STATUSES.has(String(status || "").trim().toLowerCase());
}
