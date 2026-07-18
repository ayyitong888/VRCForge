import type { ConversationItem } from "./chat-types";

export const CONTEXT_COMPACTION_PREFIRE_RATIO = 0.75;
export const CONTEXT_AUTO_COMPACT_RATIO = 0.85;
export const CONTEXT_COMPACTION_MAX_TRIGGER_RATIO = 0.9;
export const CONTEXT_COMPACTION_HARD_LIMIT_RATIO = 0.95;

const DEFAULT_MINIMUM_REDUCTION_RATIO = 0.1;
const DEFAULT_MINIMUM_REDUCTION_FLOOR_TOKENS = 1024;
const DEFAULT_POST_COMPACTION_TARGET_RATIO = 0.5;
const DEFAULT_ATTACHMENT_METADATA_TOKENS = 16;
const DEFAULT_FALLBACK_MAX_ENTRIES = 9;
const DEFAULT_FALLBACK_TARGET_CHARACTERS = 6000;
const MAX_DURABLE_STATE_ENTRIES = 24;
const MAX_DURABLE_STATE_FIELD_CHARACTERS = 128;

export type ContextCompactionLevel = "below" | "prefire" | "compact" | "hard-limit";
export type ContextInputTokenSource = "peak" | "last" | "legacy";

export type ContextLimitSource = "provider" | "known" | "unknown";

export type ContextLimitResolution = {
  limit: number;
  known: boolean;
  source: ContextLimitSource;
};

export type ContextModelInfo = {
  inputTokenLimit?: number;
  contextWindow?: number;
  maxInputTokens?: number;
};

export type ContextInputUsage = {
  exact?: boolean;
  provider?: string;
  model?: string;
  peakInputTokens?: number;
  lastInputTokens?: number;
  inputTokens?: number;
  cumulativeInputTokens?: number;
};

export function contextUsageMatchesModel(
  usage: ContextInputUsage | undefined,
  provider: string,
  model: string,
): boolean {
  if (!usage) {
    return false;
  }
  const expectedProvider = provider.trim().toLowerCase();
  const expectedModel = normalizeModelId(model);
  const measuredProvider = String(usage.provider || "").trim().toLowerCase();
  const measuredModel = normalizeModelId(String(usage.model || ""));
  return Boolean(
    expectedProvider
      && expectedModel
      && measuredProvider
      && measuredModel
      && expectedProvider === measuredProvider
      && expectedModel === measuredModel,
  );
}

export type IncomingContextAttachment = {
  name?: string;
  type?: string;
  payloadKind?: string;
  text?: string;
  estimatedTokens?: number;
};

export type CompactionFingerprintAttachment = {
  name?: string;
  type?: string;
  size?: number;
  payloadKind?: string;
  contentDigest?: string;
};

export type CompactionHistoryEntry = {
  role: "user" | "agent";
  text: string;
  attachments?: readonly CompactionFingerprintAttachment[];
};

export function buildDurableCompactionStateEntries(
  items: readonly ConversationItem[],
): CompactionHistoryEntry[] {
  const entries: CompactionHistoryEntry[] = [];
  const append = (parts: Array<string | undefined>) => {
    const text = parts.filter(Boolean).join("; ");
    if (text) {
      entries.push({ role: "agent", text });
    }
  };

  for (const item of items) {
    if (item.type === "subagent") {
      append([
        "Durable sub-agent ownership (result content omitted)",
        field("taskId", item.task.id),
        field("status", item.task.status),
        field("handoff", item.task.handoffStatus),
        field("mergeDecision", item.task.mergeDecision),
      ]);
      continue;
    }
    if (item.type === "result") {
      append([
        "Durable approval state (command output omitted)",
        field("approvalId", item.approvalId),
        field("status", item.error ? "failed" : item.result?.ok ? "completed" : "recorded"),
      ]);
      continue;
    }
    if (item.type === "user" && item.queuedFrom) {
      append([
        "Durable queue handoff",
        field("itemId", item.id),
        "status=materialized",
      ]);
      continue;
    }
    if (item.type !== "agent") {
      continue;
    }
    const response = item.response;
    const shellApproval = response.shell?.approval;
    const writeResult = recordValue(response.write?.result);
    const skillResult = recordValue(response.skill?.result);
    const approvalId = firstBoundedField(
      response.approvalId,
      response.approval_id,
      response.shell?.approvalId,
      response.shell?.approval_id,
      shellApproval?.id,
      response.write?.approvalId,
      response.write?.approval_id,
    );
    const checkpointId = firstBoundedField(
      shellApproval?.checkpoint?.id,
      writeResult?.checkpointId,
      writeResult?.checkpoint_id,
      skillResult?.checkpointId,
      skillResult?.checkpoint_id,
    );
    if (approvalId || checkpointId || response.goalDeliveryId) {
      append([
        "Durable runtime references (payload omitted)",
        field("approvalId", approvalId),
        field("checkpointId", checkpointId),
        field("goalDeliveryId", response.goalDeliveryId),
      ]);
    }
  }
  return entries.slice(-MAX_DURABLE_STATE_ENTRIES);
}

