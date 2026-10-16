import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { compactAgentHistory, type AgentContextUsage, type ChatHistoryEntry, type CompactAgentHistoryResponse } from "../lib/api";
import type { ChatCompactionState, ChatThread, ConversationItem } from "../lib/chat-types";
import {
  boundedCompactionAttempts,
  boundedCompactionLatencyMs,
  boundedCompactionSummaryCharacters,
  mergeCompactionProviderUsage,
} from "../lib/chat-compaction-state";
import { stripTransientConversationItems } from "../lib/chat-thread";
import {
  collectCompactedAttachmentReferences,
  mergeCompactedAttachmentReferences,
} from "../lib/attachment-payloads";
import {
  estimateIncomingContextTokens,
  estimateTextTokens,
  contextUsageMatchesModel,
  buildDurableCompactionStateEntries,
  evaluateCompactionBudget,
  fingerprintCompactionSource,
  contextUsageBoundaryItemId,
  mergeCompactionRecovery,
  resolveContextLimit,
  type ContextCompactionBudgetDecision,
} from "../lib/context-compaction";
import { buildChatHistory, latestAgentContextUsage } from "../lib/conversation-utils";
import type { PrepareTurnContextInput, PreparedTurnContext } from "./use-chat-run-controller";

type CompactTrigger = "manual" | "auto";
type CompactPhase = "standalone" | "pre_turn" | "mid_turn";

type CompactRequest = {
  chatId: string;
  endpoint: string;
  trigger: CompactTrigger;
  phase: CompactPhase;
  provider?: string;
  model?: string;
  contextLimit?: number;
  incomingText?: string;
  incomingAttachments?: PrepareTurnContextInput["turn"]["attachments"];
  signal?: AbortSignal;
};

type CompactOutcome = {
  status: "skipped" | "applied" | "failed" | "cancelled";
  decision?: ContextCompactionBudgetDecision;
  chat?: ChatThread;
};

type UseContextCompactionControllerParams = {
  getChatById: (chatId: string) => ChatThread | undefined;
  updateChat: (chatId: string, updater: (chat: ChatThread) => ChatThread) => boolean;
  updateChatIfRevision: (
    chatId: string,
    expectedRevision: number | undefined,
    updater: (chat: ChatThread) => ChatThread,
  ) => boolean;
  persistChatsNow: () => Promise<void>;
  setError: (message: string) => void;
  hasUnresolvedRuntimeState?: (chatId: string, sessionId: string) => boolean;
};

type ActiveCompactionJob = {
  generation: string;
  controller: AbortController;
};

