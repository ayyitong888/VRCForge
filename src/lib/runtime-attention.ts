import { invoke } from "@tauri-apps/api/core";
import type { AgentRuntimeResponse, AgentRuntimeRun } from "./api/types";
import { playNotificationSoundOnce } from "./notification-sound-delivery";

export type RuntimeAttention = { sessionId: string; clientTurnId: string; turnId: string };
const STORAGE_KEY = "vrcforge.runtime-attention.notifications.v1";

/** A later turn supersedes the earlier turn's attention, including while running. */
export function latestRuntimeAttentionCandidates(runs: AgentRuntimeRun[]): AgentRuntimeRun[] {
  const latest = new Map<string, AgentRuntimeRun>();
  const time = (run: AgentRuntimeRun) => Date.parse(run.startedAt || run.createdAt || run.updatedAt || "") || 0;
  for (const run of runs) {
    if (!run.sessionId || !run.clientTurnId || !run.turnId) continue;
    const prior = latest.get(run.sessionId);
    if (!prior || time(run) > time(prior)) latest.set(run.sessionId, run);
  }
  return [...latest.values()].filter(run => run.status === "blocked");
}

/** Verify the exact terminal receipt; a tool failure inside an active run is not attention. */
export function runtimeAttentionFromResponse(run: AgentRuntimeRun, response: AgentRuntimeResponse): RuntimeAttention | null {
  if (run.status !== "blocked" || !run.sessionId || !run.clientTurnId || !run.turnId
    || (response.sessionId || response.session_id) !== run.sessionId
    || response.clientTurnId !== run.clientTurnId
    || (response.turnId || response.turn_id) !== run.turnId) return null;
  const gate = response.plan?.completionGate;
  if (gate?.status !== "needs_user_action" || gate.reason !== "verification_required") return null;
  return { sessionId: run.sessionId, clientTurnId: run.clientTurnId, turnId: run.turnId };
}

/** Reuses the existing display-only native toast. It focuses the app, never approves or answers. */
export function createRuntimeAttentionDelivery() {
  const inflight = new Set<string>();
  const delivered = new Set<string>();
  return async (endpoint: string, attention: RuntimeAttention, title: string, body: string, current: () => boolean): Promise<void> => {
    const key = JSON.stringify([endpoint, attention.sessionId, attention.clientTurnId]);
    try {
      const saved: unknown = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]");
      if (Array.isArray(saved)) saved.filter(v => typeof v === "string").forEach(v => delivered.add(v));
    } catch { /* In-memory deduplication remains available. */ }
    if (delivered.has(key) || inflight.has(key) || !current()) return;
    inflight.add(key);
    try {
      const silent = await playNotificationSoundOnce(`runtime-attention:${key}`, "blocking");
      if (!current()) return;
      await invoke("show_background_goal_notification", { request: { title, body, silent } });
      delivered.add(key);
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...delivered])); } catch { /* Keep memory. */ }
    } finally { inflight.delete(key); }
  };
}