export type PreviousCompactionWindow = {
  sourceDigest?: string;
  contextLimit?: number;
  beforeTokens?: number;
  afterTokens?: number;
  minimumReductionTokens?: number;
  status?: "idle" | "prefire" | "ready" | "compacting" | "applied" | "failed" | "suppressed" | "cancelled";
};

const DUPLICATE_BLOCKING_STATUSES = new Set<NonNullable<PreviousCompactionWindow["status"]>>([
  "ready",
  "compacting",
  "applied",
  "failed",
  "suppressed",
  "cancelled",
]);

export type ContextCompactionPolicyOverrides = {
  triggerRatio?: number;
  minimumReductionRatio?: number;
  minimumReductionFloorTokens?: number;
  targetRatio?: number;
  attachmentMetadataTokens?: number;
};

export type ResolvedContextCompactionPolicy = {
  prefireRatio: number;
  triggerRatio: number;
  maxTriggerRatio: number;
  hardLimitRatio: number;
  minimumReductionRatio: number;
  minimumReductionFloorTokens: number;
  targetRatio: number;
  attachmentMetadataTokens: number;
};

export const DEFAULT_CONTEXT_COMPACTION_POLICY: Readonly<ResolvedContextCompactionPolicy> = Object.freeze({
  prefireRatio: CONTEXT_COMPACTION_PREFIRE_RATIO,
  triggerRatio: CONTEXT_AUTO_COMPACT_RATIO,
  maxTriggerRatio: CONTEXT_COMPACTION_MAX_TRIGGER_RATIO,
  hardLimitRatio: CONTEXT_COMPACTION_HARD_LIMIT_RATIO,
  minimumReductionRatio: DEFAULT_MINIMUM_REDUCTION_RATIO,
  minimumReductionFloorTokens: DEFAULT_MINIMUM_REDUCTION_FLOOR_TOKENS,
  targetRatio: DEFAULT_POST_COMPACTION_TARGET_RATIO,
  attachmentMetadataTokens: DEFAULT_ATTACHMENT_METADATA_TOKENS,
});

export type ContextCompactionBudgetInput = {
  usage?: ContextInputUsage;
  contextLimit: ContextLimitResolution;
  incomingText?: string;
  incomingAttachments?: readonly IncomingContextAttachment[];
  sourceDigest?: string;
  previousCompaction?: PreviousCompactionWindow;
  policy?: ContextCompactionPolicyOverrides;
};

export type ContextCompactionBudgetDecision = {
  level: ContextCompactionLevel;
  shouldCompact: boolean;
  eligible: boolean;
  reason: "below_threshold" | "prefire" | "threshold_reached" | "hard_limit" | "unknown_context_limit" | "usage_unavailable" | "duplicate_source";
  contextLimit: number;
  contextLimitKnown: boolean;
  inputTokenSource?: ContextInputTokenSource;
  observedInputTokens?: number;
  incomingTokens: number;
  projectedTokens?: number;
  projectedRatio?: number;
  triggerTokens?: number;
  hardLimitTokens?: number;
  minimumReductionTokens?: number;
  targetAfterTokens?: number;
  sourceDigest?: string;
  duplicateSource: boolean;
  previousReductionTokens?: number;
  previousReductionSufficient?: boolean;
  policy: ResolvedContextCompactionPolicy;
};

