import type { ConversationItem } from "./chat-types";

export type SteerDeliveryTraceStage =
  | "submit_received"
  | "submit_direct"
  | "reservation_rejected"
  | "reservation_acquired"
  | "steer_context_resolved"
  | "persist_started"
  | "persist_skipped_existing"
  | "persist_resolved"
  | "persist_rejected"
  | "dispatch_wait_started"
  | "dispatch_wait_resolved"
  | "dispatch_skipped_stop"
  | "rpc_started"
  | "rpc_ack"
  | "rpc_rejected"
  | "rpc_failed";

export type SteerDeliveryTraceEvent = {
  stage: SteerDeliveryTraceStage;
  timestamp: string;
  elapsedMs: number;
  chatId?: string;
  sessionId?: string;
  clientTurnId: string;
  targetClientTurnId?: string;
  waitMs?: number;
  durationMs?: number;
  pendingDispatches?: number;
  accepted?: boolean;
  mode?: string;
  requestedDeliveryMode?: "steer" | "queue";
  errorType?: string;
};

/** Emits only routing IDs and timing/outcome metadata; never include message text or request bodies. */
export function createSteerDeliveryTrace(
  callback: ((event: SteerDeliveryTraceEvent) => void) | undefined,
  identity: Pick<SteerDeliveryTraceEvent, "chatId" | "sessionId" | "clientTurnId">,
): (stage: SteerDeliveryTraceStage, details?: Partial<SteerDeliveryTraceEvent>) => void {
  const startedAt = performance.now();
  return (stage, details = {}) => {
    if (!callback) return;
    try {
    callback({
        stage,
        timestamp: new Date().toISOString(),
        elapsedMs: Math.max(0, Math.round(performance.now() - startedAt)),
        ...identity,
        ...(typeof details.chatId === "string" ? { chatId: details.chatId } : {}),
        ...(typeof details.sessionId === "string" ? { sessionId: details.sessionId } : {}),
        ...(details.targetClientTurnId ? { targetClientTurnId: details.targetClientTurnId } : {}),
        ...(Number.isFinite(details.waitMs) ? { waitMs: Math.max(0, Math.round(details.waitMs!)) } : {}),
        ...(Number.isFinite(details.durationMs) ? { durationMs: Math.max(0, Math.round(details.durationMs!)) } : {}),
        ...(Number.isFinite(details.pendingDispatches) ? { pendingDispatches: Math.max(0, Math.round(details.pendingDispatches!)) } : {}),
        ...(typeof details.accepted === "boolean" ? { accepted: details.accepted } : {}),
        ...(typeof details.mode === "string" ? { mode: details.mode } : {}),
        ...(details.requestedDeliveryMode === "steer" || details.requestedDeliveryMode === "queue"
          ? { requestedDeliveryMode: details.requestedDeliveryMode }
          : {}),
        ...(typeof details.errorType === "string" ? { errorType: details.errorType.slice(0, 64) } : {}),
      });
    } catch {
      // Diagnostics must not change message delivery behavior.
    }
  };
}

export type SteerIntentScope = {
  chatId: string;
  sessionId?: string;
};

type SteerIntent = {
  chatId?: string;
  sessionId?: string;
};

export function settleTerminalSteerDelivery(
  items: ConversationItem[],
  intents: Map<string, SteerIntent>,
  scope: SteerIntentScope,
): { items: ConversationItem[]; settledIds: string[] } {
  const settledIds = [...intents.entries()]
    .filter(([, intent]) => intent.chatId === scope.chatId && intent.sessionId === scope.sessionId)
    .map(([id]) => id);
  if (!settledIds.length) return { items, settledIds };

  const settled = new Set(settledIds);
  for (const id of settledIds) intents.delete(id);
  return {
    settledIds,
    items: items.map((item) => item.type === "user" && settled.has(item.clientTurnId || "")
      ? { ...item, queuedFrom: true, queueStatus: "delivery_unverified" }
      : item),
  };
}
