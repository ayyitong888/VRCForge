import type { ChatCompactionState, ChatThread } from "./chat-types";
import type { AgentContextUsage } from "./api";

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
  preserveActive = false,
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
  if (!preserveActive && (normalized.status === "prefire" || normalized.status === "ready" || normalized.status === "compacting")) {
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

/**
 * Add one compact operation to the durable compact ledger. The backend should
 * already have folded page/retry receipts for that operation; this function
 * therefore runs once per compact response and never estimates missing tokens.
 */
export function mergeCompactionProviderUsage(
  prior: AgentContextUsage | undefined,
  current: AgentContextUsage | undefined,
): AgentContextUsage | undefined {
  if (!prior && !current) return undefined;
  if (!prior) return { ...current };
  if (!current) {
    return {
      ...prior,
      exact: false,
      partial: true,
      coverage: markCoverageIncomplete(prior.coverage),
      cacheCoverage: markCacheCoverageIncomplete(prior.cacheCoverage),
      unavailableReason: prior.unavailableReason || "provider_usage_missing",
    };
  }
  const result: AgentContextUsage = {
    exact: prior.exact === true && current.exact === true,
  };
  for (const key of ["inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens"] as const) {
    const left = prior[key];
    const right = current[key];
    if (typeof left === "number" && Number.isFinite(left) && left >= 0
      && typeof right === "number" && Number.isFinite(right) && right >= 0) {
      result[key] = left + right;
    } else if (typeof left === "number" && Number.isFinite(left) && left >= 0) {
      result[key] = left;
    } else if (typeof right === "number" && Number.isFinite(right) && right >= 0) {
      result[key] = right;
    }
  }
  const priorRequestCount = nonNegativeInteger(prior.requestCount);
  const currentRequestCount = nonNegativeInteger(current.requestCount);
  if (priorRequestCount !== undefined && currentRequestCount !== undefined) {
    result.requestCount = priorRequestCount + currentRequestCount;
  } else if (priorRequestCount !== undefined || currentRequestCount !== undefined) {
    result.requestCount = priorRequestCount ?? currentRequestCount;
  }
  if (prior.cacheUsageComplete !== undefined || current.cacheUsageComplete !== undefined) {
    result.cacheUsageRequestCount = (nonNegativeInteger(prior.cacheUsageRequestCount) || 0)
      + (nonNegativeInteger(current.cacheUsageRequestCount) || 0);
    result.cacheUsageComplete = prior.cacheUsageComplete === true && current.cacheUsageComplete === true;
  }
  const coverage = mergeCoverage(prior.coverage, current.coverage, priorRequestCount, currentRequestCount);
  if (coverage) result.coverage = coverage;
  const cacheCoverage = mergeCacheCoverage(prior.cacheCoverage, current.cacheCoverage, priorRequestCount, currentRequestCount);
  if (cacheCoverage) result.cacheCoverage = cacheCoverage;
  const partial = prior.partial === true || current.partial === true
    || Object.values(coverage || {}).some((entry) => entry.complete !== true)
    || Boolean(cacheCoverage && (cacheCoverage.read?.complete !== true || cacheCoverage.write?.complete !== true));
  if (partial || coverage !== undefined || cacheCoverage !== undefined) result.partial = partial;
  if (result.exact !== true) {
    result.unavailableReason = prior.unavailableReason || current.unavailableReason || "provider_usage_incomplete";
  }
  for (const key of ["provider", "providerLabel", "model"] as const) {
    if (current[key] || prior[key]) result[key] = current[key] || prior[key];
  }
  return result;
}

function nonNegativeInteger(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function markCoverageIncomplete(
  coverage: AgentContextUsage["coverage"],
): AgentContextUsage["coverage"] {
  if (!coverage) return undefined;
  return Object.fromEntries(Object.entries(coverage).map(([field, entry]) => [field, {
    ...entry,
    complete: false,
  }]));
}

function markCacheCoverageIncomplete(
  coverage: AgentContextUsage["cacheCoverage"],
): AgentContextUsage["cacheCoverage"] {
  if (!coverage) return undefined;
  return {
    ...coverage,
    read: coverage.read ? { ...coverage.read, complete: false } : undefined,
    write: coverage.write ? { ...coverage.write, complete: false } : undefined,
  };
}

function mergeCoverage(
  prior: AgentContextUsage["coverage"],
  current: AgentContextUsage["coverage"],
  priorRequestCount: number | undefined,
  currentRequestCount: number | undefined,
): AgentContextUsage["coverage"] {
  const fields = new Set([...Object.keys(prior || {}), ...Object.keys(current || {})]);
  if (!fields.size) return undefined;
  const result: NonNullable<AgentContextUsage["coverage"]> = {};
  for (const field of fields) {
    const left = prior?.[field];
    const right = current?.[field];
    const leftRequestCount = nonNegativeInteger(left?.requestCount) ?? priorRequestCount;
    const rightRequestCount = nonNegativeInteger(right?.requestCount) ?? currentRequestCount;
    const requestCount = leftRequestCount !== undefined && rightRequestCount !== undefined
      ? leftRequestCount + rightRequestCount : leftRequestCount ?? rightRequestCount;
    const leftReported = nonNegativeInteger(left?.reportedRequestCount);
    const rightReported = nonNegativeInteger(right?.reportedRequestCount);
    const reportedRequestCount = leftReported !== undefined && rightReported !== undefined
      ? leftReported + rightReported : leftReported ?? rightReported;
    result[field] = {
      ...(requestCount === undefined ? {} : { requestCount }),
      ...(reportedRequestCount === undefined ? {} : { reportedRequestCount }),
      complete: left?.complete === true && right?.complete === true
        && requestCount !== undefined && reportedRequestCount !== undefined
        && reportedRequestCount === requestCount,
    };
  }
  return result;
}

function mergeCacheCoverage(
  prior: AgentContextUsage["cacheCoverage"],
  current: AgentContextUsage["cacheCoverage"],
  priorRequestCount: number | undefined,
  currentRequestCount: number | undefined,
): AgentContextUsage["cacheCoverage"] {
  if (!prior && !current) return undefined;
  const priorCount = nonNegativeInteger(prior?.requestCount) ?? priorRequestCount;
  const currentCount = nonNegativeInteger(current?.requestCount) ?? currentRequestCount;
  const requestCount = priorCount !== undefined && currentCount !== undefined
    ? priorCount + currentCount : priorCount ?? currentCount;
  const read = mergeCoverage(
    prior?.read ? { cacheReadTokens: prior.read } : undefined,
    current?.read ? { cacheReadTokens: current.read } : undefined,
    priorRequestCount,
    currentRequestCount,
  )?.cacheReadTokens;
  const write = mergeCoverage(
    prior?.write ? { cacheWriteTokens: prior.write } : undefined,
    current?.write ? { cacheWriteTokens: current.write } : undefined,
    priorRequestCount,
    currentRequestCount,
  )?.cacheWriteTokens;
  return {
    ...(requestCount === undefined ? {} : { requestCount }),
    ...(read ? { read } : {}),
    ...(write ? { write } : {}),
  };
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