export type BoundedCompactionFallbackOptions = {
  maxEntries?: number;
  targetCharacters?: number;
  primaryGoalLabel?: string;
  recentTurnsLabel?: string;
  userLabel?: string;
  agentLabel?: string;
  omittedLabel?: (count: number) => string;
};

export type BoundedCompactionFallback = {
  schema: "vrcforge.compaction_fallback.v1";
  fidelity: "fallback";
  text: string;
  entries: CompactionHistoryEntry[];
  entryCount: number;
  retainedEntryCount: number;
  omittedEntryCount: number;
  sourceDigest: string;
  primaryGoalIndex: number;
  targetCharacters: number;
  overTarget: boolean;
};

export type RuntimeCompactionProjection = {
  items: ConversationItem[];
  replacedCount: number;
};

export function invalidateCompactedWindowUsage(items: readonly ConversationItem[]): ConversationItem[] {
  return items.map((item) => {
    if (item.type !== "agent" || !item.response.contextUsage) {
      return item;
    }
    const { contextUsage: _staleContextUsage, ...response } = item.response;
    return { ...item, response };
  });
}

export function projectRuntimeCompactionItems(
  items: readonly ConversationItem[],
  summarizedItemIds: ReadonlySet<string>,
  compactItem: Extract<ConversationItem, { type: "compact" }>,
): RuntimeCompactionProjection {
  const projected: ConversationItem[] = [];
  let inserted = false;
  let replacedCount = 0;
  for (const item of items) {
    const summarizedDialogue = summarizedItemIds.has(item.id)
      && (item.type === "user" || item.type === "agent" || item.type === "compact");
    if (!summarizedDialogue) {
      projected.push(item);
      continue;
    }
    replacedCount += 1;
    if (!inserted) {
      projected.push(compactItem);
      inserted = true;
    }
  }
  return replacedCount > 0 ? { items: projected, replacedCount } : { items: [...items], replacedCount: 0 };
}

const KNOWN_MODEL_CONTEXT_LIMITS: Readonly<Record<string, number>> = Object.freeze({
  "deepseek:deepseek-v4-pro": 1_000_000,
  "gemini:gemini-2.5-flash": 1_048_576,
  "gemini:gemini-2.5-pro": 1_048_576,
  "gemini:gemini-2.5-flash-lite": 1_048_576,
  "gemini:gemini-3.5-flash": 1_048_576,
});

export function normalizeContextProvider(provider: string): string {
  const key = provider.trim().toLowerCase();
  if (key.includes("gemini") || key.includes("google") || key.includes("vertex")) {
    return "gemini";
  }
  if (key.includes("anthropic") || key.includes("claude")) {
    return "anthropic";
  }
  if (key.includes("deepseek")) {
    return "deepseek";
  }
  if (key.includes("openai")) {
    return "openai";
  }
  return key || "unknown";
}

export function resolveContextLimit(
  provider: string,
  model: string,
  modelInfo?: ContextModelInfo,
): ContextLimitResolution {
  const providerLimit = firstPositiveNumber(
    modelInfo?.inputTokenLimit,
    modelInfo?.contextWindow,
    modelInfo?.maxInputTokens,
  );
  if (providerLimit !== undefined) {
    return { limit: providerLimit, known: true, source: "provider" };
  }

  const knownLimit = KNOWN_MODEL_CONTEXT_LIMITS[
    `${normalizeContextProvider(provider)}:${normalizeModelId(model)}`
  ];
  if (knownLimit !== undefined) {
    return { limit: knownLimit, known: true, source: "known" };
  }
  return { limit: 0, known: false, source: "unknown" };
}

