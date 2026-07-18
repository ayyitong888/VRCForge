import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { compactAgentHistory, type ChatHistoryEntry, type CompactAgentHistoryResponse } from "../lib/api";
import type { ChatCompactionState, ChatThread, ConversationItem } from "../lib/chat-types";
import {
  boundedCompactionAttempts,
  boundedCompactionLatencyMs,
  boundedCompactionSummaryCharacters,
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
  invalidateCompactedWindowUsage,
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
}: UseContextCompactionControllerParams) {
  const { t, i18n } = useTranslation();
  const jobsRef = useRef(new Map<string, ActiveCompactionJob>());
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
    const sourceDigest = fingerprintCompactionSource(history);
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
    const measuredUsage = latestAgentContextUsage(snapshotItems) || snapshot.contextUsageCache;
    const usage = contextUsageMatchesModel(measuredUsage, request.provider || "", request.model || "")
      ? measuredUsage
      : undefined;
    const decision = evaluateCompactionBudget({
      usage,
      contextLimit,
      incomingText: request.incomingText,
      incomingAttachments: request.incomingAttachments,
      sourceDigest: projectedDigest,
      previousCompaction: snapshot.compaction,
    });

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

    const generation = `compact-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const controller = new AbortController();
    const unlinkAbort = forwardAbort(request.signal, controller);
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
    };
    if (!updateChatIfRevision(request.chatId, snapshotRevision, (chat) => ({ ...chat, compaction: startState }))) {
      unlinkAbort();
      return { status: "skipped", decision };
    }
    const workingRevision = snapshotRevision + 1;
    jobsRef.current.set(request.chatId, { generation, controller });
    syncActiveJobCount();

    try {
      const payload: CompactAgentHistoryResponse = await compactAgentHistory(request.endpoint, {
        history,
        signal: controller.signal,
        trigger: request.trigger,
        phase: request.phase,
        sourceDigest,
        language: i18n.resolvedLanguage || i18n.language,
        provider: request.provider,
        model: request.model,
        targetTokens: decision.targetAfterTokens,
        realContextLimit: contextLimit.known ? contextLimit.limit : undefined,
      });

      const summary = String(payload.summary || "").trim();
      if (!summary) {
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
        createdAt: new Date().toISOString(),
      });
      const replacementHistory = buildChatHistory(replacementItems, t);
      const incomingTokens = estimateIncomingContextTokens(request.incomingText, request.incomingAttachments);
      const afterTokens = estimateHistoryTokens(replacementHistory) + incomingTokens;
      if (!replacementIsUseful(beforeTokens, afterTokens, decision, contextLimit.known ? contextLimit.limit : 0)) {
        throw new Error("Compaction did not reduce context enough to apply safely.");
      }
      const appliedItems = replacementItems.map((item) => (
        item.type === "compact" && item.id === generation ? { ...item, afterTokens } : item
      ));

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
      };
      const committed = updateChatIfRevision(request.chatId, workingRevision, (chat) => ({
        ...chat,
        ...applyCompactedAttachmentReferences(chat, snapshotItems, appliedItems),
        sessionId: snapshot.sessionId,
        items: appliedItems,
        compaction: appliedState,
      }));
      if (!committed) {
        markLatestGeneration(request.chatId, generation, "failed", "stale_snapshot", t("compact.failed"));
        return { status: "failed", decision, chat: getChatById(request.chatId) };
      }

      const committedRevision = workingRevision + 1;
      try {
        await persistChatsNow();
      } catch (cause) {
        const restored = updateChatIfRevision(request.chatId, committedRevision, (chat) => ({
          ...chat,
          sessionId: snapshot.sessionId,
          items: snapshotItems,
          attachmentPayloads: snapshot.attachmentPayloads,
          compactedAttachmentRefs: snapshot.compactedAttachmentRefs,
          compaction: {
            ...startState,
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
      const cancelled = controller.signal.aborted;
      const failureClass = cancelled ? "cancelled" : classifyCompactionFailure(cause);
      markLatestGeneration(
        request.chatId,
        generation,
        cancelled ? "cancelled" : request.trigger === "auto" && isStickyFailure(failureClass) ? "suppressed" : "failed",
        failureClass,
        cancelled ? t("compact.cancelled") : t("compact.failed"),
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
  const retained = invalidateCompactedWindowUsage(
    durable.filter((item) => retainedIds.has(item.id)),
  );
  return [compactItem, ...retained];
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