export function useContextCompactionController({
  getChatById,
  updateChat,
  updateChatIfRevision,
  persistChatsNow,
  setError,
  hasUnresolvedRuntimeState,
}: UseContextCompactionControllerParams) {
  const { t, i18n } = useTranslation();
  const jobsRef = useRef(new Map<string, ActiveCompactionJob>());
  const hasUnresolvedRuntimeStateRef = useRef(hasUnresolvedRuntimeState);
  hasUnresolvedRuntimeStateRef.current = hasUnresolvedRuntimeState;
  const [activeJobCount, setActiveJobCount] = useState(0);

  function syncActiveJobCount() {
    setActiveJobCount(jobsRef.current.size);
  }

  function cancelCompaction(chatId: string) {
    jobsRef.current.get(chatId)?.controller.abort();
  }

  async function compactChat(request: CompactRequest): Promise<CompactOutcome> {
    if (jobsRef.current.has(request.chatId)) {
      return { status: "skipped" };
    }
    const snapshot = getChatById(request.chatId);
    if (!snapshot) {
      return { status: "skipped" };
    }
    if (hasUnresolvedRuntimeStateRef.current?.(request.chatId, snapshot.sessionId)) {
      return { status: "skipped" };
    }
    const snapshotItems = stripTransientConversationItems(snapshot.items);
    const history = [
      ...buildChatHistory(snapshotItems, t),
      ...buildDurableCompactionStateEntries(snapshotItems),
    ];
    if (!history.length) {
      if (request.trigger === "manual") {
        setError(t("compact.noContent"));
      }
      return { status: "skipped" };
    }

    const snapshotRevision = normalizeRevision(snapshot.revision);
    const projectedDigest = fingerprintCompactionSource([
      ...history,
      ...(request.incomingText || request.incomingAttachments?.length
        ? [{
            role: "user" as const,
            text: request.incomingText || "",
            attachments: (request.incomingAttachments || []).map((attachment) => ({
              name: attachment.name,
              type: attachment.type,
              size: attachment.size,
              payloadKind: attachment.payloadKind,
            })),
          }]
        : []),
    ]);
    const contextLimit = request.contextLimit && request.contextLimit > 0
      ? { limit: request.contextLimit, known: true, source: "provider" as const }
      : resolveContextLimit(request.provider || "", request.model || "");
    const measuredUsage = latestAgentContextUsage(snapshotItems)
      || (snapshotItems.some((item) => item.type === "compact") ? undefined : snapshot.contextUsageCache);
    const usage = contextUsageMatchesModel(measuredUsage, request.provider || "", request.model || "")
      ? measuredUsage
      : undefined;
    let decision = evaluateCompactionBudget({
      usage,
      contextLimit,
      incomingText: request.incomingText,
      incomingAttachments: request.incomingAttachments,
      sourceDigest: projectedDigest,
      previousCompaction: snapshot.compaction,
    });

    if (request.trigger === "manual") {
      // Manual compaction reduces the visible model-history projection, not
      // the provider's schema/system overhead or ten percent of its capacity.
      // Compare before/after in the same units; keep automatic guard policy.
      const projectedTokens = estimateHistoryTokens(history)
        + estimateIncomingContextTokens(request.incomingText, request.incomingAttachments);
      const minimumReductionTokens = Math.max(1, Math.ceil(projectedTokens * 0.1));
      decision = {
        ...decision,
        projectedTokens,
        minimumReductionTokens,
        targetAfterTokens: Math.max(0, Math.min(
          projectedTokens - minimumReductionTokens,
          contextLimit.known ? Math.floor(contextLimit.limit * 0.5) : projectedTokens,
        )),
      };
    }

    if (request.trigger === "auto" && !decision.shouldCompact) {
      if (decision.level === "prefire" && decision.reason === "prefire") {
        if (snapshot.compaction?.status !== "prefire" || snapshot.compaction.sourceDigest !== projectedDigest) {
          updateChatIfRevision(request.chatId, snapshotRevision, (chat) => ({
            ...chat,
            compaction: {
              generation: `prefire-${Date.now()}-${Math.random().toString(16).slice(2)}`,
              status: "prefire",
              trigger: "auto",
              phase: request.phase,
              sourceDigest: projectedDigest,
              beforeTokens: decision.projectedTokens,
              contextLimit: contextLimit.limit,
              minimumReductionTokens: decision.minimumReductionTokens,
              targetAfterTokens: decision.targetAfterTokens,
              provider: request.provider,
              model: request.model,
              startedAt: new Date().toISOString(),
              attempts: 0,
            },
          }));
        }
      } else if (snapshot.compaction?.status === "prefire") {
        const completedAt = new Date().toISOString();
        updateChatIfRevision(request.chatId, snapshotRevision, (chat) => ({
          ...chat,
          compaction: {
            ...chat.compaction!,
            status: "idle",
            prefireOutcome: "waste",
            completedAt,
            latencyMs: boundedCompactionLatencyMs(chat.compaction?.startedAt, completedAt),
            failureClass: "prefire_cleared",
          },
        }));
      }
      return { status: "skipped", decision, chat: snapshot };
    }

    let generation = `compact-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    while (snapshotItems.some((item) => item.id === generation)) generation += "-";
    // The API budgets summary text; the decision budgets the whole replacement.
    // Project the same retained history and reserve the summary entry framing.
    const retainedProjection = buildReplacementItems(snapshotItems, {
      id: generation, type: "compact", text: "", detail: "", status: "completed",
    });
    const sourceMarker = retainedProjection.find((item) => item.type === "compact" && item.id === generation);
    const summarizedIds = new Set(sourceMarker?.type === "compact" ? sourceMarker.summarizedItemIds || [] : []);
    const summarySourceItems = snapshotItems.filter((item) => summarizedIds.has(item.id));
    const summaryHistory = [
      ...buildChatHistory(summarySourceItems, t),
      ...buildDurableCompactionStateEntries(summarySourceItems),
    ];
    const summaryTargetTokens = (decision.targetAfterTokens ?? 0)
      - estimateHistoryTokens(buildChatHistory(retainedProjection, t))
      - estimateIncomingContextTokens(request.incomingText, request.incomingAttachments) - 8;
    if (!summaryHistory.length || summaryTargetTokens < 64) {
      if (request.trigger === "manual") setError(t("compact.noContent"));
      return { status: "skipped", decision, chat: snapshot };
    }
    const controller = new AbortController();
    const unlinkAbort = forwardAbort(request.signal, controller);
    const priorCompactionUsage = previousCompactionUsage(snapshot.compaction);
    const beforeTokens = decision.projectedTokens ?? estimateHistoryTokens(history);
    const prefireHit = request.trigger === "auto"
      && snapshot.compaction?.status === "prefire"
      && snapshot.compaction.provider === request.provider
      && snapshot.compaction.model === request.model
      && snapshot.compaction.contextLimit === (contextLimit.known ? contextLimit.limit : undefined);
    const startState: ChatCompactionState = {
      generation,
      status: "compacting",
      trigger: request.trigger,
      phase: request.phase,
      sourceDigest: projectedDigest,
      beforeTokens,
      contextLimit: contextLimit.known ? contextLimit.limit : undefined,
      minimumReductionTokens: decision.minimumReductionTokens,
      targetAfterTokens: decision.targetAfterTokens,
      provider: request.provider,
      model: request.model,
      prefireOutcome: prefireHit ? "hit" : undefined,
      startedAt: new Date().toISOString(),
      attempts: 1,
      providerUsage: priorCompactionUsage,
    };
    if (!updateChatIfRevision(request.chatId, snapshotRevision, (chat) => ({ ...chat, compaction: startState }))) {
      unlinkAbort();
      return { status: "skipped", decision };
    }
    const workingRevision = snapshotRevision + 1;
    jobsRef.current.set(request.chatId, { generation, controller });
    syncActiveJobCount();
    let failureClassOverride: string | undefined;
    let failureAfterTokens: number | undefined;
    let compactionProviderUsage = priorCompactionUsage;
    let compactionResponseReceived = false;

    try {
      const payload: CompactAgentHistoryResponse = await compactAgentHistory(request.endpoint, {
        history: summaryHistory,
        signal: controller.signal,
        trigger: request.trigger,
        phase: request.phase,
        sourceDigest: fingerprintCompactionSource(summaryHistory),
        language: i18n.resolvedLanguage || i18n.language,
        provider: request.provider,
        model: request.model,
        targetTokens: summaryTargetTokens,
        realContextLimit: contextLimit.known ? contextLimit.limit : undefined,
      });
      compactionResponseReceived = true;
      compactionProviderUsage = mergeCompactionProviderUsage(compactionProviderUsage, payload.providerUsage);

      const summary = String(payload.summary || "").trim();
      if (!summary) {
        failureClassOverride = "empty_summary";
        throw new Error("Compaction returned an empty summary.");
      }
      const replacementItems = buildReplacementItems(snapshotItems, {
        id: generation,
        type: "compact",
        text: t("compact.completed"),
        detail: summary,
        status: "completed",
        entryCount: payload.entryCount ?? history.length,
        beforeTokens,
        contextLimit: contextLimit.known ? contextLimit.limit : undefined,
        compactionRecovery: mergeCompactionRecovery(
          ...snapshotItems.map((item) => (item.type === "agent" || item.type === "compact") ? item.compactionRecovery : undefined),
          payload.recovery,
        ),
        createdAt: new Date().toISOString(),
      });
      const replacementHistory = buildChatHistory(replacementItems, t);
      const incomingTokens = estimateIncomingContextTokens(request.incomingText, request.incomingAttachments);
      const afterTokens = estimateHistoryTokens(replacementHistory) + incomingTokens;
      failureAfterTokens = afterTokens;
      if (!replacementIsUseful(beforeTokens, afterTokens, decision, contextLimit.known ? contextLimit.limit : 0)) {
        failureClassOverride = "reduction_gate";
        throw new Error("Compaction did not reduce context enough to apply safely.");
      }
      const latestBeforeCommit = getChatById(request.chatId);
      if (latestBeforeCommit && hasUnresolvedRuntimeStateRef.current?.(request.chatId, latestBeforeCommit.sessionId)) {
        markLatestGeneration(request.chatId, generation, "failed", "runtime_pending", t("compact.failed"), undefined, compactionProviderUsage);
        return { status: "skipped", decision, chat: latestBeforeCommit };
      }
      const appliedItems = replacementItems.map((item) => (
        item.type === "compact" && item.id === generation ? { ...item, afterTokens } : item
      ));

      if (controller.signal.aborted) {
        markLatestGeneration(request.chatId, generation, "cancelled", "cancelled", t("compact.cancelled"), undefined, compactionProviderUsage);
        return { status: "cancelled", decision, chat: getChatById(request.chatId) };
      }

      const appliedAt = new Date().toISOString();
      const appliedState: ChatCompactionState = {
        ...startState,
        status: "applied",
        summaryDigest: payload.summaryDigest,
        afterTokens,
        entryCount: payload.entryCount ?? history.length,
        retainedEntryCount: payload.retainedEntryCount,
        fidelity: payload.fidelity || "full",
        attempts: boundedCompactionAttempts(payload.attempts ?? payload.providerAttempts ?? 1),
        latencyMs: boundedCompactionLatencyMs(startState.startedAt, appliedAt),
        retainedSummaryCharacters: boundedCompactionSummaryCharacters(summary.length),
        completedAt: appliedAt,
        failureClass: payload.failureClass || payload.fallbackReason,
        providerUsage: compactionProviderUsage,
      };
      const committed = updateChatIfRevision(request.chatId, workingRevision, (chat) => ({
        ...chat,
        ...applyCompactedAttachmentReferences(chat, snapshotItems, appliedItems),
        sessionId: "",
        items: appliedItems,
        compaction: appliedState,
      }));
      if (!committed) {
        markLatestGeneration(request.chatId, generation, "failed", "stale_snapshot", t("compact.failed"), undefined, compactionProviderUsage);
        return { status: "failed", decision, chat: getChatById(request.chatId) };
      }

      const committedRevision = workingRevision + 1;
      try {
        await persistChatsNow();
      } catch (cause) {
        failureClassOverride = "persistence";
        const restored = updateChatIfRevision(request.chatId, committedRevision, (chat) => ({
          ...chat,
          sessionId: snapshot.sessionId,
          items: snapshotItems,
          attachmentPayloads: snapshot.attachmentPayloads,
          compactedAttachmentRefs: snapshot.compactedAttachmentRefs,
          compaction: {
            ...startState,
            providerUsage: compactionProviderUsage,
            status: "failed",
            completedAt: new Date().toISOString(),
            failureClass: "persistence",
            message: t("compact.failed"),
          },
        }));
        if (restored) {
          await persistChatsNow().catch(() => undefined);
        }
        throw cause;
      }
      return { status: "applied", decision, chat: getChatById(request.chatId) };
    } catch (cause) {
      if (!compactionResponseReceived) {
        compactionProviderUsage = mergeCompactionProviderUsage(compactionProviderUsage, {
          exact: false,
          unavailableReason: "provider_usage_missing",
        });
      }
      const cancelled = controller.signal.aborted;
      const failureClass = cancelled ? "cancelled" : failureClassOverride || classifyCompactionFailure(cause);
      markLatestGeneration(
        request.chatId,
        generation,
        cancelled ? "cancelled" : request.trigger === "auto" && isStickyFailure(failureClass) ? "suppressed" : "failed",
        failureClass,
        cancelled ? t("compact.cancelled") : t("compact.failed"),
        failureAfterTokens,
        compactionProviderUsage,
      );
      if (request.trigger === "manual" && !cancelled) {
        setError(t("compact.failed"));
      }
      return { status: cancelled ? "cancelled" : "failed", decision, chat: getChatById(request.chatId) };
    } finally {
      unlinkAbort();
      const active = jobsRef.current.get(request.chatId);
      if (active?.generation === generation) {
        jobsRef.current.delete(request.chatId);
        syncActiveJobCount();
      }
    }
  }

  async function prepareTurnContext(input: PrepareTurnContextInput): Promise<PreparedTurnContext | null> {
    const chat = getChatById(input.chatId);
    if (!chat) {
      return null;
    }
    const outcome = await compactChat({
      chatId: input.chatId,
      endpoint: input.endpoint,
      trigger: "auto",
      phase: "pre_turn",
      provider: input.turn.provider,
      model: input.turn.model,
      contextLimit: input.turn.contextLimit,
      incomingText: input.turn.text,
      incomingAttachments: input.turn.attachments,
      signal: input.signal,
    });
    if (outcome.status === "cancelled") {
      throw new Error(t("compact.cancelled"));
    }
    if (outcome.status === "failed" && outcome.decision?.level === "hard-limit") {
      throw new Error(t("compact.failed"));
    }
    if (outcome.status === "skipped" && outcome.decision?.level === "hard-limit") {
      throw new Error(t("compact.failed"));
    }
    const latest = getChatById(input.chatId);
    return latest
      ? {
          baseItems: latest.items,
          sessionId: latest.sessionId,
          compactionGeneration: latest.compaction?.generation,
        }
      : null;
  }

  function markLatestGeneration(
    chatId: string,
    generation: string,
    status: ChatCompactionState["status"],
    failureClass: string,
    message: string,
    afterTokens?: number,
    providerUsage?: AgentContextUsage,
  ) {
    updateChat(chatId, (chat) => {
      if (chat.compaction?.generation !== generation) {
        return chat;
      }
      const completedAt = new Date().toISOString();
      return {
        ...chat,
        compaction: {
          ...chat.compaction,
          status,
          completedAt,
          latencyMs: boundedCompactionLatencyMs(chat.compaction.startedAt, completedAt),
          afterTokens: afterTokens ?? chat.compaction.afterTokens,
          providerUsage: providerUsage ?? chat.compaction.providerUsage,
          failureClass,
          suppressionReason: status === "suppressed" ? failureClass : undefined,
          message,
        },
      };
    });
  }

  return {
    compacting: activeJobCount > 0,
    compactChat,
    prepareTurnContext,
    cancelCompaction,
  };
}

function previousCompactionUsage(state: ChatCompactionState | undefined): AgentContextUsage | undefined {
  if (!state) return undefined;
  if (state.providerUsage) return state.providerUsage;
  // A legacy applied/failed generation may have consumed a provider request
  // before 075 added this field. Preserve that uncertainty instead of treating
  // the absent receipt as a zero-cost operation.
  if (["compacting", "applied", "failed", "suppressed", "cancelled"].includes(state.status)) {
    return { exact: false, unavailableReason: "provider_usage_missing" };
  }
  return undefined;
}

function buildReplacementItems(
  items: ConversationItem[],
  compactItem: Extract<ConversationItem, { type: "compact" }>,
): ConversationItem[] {
  const durable = stripTransientConversationItems(items);
  let lastUserIndex = -1;
  for (let index = durable.length - 1; index >= 0; index -= 1) {
    if (durable[index]?.type === "user") {
      lastUserIndex = index;
      break;
    }
  }
  const recentDialogue = lastUserIndex >= 0
    ? durable.slice(lastUserIndex).filter((item) => item.type === "user" || item.type === "agent")
    : [];
  const stateCards = durable
    .filter((item) => item.type === "subagent" || item.type === "result");
  const retainedIds = new Set([...recentDialogue, ...stateCards].map((item) => item.id));
  const summarizedItemIds = durable
    .filter((item) => (item.type === "user" || item.type === "agent" || item.type === "compact") && !retainedIds.has(item.id))
    .map((item) => item.id);
  const marker = { ...compactItem, summarizedItemIds, contextUsageBoundaryItemId: contextUsageBoundaryItemId(durable) };
  const result: ConversationItem[] = [];
  let inserted = false;
  for (const item of durable) {
    if (!inserted && summarizedItemIds.includes(item.id)) {
      result.push(marker);
      inserted = true;
    }
    result.push(item);
  }
  return inserted ? result : [marker, ...result];
}

function applyCompactedAttachmentReferences(
  chat: ChatThread,
  sourceItems: readonly ConversationItem[],
  replacementItems: readonly ConversationItem[],
): Pick<ChatThread, "attachmentPayloads" | "compactedAttachmentRefs"> {
  const retainedIds = new Set(replacementItems.map((item) => item.id));
  const removedItems = sourceItems.filter((item) => !retainedIds.has(item.id));
  const attachmentPayloads = { ...(chat.attachmentPayloads || {}) };
  const compactedAttachmentRefs = mergeCompactedAttachmentReferences(
    chat.compactedAttachmentRefs,
    collectCompactedAttachmentReferences(removedItems, attachmentPayloads),
  );
  return {
    attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
    compactedAttachmentRefs,
  };
}

function estimateHistoryTokens(history: ChatHistoryEntry[]): number {
  return history.reduce((total, entry) => total + estimateTextTokens(entry.text) + 8, 0);
}

function replacementIsUseful(
  beforeTokens: number,
  afterTokens: number,
  decision: ContextCompactionBudgetDecision,
  contextLimit: number,
): boolean {
  if (afterTokens >= beforeTokens) {
    return false;
  }
  if (decision.targetAfterTokens !== undefined && afterTokens > decision.targetAfterTokens) {
    return false;
  }
  if (decision.minimumReductionTokens !== undefined && beforeTokens - afterTokens < decision.minimumReductionTokens) {
    return false;
  }
  return contextLimit <= 0 || afterTokens < Math.ceil(contextLimit * 0.85);
}

function forwardAbort(source: AbortSignal | undefined, target: AbortController): () => void {
  if (!source) {
    return () => undefined;
  }
  const abort = () => target.abort(source.reason);
  if (source.aborted) {
    abort();
    return () => undefined;
  }
  source.addEventListener("abort", abort, { once: true });
  return () => source.removeEventListener("abort", abort);
}

function normalizeRevision(value: unknown): number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : 0;
}

function classifyCompactionFailure(cause: unknown): string {
  const message = (cause instanceof Error ? cause.message : String(cause)).toLowerCase();
  if (/401|403|unauthor|forbidden|api.?key|auth|credit|quota|billing/.test(message)) {
    return "auth_credit";
  }
  if (/schema|json|empty|malformed|privacy|secret|redact/.test(message)) {
    return "schema_privacy";
  }
  if (/context|too large|too long|size|token limit/.test(message)) {
    return "size";
  }
  if (/timeout|timed out|offline|unreachable|network|temporar|429|5\d\d/.test(message)) {
    return "transient";
  }
  return "unknown";
}

function isStickyFailure(failureClass: string): boolean {
  return failureClass === "auth_credit" || failureClass === "schema_privacy" || failureClass === "size";
}