export function resolveContextInputTokens(
  usage?: ContextInputUsage,
): { tokens: number; source: ContextInputTokenSource } | undefined {
  if (!usage || usage.exact !== true) {
    return undefined;
  }
  const candidates: Array<[ContextInputTokenSource, unknown]> = [
    ["peak", usage.peakInputTokens],
    ["last", usage.lastInputTokens],
    ["legacy", usage.inputTokens],
  ];
  for (const [source, value] of candidates) {
    const tokens = nonNegativeNumber(value);
    if (tokens !== undefined) {
      return { tokens, source };
    }
  }
  return undefined;
}

export function resolveContextCompactionPolicy(
  overrides: ContextCompactionPolicyOverrides = {},
): ResolvedContextCompactionPolicy {
  const triggerRatio = clamp(
    finiteNumber(overrides.triggerRatio) ?? DEFAULT_CONTEXT_COMPACTION_POLICY.triggerRatio,
    CONTEXT_COMPACTION_PREFIRE_RATIO,
    CONTEXT_COMPACTION_MAX_TRIGGER_RATIO,
  );
  const minimumReductionRatio = clamp(
    finiteNumber(overrides.minimumReductionRatio) ?? DEFAULT_CONTEXT_COMPACTION_POLICY.minimumReductionRatio,
    0.01,
    0.5,
  );
  const minimumReductionFloorTokens = Math.max(
    1,
    Math.floor(finiteNumber(overrides.minimumReductionFloorTokens) ?? DEFAULT_CONTEXT_COMPACTION_POLICY.minimumReductionFloorTokens),
  );
  const targetRatio = clamp(
    finiteNumber(overrides.targetRatio) ?? DEFAULT_CONTEXT_COMPACTION_POLICY.targetRatio,
    0.1,
    CONTEXT_COMPACTION_PREFIRE_RATIO,
  );
  const attachmentMetadataTokens = Math.max(
    0,
    Math.ceil(finiteNumber(overrides.attachmentMetadataTokens) ?? DEFAULT_CONTEXT_COMPACTION_POLICY.attachmentMetadataTokens),
  );
  return {
    prefireRatio: CONTEXT_COMPACTION_PREFIRE_RATIO,
    triggerRatio,
    maxTriggerRatio: CONTEXT_COMPACTION_MAX_TRIGGER_RATIO,
    hardLimitRatio: CONTEXT_COMPACTION_HARD_LIMIT_RATIO,
    minimumReductionRatio,
    minimumReductionFloorTokens,
    targetRatio,
    attachmentMetadataTokens,
  };
}

export function estimateTextTokens(text: string): number {
  let units = 0;
  for (const character of text) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (isCjkCodePoint(codePoint)) {
      // Most CJK code points occupy three UTF-8 bytes. Counting one full token
      // is deliberately more conservative than the common bytes / 4 fallback.
      units += 1;
    } else {
      units += utf8ByteLength(codePoint) / 4;
    }
  }
  return Math.ceil(units);
}

export function estimateIncomingContextTokens(
  incomingText = "",
  incomingAttachments: readonly IncomingContextAttachment[] = [],
  attachmentMetadataTokens = DEFAULT_ATTACHMENT_METADATA_TOKENS,
): number {
  let total = estimateTextTokens(incomingText);
  const metadataOverhead = Math.max(0, Math.ceil(attachmentMetadataTokens));
  for (const attachment of incomingAttachments) {
    total += metadataOverhead;
    total += estimateTextTokens(`${attachment.name || ""}\n${attachment.type || ""}\n${attachment.payloadKind || ""}`);
    const explicitEstimate = nonNegativeNumber(attachment.estimatedTokens);
    if (explicitEstimate !== undefined) {
      total += Math.ceil(explicitEstimate);
    } else if (attachment.text) {
      total += estimateTextTokens(attachment.text);
    }
  }
  return total;
}

