import type { ChatCompactionState, ChatThread } from "./chat-types";

const MAX_COMPACTION_LATENCY_MS = 24 * 60 * 60 * 1_000;
const MAX_COMPACTION_SUMMARY_CHARACTERS = 100_000;
const MAX_COMPACTION_ATTEMPTS = 16;

export type RevisionedChatUpdate = {
  applied: boolean;
  chat: ChatThread;
};

export function normalizeChatRevision(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

export function applyRevisionedChatUpdate(
  chat: ChatThread,
  expectedRevision: number | undefined,
  updater: (chat: ChatThread) => ChatThread,
): RevisionedChatUpdate {
  const currentRevision = normalizeChatRevision(chat.revision);
  if (expectedRevision !== undefined && currentRevision !== expectedRevision) {
    return { applied: false, chat };
  }
  const updated = updater(chat);
  if (updated === chat) {
    return { applied: false, chat };
  }
  return {
    applied: true,
    chat: { ...updated, revision: currentRevision + 1 },
  };
}

export function normalizeRestoredCompaction(
  value: unknown,
  now = new Date().toISOString(),
): ChatCompactionState | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const state = value as ChatCompactionState;
  const validStatuses = new Set<ChatCompactionState["status"]>([
    "idle",
    "prefire",
    "ready",
    "compacting",
    "applied",
    "failed",
    "suppressed",
    "cancelled",
  ]);
  if (!validStatuses.has(state.status)) {
    return undefined;
  }
  const normalized = normalizeCompactionTelemetry(state);
  if (normalized.status === "prefire" || normalized.status === "ready" || normalized.status === "compacting") {
    return {
      ...normalized,
      status: "failed",
      failureClass: "interrupted",
      message: "Compaction was interrupted before it could replace the chat history.",
      completedAt: now,
    };
  }
  return normalized;
}

export function restoredCompactionRequiresPersistence(value: unknown): boolean {
  if (!value || typeof value !== "object") {
    return false;
  }
  const status = (value as { status?: unknown }).status;
  return status === "prefire" || status === "ready" || status === "compacting";
}

export function boundedCompactionLatencyMs(
  startedAt: string | undefined,
  completedAt: string,
): number | undefined {
  const started = Date.parse(startedAt || "");
  const completed = Date.parse(completedAt);
  if (!Number.isFinite(started) || !Number.isFinite(completed) || completed < started) {
    return undefined;
  }
  return Math.min(MAX_COMPACTION_LATENCY_MS, Math.round(completed - started));
}

export function boundedCompactionSummaryCharacters(value: unknown): number | undefined {
  return boundedTelemetryInteger(value, MAX_COMPACTION_SUMMARY_CHARACTERS);
}

export function boundedCompactionAttempts(value: unknown): number | undefined {
  return boundedTelemetryInteger(value, MAX_COMPACTION_ATTEMPTS);
}

function boundedTelemetryInteger(value: unknown, maximum: number): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    return undefined;
  }
  return Math.min(maximum, Math.round(value));
}

function normalizeCompactionTelemetry(state: ChatCompactionState): ChatCompactionState {
  const prefireOutcome = state.prefireOutcome === "hit" || state.prefireOutcome === "waste"
    ? state.prefireOutcome
    : undefined;
  const suppressionReason = typeof state.suppressionReason === "string"
    ? state.suppressionReason.trim().slice(0, 80) || undefined
    : undefined;
  return {
    ...state,
    attempts: boundedCompactionAttempts(state.attempts),
    latencyMs: boundedTelemetryInteger(state.latencyMs, MAX_COMPACTION_LATENCY_MS),
    retainedSummaryCharacters: boundedCompactionSummaryCharacters(state.retainedSummaryCharacters),
    prefireOutcome,
    suppressionReason,
  };
}
