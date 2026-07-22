import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export const MEMORY_REVIEW_MODES = [
  "off",
  "shadow",
  "suggest_only",
  "bounded_background",
  "auto_safe",
] as const;

export type MemoryReviewMode = (typeof MEMORY_REVIEW_MODES)[number];
export type MemoryReviewScope = "user" | "project";
export type MemoryReviewCandidateAction = "accept" | "reject" | "defer" | "erase" | "undo" | "read";
export type MemoryReviewCandidateState =
  | "proposed"
  | "accepted"
  | "rejected"
  | "deferred"
  | "expired"
  | "conflicting";

export type MemoryReviewUsage = {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  costUsd?: number;
  costUnavailableReason?: string;
  attempts?: number;
  costUpperBoundUsd?: number;
  costAccounting?: "bounded_retry" | "retry_usage_unavailable";
};

export type MemoryReviewProviderDisclosure = {
  paidRun: boolean;
  provider?: string;
  providerLabel?: string;
  model?: string;
  cadenceMinutes?: number;
  inputCharCap?: number;
  tokenCap?: number;
  costCapUsd?: number;
  inputCostPerMillionUsd?: number;
  outputCostPerMillionUsd?: number;
  privacyScope?: MemoryReviewScope;
  activeConfigMatches?: boolean;
};

export type MemoryReviewRunStatus = {
  state:
    | "idle"
    | "queued"
    | "scanning"
    | "provider_call"
    | "persisting"
    | "completed"
    | "failed"
    | "cancelled"
    | (string & {});
  phase?: string;
  startedAt?: string;
  completedAt?: string;
  failureLabel?: string;
  failureClass?: string;
  attempt?: number;
  deferredReason?: string;
  nextRetryAt?: string;
};

export type MemoryReviewRunBudget = {
  inputCharCap?: number;
  tokenCap?: number;
  costCapUsd?: number;
  inputCostPerMillionUsd?: number;
  outputCostPerMillionUsd?: number;
};

export type MemoryReviewLastRun = {
  runId?: string;
  status?: string;
  startedAt?: string;
  completedAt?: string;
  eligibleCount?: number;
  candidateCount?: number;
  provider?: string;
  model?: string;
  budget?: MemoryReviewRunBudget;
  phase?: string;
  failureClass?: string;
  attempt?: number;
  nonConsuming?: boolean;
  deferredReason?: string;
  nextRetryAt?: string;
  usage?: MemoryReviewUsage;
};

export type MemoryReviewShadowSummary = {
  schema?: string;
  scope?: MemoryReviewScope;
  projectRoot?: string;
  eligibleCount: number;
  sourceTypeCounts?: Record<string, number>;
  reasonCounts?: Record<string, number>;
  scannedAt?: string;
  revision?: number;
};

export type MemoryReviewCandidate = {
  candidateId: string;
  scope: MemoryReviewScope;
  kind: string;
  proposedText: string;
  state: MemoryReviewCandidateState;
  policyVersion: string;
  evidenceCount: number;
  firstObservedAt?: string;
  lastObservedAt?: string;
  conflictCount?: number;
  conflictExplanation?: "none" | "candidate" | "accepted_memory" | "mixed";
  confidenceScore?: number;
  sourceTypeCounts?: Record<string, number>;
  unread?: boolean;
  eraseOnly?: boolean;
  runId?: string;
  provider?: string;
  model?: string;
  usage?: MemoryReviewUsage;
};

export type MemoryReviewSnapshot = {
  ok?: boolean;
  schema: string;
  mode: MemoryReviewMode;
  policyVersion: string;
  revision: number;
  scope: MemoryReviewScope;
  projectRoot?: string;
  requestedProjectRoot?: string;
  configuredProjectMatches?: boolean;
  cadenceMinutes: number;
  inputCharCap: number;
  tokenCap: number;
  costCapUsd: number;
  inputCostPerMillionUsd: number;
  outputCostPerMillionUsd: number;
  retentionDays: number;
  provider?: string;
  model?: string;
  runStatus: MemoryReviewRunStatus;
  unreadCount: number;
  candidates: MemoryReviewCandidate[];
  providerDisclosure: MemoryReviewProviderDisclosure;
  usage?: MemoryReviewUsage;
  nextRunAt?: string;
  lastRun?: MemoryReviewLastRun;
  shadowSummary?: MemoryReviewShadowSummary;
};