export function evaluateCompactionBudget(
  input: ContextCompactionBudgetInput,
): ContextCompactionBudgetDecision {
  const policy = resolveContextCompactionPolicy(input.policy);
  const limit = input.contextLimit.known ? positiveNumber(input.contextLimit.limit) : undefined;
  const incomingTokens = estimateIncomingContextTokens(
    input.incomingText,
    input.incomingAttachments,
    policy.attachmentMetadataTokens,
  );
  if (limit === undefined) {
    return baseUnavailableDecision("unknown_context_limit", input, policy, incomingTokens);
  }

  const observed = resolveContextInputTokens(input.usage);
  if (!observed) {
    return baseUnavailableDecision("usage_unavailable", input, policy, incomingTokens, limit);
  }

  const projectedTokens = observed.tokens + incomingTokens;
  const projectedRatio = projectedTokens / limit;
  const triggerTokens = Math.ceil(limit * policy.triggerRatio);
  const hardLimitTokens = Math.ceil(limit * policy.hardLimitRatio);
  const minimumReductionTokens = Math.max(
    policy.minimumReductionFloorTokens,
    Math.ceil(limit * policy.minimumReductionRatio),
  );
  const targetAfterTokens = Math.max(
    0,
    Math.min(
      Math.floor(limit * policy.targetRatio),
      projectedTokens - minimumReductionTokens,
    ),
  );
  const level = compactionLevel(projectedRatio, policy);
  const sameWindow = input.previousCompaction?.contextLimit === limit;
  const previousStatus = input.previousCompaction?.status;
  const duplicateSource = Boolean(
    input.sourceDigest &&
      sameWindow &&
      input.previousCompaction?.sourceDigest === input.sourceDigest &&
      previousStatus &&
      DUPLICATE_BLOCKING_STATUSES.has(previousStatus),
  );
  const previousReductionTokens = reductionTokens(input.previousCompaction);
  const previousMinimumReduction = input.previousCompaction?.minimumReductionTokens ?? minimumReductionTokens;
  const previousReductionSufficient = previousReductionTokens === undefined
    ? undefined
    : previousReductionTokens >= previousMinimumReduction;
  const thresholdReached = level === "compact" || level === "hard-limit";
  const eligible = thresholdReached && !duplicateSource;

  return {
    level,
    shouldCompact: eligible,
    eligible,
    reason: duplicateSource
      ? "duplicate_source"
      : level === "hard-limit"
        ? "hard_limit"
        : level === "compact"
          ? "threshold_reached"
          : level === "prefire"
            ? "prefire"
            : "below_threshold",
    contextLimit: limit,
    contextLimitKnown: true,
    inputTokenSource: observed.source,
    observedInputTokens: observed.tokens,
    incomingTokens,
    projectedTokens,
    projectedRatio,
    triggerTokens,
    hardLimitTokens,
    minimumReductionTokens,
    targetAfterTokens,
    sourceDigest: input.sourceDigest,
    duplicateSource,
    previousReductionTokens,
    previousReductionSufficient,
    policy,
  };
}

export function fingerprintCompactionSource(
  history: readonly CompactionHistoryEntry[],
): string {
  // This is a synchronous browser-safe change detector for stale/duplicate
  // work. It is deliberately not a cryptographic integrity or signature hash;
  // controllers must pair it with the persisted chat revision for CAS.
  let first = 0x811c9dc5;
  let second = 0x9e3779b9;
  let logicalLength = 0;
  const write = (value: string) => {
    logicalLength += value.length;
    for (let index = 0; index < value.length; index += 1) {
      const code = value.charCodeAt(index);
      first = Math.imul(first ^ (code & 0xff), 0x01000193) >>> 0;
      first = Math.imul(first ^ (code >>> 8), 0x01000193) >>> 0;
      second = Math.imul(second ^ code, 0x85ebca6b) >>> 0;
      second ^= second >>> 13;
    }
  };
  history.forEach((entry, entryIndex) => {
    write(`${entryIndex}\u001f${entry.role}\u001f${entry.text.length}\u001f${entry.text}\u001e`);
    (entry.attachments || []).forEach((attachment, attachmentIndex) => {
      write(
        `${attachmentIndex}\u001f${attachment.name || ""}\u001f${attachment.type || ""}\u001f` +
          `${attachment.size ?? ""}\u001f${attachment.payloadKind || ""}\u001f${attachment.contentDigest || ""}\u001e`,
      );
    });
  });
  return `ctx1-${history.length.toString(36)}-${logicalLength.toString(36)}-${hex32(first)}${hex32(second)}`;
}

