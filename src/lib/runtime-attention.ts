import { invoke } from "@tauri-apps/api/core";
import type { AgentRuntimeResponse, AgentRuntimeRun, AgentTurnResponseRecovery } from "./api/types";
import { playNotificationSoundOnce } from "./notification-sound-delivery";

export type RuntimeAttentionStatus = "completed" | "blocked" | "failed" | "cancelled" | "stopped";
export type RuntimeAttention = { sessionId: string; clientTurnId: string; turnId: string; status: RuntimeAttentionStatus };

function attentionStatus(status: string | undefined): RuntimeAttentionStatus | null {
  if (status === "denied") return "failed";
  return status === "completed" || status === "blocked" || status === "failed" || status === "cancelled" || status === "stopped" ? status : null;
}
const STORAGE_KEY = "vrcforge.runtime-attention.notifications.v1";

/** All watched terminal turns must deliver, even if a newer turn already runs. */
export function runtimeAttentionCandidates(runs: AgentRuntimeRun[]): AgentRuntimeRun[] {
  return runs.filter(run => run.sessionId && run.clientTurnId && run.turnId && attentionStatus(run.status) !== null);
}

/** A later turn supersedes the earlier turn's attention, including while running. */
export function latestRuntimeAttentionCandidates(runs: AgentRuntimeRun[]): AgentRuntimeRun[] {
  const latest = new Map<string, AgentRuntimeRun>();
  const time = (run: AgentRuntimeRun) => Date.parse(run.startedAt || run.createdAt || run.updatedAt || "") || 0;
  for (const run of runs) {
    if (!run.sessionId || !run.clientTurnId || !run.turnId) continue;
    const prior = latest.get(run.sessionId);
    if (!prior || time(run) > time(prior)) latest.set(run.sessionId, run);
  }
  return runtimeAttentionCandidates([...latest.values()]);
}

/** Durable runtime ledger state survives backend restarts; notification delivery
 * must not depend on the in-memory response recovery cache. */
export function runtimeAttentionFromRun(run: AgentRuntimeRun): RuntimeAttention | null {
  const status = attentionStatus(run.status);
  if (!status || !run.sessionId || !run.clientTurnId || !run.turnId) return null;
  return { sessionId: run.sessionId, clientTurnId: run.clientTurnId, turnId: run.turnId, status };
}

/** Verify the exact terminal receipt; a tool failure inside an active run is not attention. */
export function runtimeAttentionFromResponse(run: AgentRuntimeRun, response: AgentRuntimeResponse): RuntimeAttention | null {
  const status = attentionStatus(run.status);
  if (!status || !run.sessionId || !run.clientTurnId || !run.turnId
    || (response.sessionId || response.session_id) !== run.sessionId
    || response.clientTurnId !== run.clientTurnId
    || (response.turnId || response.turn_id) !== run.turnId) return null;
  // The run ledger owns lifecycle state. A failed tool or visual check is not
  // human blockage; only an actual blocked run gets critical delivery.
  return { sessionId: run.sessionId, clientTurnId: run.clientTurnId, turnId: run.turnId, status };
}

/** Transport failures can end before a model response exists. The exact recovery
 * envelope still proves the terminal client turn; never infer this from a tool. */
export function runtimeAttentionFromTurnState(run: AgentRuntimeRun, state: AgentTurnResponseRecovery): RuntimeAttention | null {
  if (!state.ok || !["completed", "failed"].includes(state.status)
    || state.sessionId !== run.sessionId || state.clientTurnId !== run.clientTurnId) return null;
  if (state.response) return runtimeAttentionFromResponse(run, state.response);
  const status = attentionStatus(run.status);
  if (state.status !== "failed" || !status || status === "completed" || status === "blocked"
    || !run.sessionId || !run.clientTurnId || !run.turnId) return null;
  return { sessionId: run.sessionId, clientTurnId: run.clientTurnId, turnId: run.turnId, status };
}

/** Reuses the existing display-only native toast. It focuses the app, never approves or answers. */
export function createRuntimeAttentionDelivery() {
  const inflight = new Set<string>();
  const delivered = new Set<string>();
  return async (endpoint: string, attention: RuntimeAttention, title: string, body: string, current: () => boolean): Promise<void> => {
    const key = JSON.stringify([endpoint, attention.sessionId, attention.clientTurnId, attention.turnId, attention.status]);
    try {
      const saved: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
      if (Array.isArray(saved)) saved.filter(v => typeof v === "string").forEach(v => delivered.add(v));
    } catch { /* In-memory deduplication remains available. */ }
    if (delivered.has(key) || inflight.has(key) || !current()) return;
    inflight.add(key);
    try {
      const silent = await playNotificationSoundOnce(`runtime-attention:${key}`, attention.status === "blocked" ? "blocking" : "ordinary");
      if (!current()) return;
      await invoke("show_background_goal_notification", { request: { title, body, silent } });
      delivered.add(key);
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...delivered])); } catch { /* Keep memory. */ }
    } finally { inflight.delete(key); }
  };
}