export type MemoryReviewConfigMutation = {
  mode: MemoryReviewMode;
  cadenceMinutes: number;
  inputCharCap: number;
  tokenCap: number;
  costCapUsd: number;
  inputCostPerMillionUsd: number;
  outputCostPerMillionUsd: number;
  retentionDays: number;
  provider: string;
  model: string;
  scope: MemoryReviewScope;
  projectRoot?: string;
  expectedRevision: number;
};

export type MemoryReviewRunMutation = {
  scope: MemoryReviewScope;
  projectRoot?: string;
  expectedRevision: number;
};

export type MemoryReviewCancelMutation = {
  runId: string;
};

export type MemoryReviewCandidateMutation = {
  expectedRevision: number;
  projectRoot?: string;
  editedText?: string;
};

export function normalizeMemoryReviewMode(value: unknown): MemoryReviewMode {
  const normalized = String(value || "").trim().toLowerCase();
  return (MEMORY_REVIEW_MODES as readonly string[]).includes(normalized)
    ? normalized as MemoryReviewMode
    : "off";
}

export async function fetchMemoryReviewSnapshot(
  endpoint: string,
  params: { scope?: MemoryReviewScope; projectRoot?: string; signal?: AbortSignal } = {},
): Promise<MemoryReviewSnapshot> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<MemoryReviewSnapshot>("fetch_agent_memory_review", {
      request: {
        scope: params.scope,
        projectRoot: params.projectRoot,
        timeoutMs: 30_000,
      },
    }, params.signal);
  }
  const query = new URLSearchParams();
  if (params.scope) {
    query.set("scope", params.scope);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  return requestJson<MemoryReviewSnapshot>(`${endpoint}/api/app/agent/memory/review${suffix}`, {
    signal: params.signal,
    timeoutMs: 30_000,
  });
}

export async function updateMemoryReviewConfig(
  endpoint: string,
  payload: MemoryReviewConfigMutation,
  signal?: AbortSignal,
): Promise<MemoryReviewSnapshot> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<MemoryReviewSnapshot>("update_agent_memory_review", {
      request: { body: payload, timeoutMs: 30_000 },
    }, signal);
  }
  return requestJson<MemoryReviewSnapshot>(`${endpoint}/api/app/agent/memory/review/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
    timeoutMs: 30_000,
  });
}

export async function runMemoryReview(
  endpoint: string,
  payload: MemoryReviewRunMutation,
  signal?: AbortSignal,
): Promise<MemoryReviewSnapshot> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<MemoryReviewSnapshot>("run_agent_memory_review", {
      request: { body: payload, timeoutMs: 1_200_000 },
    }, signal);
  }
  return requestJson<MemoryReviewSnapshot>(`${endpoint}/api/app/agent/memory/review/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
    timeoutMs: 1_200_000,
  });
}

export async function cancelMemoryReviewRun(
  endpoint: string,
  payload: MemoryReviewCancelMutation,
  signal?: AbortSignal,
): Promise<MemoryReviewSnapshot> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<MemoryReviewSnapshot>("cancel_agent_memory_review", {
      request: { body: payload, timeoutMs: 30_000 },
    }, signal);
  }
  return requestJson<MemoryReviewSnapshot>(`${endpoint}/api/app/agent/memory/review/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
    timeoutMs: 30_000,
  });
}

export async function mutateMemoryReviewCandidate(
  endpoint: string,
  candidateId: string,
  action: MemoryReviewCandidateAction,
  payload: MemoryReviewCandidateMutation,
  signal?: AbortSignal,
): Promise<MemoryReviewSnapshot> {
  const encodedId = encodeURIComponent(candidateId);
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<MemoryReviewSnapshot>("mutate_agent_memory_review_candidate", {
      request: {
        id: candidateId,
        action,
        body: payload,
        timeoutMs: action === "erase" ? 60_000 : 30_000,
      },
    }, signal);
  }
  return requestJson<MemoryReviewSnapshot>(
    `${endpoint}/api/app/agent/memory/review/candidates/${encodedId}/${action}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
      timeoutMs: action === "erase" ? 60_000 : 30_000,
    },
  );
}