export function buildBoundedCompactionFallback(
  history: readonly CompactionHistoryEntry[],
  options: BoundedCompactionFallbackOptions = {},
): BoundedCompactionFallback {
  const normalized = history
    .map((entry, sourceIndex) => ({ entry: { ...entry }, sourceIndex }))
    .filter(({ entry }) => (entry.role === "user" || entry.role === "agent") && entry.text.trim().length > 0);
  const maxEntries = Math.max(1, Math.floor(options.maxEntries ?? DEFAULT_FALLBACK_MAX_ENTRIES));
  const targetCharacters = Math.max(1, Math.floor(options.targetCharacters ?? DEFAULT_FALLBACK_TARGET_CHARACTERS));
  const firstUser = normalized.find((item) => item.entry.role === "user");
  const primary = firstUser || normalized[0];
  const primaryGoalIndex = primary?.sourceIndex ?? -1;
  const selectedSourceIndexes = new Set<number>();
  let selectedCharacters = 0;
  if (primary) {
    selectedSourceIndexes.add(primary.sourceIndex);
    selectedCharacters += renderedEntryCost(primary.entry);
  }

  const completeTurns = collectCompleteTurns(normalized);
  for (let index = completeTurns.length - 1; index >= 0; index -= 1) {
    const additional = completeTurns[index].filter((item) => !selectedSourceIndexes.has(item.sourceIndex));
    if (!additional.length) {
      continue;
    }
    const nextEntryCount = selectedSourceIndexes.size + additional.length;
    const nextCharacterCount = selectedCharacters + additional.reduce(
      (sum, item) => sum + renderedEntryCost(item.entry),
      0,
    );
    if (nextEntryCount > maxEntries || nextCharacterCount > targetCharacters) {
      continue;
    }
    for (const item of additional) {
      selectedSourceIndexes.add(item.sourceIndex);
    }
    selectedCharacters = nextCharacterCount;
  }

  const entries = normalized
    .filter((item) => selectedSourceIndexes.has(item.sourceIndex))
    .map((item) => item.entry);
  const omittedEntryCount = normalized.length - entries.length;
  const primaryGoalLabel = options.primaryGoalLabel ?? "Primary goal";
  const recentTurnsLabel = options.recentTurnsLabel ?? "Recent complete turns";
  const userLabel = options.userLabel ?? "User";
  const agentLabel = options.agentLabel ?? "Agent";
  const omittedLabel = options.omittedLabel ?? ((count: number) => `[${count} earlier entries omitted]`);
  const lines: string[] = [];
  if (primary) {
    lines.push(`${primaryGoalLabel}:`);
  }
  entries.forEach((entry, index) => {
    if (primary && index === 1) {
      lines.push(`${recentTurnsLabel}:`);
    }
    lines.push(`${entry.role === "user" ? userLabel : agentLabel}: ${entry.text}`);
  });
  if (omittedEntryCount > 0) {
    lines.push(omittedLabel(omittedEntryCount));
  }
  const text = lines.join("\n");
  return {
    schema: "vrcforge.compaction_fallback.v1",
    fidelity: "fallback",
    text,
    entries,
    entryCount: normalized.length,
    retainedEntryCount: entries.length,
    omittedEntryCount,
    sourceDigest: fingerprintCompactionSource(normalized.map((item) => item.entry)),
    primaryGoalIndex,
    targetCharacters,
    overTarget: text.length > targetCharacters,
  };
}

function baseUnavailableDecision(
  reason: "unknown_context_limit" | "usage_unavailable",
  input: ContextCompactionBudgetInput,
  policy: ResolvedContextCompactionPolicy,
  incomingTokens: number,
  knownLimit = 0,
): ContextCompactionBudgetDecision {
  return {
    level: "below",
    shouldCompact: false,
    eligible: false,
    reason,
    contextLimit: knownLimit,
    contextLimitKnown: knownLimit > 0,
    incomingTokens,
    sourceDigest: input.sourceDigest,
    duplicateSource: false,
    policy,
  };
}

function compactionLevel(
  ratio: number,
  policy: ResolvedContextCompactionPolicy,
): ContextCompactionLevel {
  if (ratio >= policy.hardLimitRatio) {
    return "hard-limit";
  }
  if (ratio >= policy.triggerRatio) {
    return "compact";
  }
  if (ratio >= policy.prefireRatio) {
    return "prefire";
  }
  return "below";
}

function collectCompleteTurns(
  entries: Array<{ entry: CompactionHistoryEntry; sourceIndex: number }>,
): Array<Array<{ entry: CompactionHistoryEntry; sourceIndex: number }>> {
  const turns: Array<Array<{ entry: CompactionHistoryEntry; sourceIndex: number }>> = [];
  let current: Array<{ entry: CompactionHistoryEntry; sourceIndex: number }> = [];
  for (const item of entries) {
    if (item.entry.role === "user") {
      if (current.some((part) => part.entry.role === "agent")) {
        turns.push(current);
      }
      current = [item];
    } else if (current.length > 0) {
      current.push(item);
    }
  }
  if (current.some((part) => part.entry.role === "agent")) {
    turns.push(current);
  }
  return turns;
}

function renderedEntryCost(entry: CompactionHistoryEntry): number {
  return entry.text.length + 16;
}

function reductionTokens(previous?: PreviousCompactionWindow): number | undefined {
  const before = nonNegativeNumber(previous?.beforeTokens);
  const after = nonNegativeNumber(previous?.afterTokens);
  return before === undefined || after === undefined ? undefined : Math.max(0, before - after);
}

function normalizeModelId(model: string): string {
  const value = model.trim().toLowerCase();
  const modelsPathIndex = value.lastIndexOf("/models/");
  if (modelsPathIndex >= 0) {
    return value.slice(modelsPathIndex + "/models/".length);
  }
  return value.replace(/^models\//, "");
}

function firstPositiveNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    const number = positiveNumber(value);
    if (number !== undefined) {
      return number;
    }
  }
  return undefined;
}

function positiveNumber(value: unknown): number | undefined {
  const number = finiteNumber(value);
  return number !== undefined && number > 0 ? number : undefined;
}

function nonNegativeNumber(value: unknown): number | undefined {
  const number = finiteNumber(value);
  return number !== undefined && number >= 0 ? number : undefined;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function recordValue(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function firstBoundedField(...values: unknown[]): string | undefined {
  for (const value of values) {
    const bounded = boundedStateField(value);
    if (bounded) {
      return bounded;
    }
  }
  return undefined;
}

function field(name: string, value: unknown): string | undefined {
  const bounded = boundedStateField(value);
  return bounded ? `${name}=${bounded}` : undefined;
}

function boundedStateField(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const normalized = value.trim();
  if (
    !normalized
    || normalized.length > MAX_DURABLE_STATE_FIELD_CHARACTERS
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(normalized)
    || /^avtr_/i.test(normalized)
    || /^(?:sk-|gh[pousr]_|github_pat_|hf_|xox[baprs]-)/i.test(normalized)
  ) {
    return undefined;
  }
  return normalized;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

function isCjkCodePoint(codePoint: number): boolean {
  return (
    (codePoint >= 0x2e80 && codePoint <= 0x9fff) ||
    (codePoint >= 0xf900 && codePoint <= 0xfaff) ||
    (codePoint >= 0x20000 && codePoint <= 0x2fa1f) ||
    (codePoint >= 0x3040 && codePoint <= 0x30ff) ||
    (codePoint >= 0x31f0 && codePoint <= 0x31ff) ||
    (codePoint >= 0x1100 && codePoint <= 0x11ff) ||
    (codePoint >= 0xac00 && codePoint <= 0xd7af)
  );
}

function utf8ByteLength(codePoint: number): number {
  if (codePoint <= 0x7f) return 1;
  if (codePoint <= 0x7ff) return 2;
  if (codePoint <= 0xffff) return 3;
  return 4;
}

function hex32(value: number): string {
  return (value >>> 0).toString(16).padStart(8, "0");
}
