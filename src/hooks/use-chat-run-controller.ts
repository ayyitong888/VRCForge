import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentRuntimeDeltaEvent } from "../lib/chat-streaming";
import type { ChatAttachment, ChatThread, ConversationItem } from "../lib/chat-types";
import { stripTransientConversationItems } from "../lib/chat-thread";
import {
  collectCompactedAttachmentReferences,
  mergeCompactedAttachmentReferences,
  persistAttachmentReference,
  resolveAttachmentPayloadReferences,
  resolveHistoricalAttachmentPayloads,
} from "../lib/attachment-payloads";
import {
  appendAttachmentSummary,
  buildChatHistory,
  serializeChatAttachments,
} from "../lib/conversation-utils";
import { isRuntimeSessionVerificationError } from "../lib/app-runtime";
import {
  boundedCompactionAttempts,
  boundedCompactionSummaryCharacters,
} from "../lib/chat-compaction-state";
import { fingerprintCompactionSource, projectRuntimeCompactionItems } from "../lib/context-compaction";
import {
  issueComputerUseTurnGrant,
  recordAgentRunQueued,
  requestAgentRunCancel,
  sendAgentMessage,
} from "../lib/api";

export const MAX_QUEUED_TURNS = 8;

export type QueuedTurn = {
  id: string;
  text: string;
  attachments: ChatAttachment[];
  providerLabel: string;
  provider: string;
  model: string;
  contextLimit?: number;
  queuedFrom?: boolean;
  chatId?: string;
  sessionId?: string;
  projectPath?: string;
  goalDelivery?: {
    deliveryId: string;
    userItemId: string;
    agentItemId: string;
  };
  computerUseRequested?: boolean;
  computerUseVisualTheme?: "light" | "dark";
  computerUseVisualAccent?: string;
};

export type CurrentTurn = {
  clientTurnId?: string;
  text: string;
  startedAt: number;
  providerLabel: string;
  model: string;
  computerUseRequested?: boolean;
};

export type RunSingleTurnOptions = {
  baseItems?: ConversationItem[];
  sessionId?: string;
  restoreOnFailure?: {
    items: ConversationItem[];
    sessionId: string;
    title?: string;
    updatedAt?: string;
  };
  onFailure?: (message: string) => void;
};

export type PrepareTurnContextInput = {
  endpoint: string;
  chatId: string;
  turn: QueuedTurn;
  signal: AbortSignal;
};

export type PreparedTurnContext = {
  baseItems: ConversationItem[];
  sessionId?: string;
  compactionGeneration?: string;
};

export type SubmitTurnResult = "started" | "queued" | "queue_full" | "failed";

type UseChatRunControllerParams = {
  endpoint: string;
  runtimeConnected: boolean;
  sessionId: string;
  activeRuntimeProjectPath: string;
  getChatById: (chatId: string) => ChatThread | undefined;
  ensureActiveChat: () => string;
  updateChat: (chatId: string, updater: (chat: ChatThread) => ChatThread) => void;
  appendToChat: (chatId: string, item: ConversationItem) => void;
  touchChat: (chat: ChatThread, timestamp?: string) => ChatThread;
  startRuntime: () => Promise<string | null>;
  refresh: (target?: string) => Promise<void>;
  refreshRuntimeRuns: (includeEvents?: boolean, target?: string) => Promise<void>;
  handleRuntimeSessionFailure: (message: string) => void;
  setError: (message: string) => void;
  prepareTurnContext?: (input: PrepareTurnContextInput) => Promise<PreparedTurnContext | null>;
  persistChatsNow?: () => Promise<void>;
};

export function useChatRunController({
  endpoint,
  runtimeConnected,
  sessionId,
  activeRuntimeProjectPath,
  getChatById,
  ensureActiveChat,
  updateChat,
  appendToChat,
  touchChat,
  startRuntime,
  refresh,
  refreshRuntimeRuns,
  handleRuntimeSessionFailure,
  setError,
  prepareTurnContext,
  persistChatsNow,
}: UseChatRunControllerParams) {
  const { t } = useTranslation();
  const [sending, setSending] = useState(false);
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [currentTurn, setCurrentTurn] = useState<CurrentTurn | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const queueRef = useRef<QueuedTurn[]>([]);
  const sendingRef = useRef(false);
  const stopRequestedRef = useRef(false);
  const streamingTurnChatRef = useRef(new Map<string, string>());
  const activeTurnAbortRef = useRef<AbortController | null>(null);

  function isRunning() {
    return sendingRef.current;
  }

  function applyRuntimeDelta(delta: AgentRuntimeDeltaEvent) {
    const clientTurnId = String(delta.clientTurnId || "").trim();
    if (!clientTurnId || !delta.textDelta) {
      return;
    }
    const chatId = streamingTurnChatRef.current.get(clientTurnId);
    if (!chatId) {
      return;
    }
    updateChat(chatId, (chat) => {
      const index = chat.items.findIndex((item) => item.type === "streaming" && item.clientTurnId === clientTurnId);
      if (index < 0) {
        return chat;
      }
      const items = [...chat.items];
      const item = items[index];
      if (!item || item.type !== "streaming") {
        return chat;
      }
      items[index] = { ...item, text: `${item.text}${delta.textDelta}` };
      return { ...chat, items };
    });
  }

  async function submitTurn(turn: QueuedTurn): Promise<SubmitTurnResult> {
    if (sendingRef.current) {
      if (queueRef.current.length >= MAX_QUEUED_TURNS) {
        setError(t("chat.queueFull", { max: MAX_QUEUED_TURNS }));
        return "queue_full";
      }
      const ownerChatId = turn.chatId && getChatById(turn.chatId) ? turn.chatId : ensureActiveChat();
      const ownerChat = getChatById(ownerChatId);
      const queuedTurn = {
        ...turn,
        queuedFrom: true,
        chatId: ownerChatId,
        sessionId: turn.sessionId || ownerChat?.sessionId || sessionId || undefined,
        projectPath: turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
      };
      queueRef.current.push(queuedTurn);
      setQueued([...queueRef.current]);
      void recordAgentRunQueued(endpoint, {
        sessionId: queuedTurn.sessionId,
        clientTurnId: turn.id,
        message: turn.text,
        attachments: serializeChatAttachments(turn.attachments),
        provider: turn.provider,
        providerLabel: turn.providerLabel,
        model: turn.model,
        projectPath: queuedTurn.projectPath,
        projectRoot: queuedTurn.projectPath,
      })
        .then(() => refreshRuntimeRuns(false))
        .catch(() => undefined);
      return "queued";
    }

    const chatId = turn.chatId && getChatById(turn.chatId) ? turn.chatId : ensureActiveChat();
    sendingRef.current = true;
    setSending(true);
    setStopRequested(false);
    stopRequestedRef.current = false;
    try {
      let next: QueuedTurn | undefined = turn;
      let initialTurnSucceeded = false;
      let isInitialTurn = true;
      while (next !== undefined) {
        const succeeded = await runSingleTurn(
          next.chatId || chatId,
          next,
          next.sessionId ? { sessionId: next.sessionId } : undefined,
        );
        if (isInitialTurn) {
          initialTurnSucceeded = succeeded;
          isInitialTurn = false;
        }
        if (stopRequestedRef.current) {
          queueRef.current = [];
          break;
        }
        next = queueRef.current.shift();
        setQueued([...queueRef.current]);
      }
      return initialTurnSucceeded ? "started" : "failed";
    } finally {
      queueRef.current = [];
      setQueued([]);
      sendingRef.current = false;
      setSending(false);
      setStopRequested(false);
      stopRequestedRef.current = false;
    }
  }

  async function runTurnNow(chatId: string, turn: QueuedTurn, options?: RunSingleTurnOptions) {
    if (sendingRef.current) {
      setError(t("chat.cannotActionWhileRunning"));
      return false;
    }
    sendingRef.current = true;
    setSending(true);
    setStopRequested(false);
    stopRequestedRef.current = false;
    try {
      return await runSingleTurn(chatId, turn, options);
    } finally {
      queueRef.current = [];
      setQueued([]);
      sendingRef.current = false;
      setSending(false);
      setStopRequested(false);
      stopRequestedRef.current = false;
    }
  }

  async function runSingleTurn(chatId: string, turn: QueuedTurn, options?: RunSingleTurnOptions): Promise<boolean> {
    const startedAt = Date.now();
    const abortController = new AbortController();
    let userItemId = "";
    activeTurnAbortRef.current = abortController;
    setCurrentTurn({
      clientTurnId: turn.id,
      text: turn.text,
      startedAt,
      providerLabel: turn.providerLabel,
      model: turn.model,
      computerUseRequested: turn.computerUseRequested,
    });
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          throw new Error(t("agent.coreDisconnectedSend"));
        }
        targetEndpoint = readyEndpoint;
      }
      const prepared = !options?.baseItems && prepareTurnContext
        ? await prepareTurnContext({
            endpoint: targetEndpoint,
            chatId,
            turn,
            signal: abortController.signal,
          })
        : null;
      const chat = getChatById(chatId);
      const baseItems = prepared?.baseItems ?? options?.baseItems ?? chat?.items ?? [];
      const chatSessionId = (prepared?.sessionId ?? options?.sessionId ?? chat?.sessionId) || `session-${turn.id}`;
      const chatAgentName = chat?.agentName || "desktop-agent";
      const history = baseItems.length > 0 ? buildChatHistory(baseItems, t) : [];
      const currentAttachments = resolveAttachmentPayloadReferences(turn.attachments, chat?.attachmentPayloads);
      const historicalAttachments = currentAttachments.length > 0
        ? []
        : resolveHistoricalAttachmentPayloads(
            baseItems,
            chat?.attachmentPayloads,
            turn.text,
            chat?.compactedAttachmentRefs,
          ).attachments;
      const requestAttachments = deduplicateRequestAttachments([...currentAttachments, ...historicalAttachments]);
      const messageForModel = appendAttachmentSummary(turn.text, requestAttachments, t);
      const summarizedSourceDigest = fingerprintCompactionSource(history);
      const summarizedSourceItemIds = new Set(
        baseItems
          .filter((item) => item.type === "user" || item.type === "agent" || item.type === "compact" || item.type === "subagent")
          .map((item) => item.id),
      );
      const summarizedItemIds = new Set(
        baseItems
          .filter((item) => item.type === "user" || item.type === "agent" || item.type === "compact")
          .map((item) => item.id),
      );
      const userItem: Extract<ConversationItem, { type: "user" }> = {
        id: turn.goalDelivery?.userItemId || `user-${turn.id}`,
        type: "user",
        text: turn.text,
        attachments: turn.attachments,
        queuedFrom: Boolean(turn.queuedFrom),
        createdAt: new Date(startedAt).toISOString(),
      };
      userItemId = userItem.id;
      const streamingItem: ConversationItem = {
        id: `stream-${turn.id}`,
        type: "streaming",
        clientTurnId: turn.id,
        text: "",
        providerLabel: turn.providerLabel,
        model: turn.model,
        createdAt: new Date(startedAt).toISOString(),
      };
      const message = turn.text;
      streamingTurnChatRef.current.set(turn.id, chatId);
      updateChat(chatId, (current) => {
        const attachmentPayloads = { ...(current.attachmentPayloads || {}) };
        const storedUserItem: ConversationItem = {
          ...userItem,
          attachments: (userItem.attachments || []).map((attachment) => {
            const reference = persistAttachmentReference(attachment, attachmentPayloads);
            return {
              ...attachment,
              payloadHash: reference.payloadHash,
              payloadKind: reference.payloadKind,
            };
          }),
        };
        return {
          ...touchChat(current),
          sessionId: chatSessionId,
          title: current.title || (message.length > 24 ? `${message.slice(0, 24)}...` : message),
          attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
          items: [
            ...stripTransientConversationItems(options?.baseItems ?? current.items).filter(
              (item) => item.id !== userItem.id && item.id !== turn.goalDelivery?.agentItemId,
            ),
            storedUserItem,
            streamingItem,
          ],
        };
      });
      const computerUseGrant = turn.computerUseRequested
        ? await issueComputerUseTurnGrant(targetEndpoint, {
            sessionId: chatSessionId || undefined,
            clientTurnId: turn.id,
            projectRoot: chat?.projectPath || activeRuntimeProjectPath || undefined,
          })
        : null;
      const response = await sendAgentMessage(targetEndpoint, messageForModel, chatSessionId || undefined, history, chatAgentName, {
        signal: abortController.signal,
        attachments: serializeChatAttachments(requestAttachments),
        projectPath: chat?.projectPath || activeRuntimeProjectPath || undefined,
        provider: turn.provider,
        providerLabel: turn.providerLabel,
        model: turn.model,
        contextLimit: turn.contextLimit,
        clientTurnId: turn.id,
        goalDeliveryId: turn.goalDelivery?.deliveryId,
        computerUseRequested: Boolean(turn.computerUseRequested),
        computerUseGrantId: computerUseGrant?.grantId,
        computerUseVisualTheme: turn.computerUseVisualTheme,
        computerUseVisualAccent: turn.computerUseVisualAccent,
      });
      const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      let midTurnCompactionApplied = false;
      updateChat(chatId, (current) => ({
        ...applyRuntimeResponseToChat(current),
      }));
      if (midTurnCompactionApplied && persistChatsNow) {
        await persistChatsNow();
      }
      await refresh(targetEndpoint);
      await refreshRuntimeRuns(false, targetEndpoint);
      return true;

      function applyRuntimeResponseToChat(current: ChatThread): ChatThread {
        const responseItemId = turn.goalDelivery?.agentItemId || response.turnId || response.turn_id;
        let durableItems = stripTransientConversationItems(current.items).filter(
          (item) => item.id !== responseItemId,
        );
        let compaction = current.compaction;
        const runtimeCompaction = response.contextCompaction;
        const summary = String(runtimeCompaction?.summary || "").trim();
        const currentSourceDigest = fingerprintCompactionSource(buildChatHistory(
          durableItems.filter((item) => summarizedSourceItemIds.has(item.id)),
          t,
        ));
        if (runtimeCompaction?.applied && summary && currentSourceDigest === summarizedSourceDigest) {
          const generation = `runtime-compact-${turn.id}-${runtimeCompaction.summaryDigest?.slice(0, 12) || Date.now()}`;
          const compactItem: Extract<ConversationItem, { type: "compact" }> = {
            id: generation,
            type: "compact",
            text: t("compact.completed"),
            detail: summary,
            status: "completed",
            entryCount: runtimeCompaction.entryCount ?? history.length,
            beforeTokens: runtimeCompaction.beforeTokens,
            afterTokens: runtimeCompaction.afterTokens,
            contextLimit: runtimeCompaction.contextLimit,
            createdAt: new Date().toISOString(),
          };
          const projection = projectRuntimeCompactionItems(durableItems, summarizedItemIds, compactItem);
          if (projection.replacedCount > 0 || summarizedItemIds.size === 0) {
            const attachmentPayloads = { ...(current.attachmentPayloads || {}) };
            const compactedAttachmentRefs = mergeCompactedAttachmentReferences(
              current.compactedAttachmentRefs,
              collectCompactedAttachmentReferences(
                durableItems.filter((item) => summarizedItemIds.has(item.id)),
                attachmentPayloads,
              ),
            );
            durableItems = projection.replacedCount > 0 ? projection.items : [compactItem, ...durableItems];
            midTurnCompactionApplied = true;
            current = {
              ...current,
              attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
              compactedAttachmentRefs,
            };
            compaction = {
              generation,
              status: "applied",
              trigger: "auto",
              phase: "mid_turn",
              sourceDigest: runtimeCompaction.sourceDigest,
              summaryDigest: runtimeCompaction.summaryDigest,
              beforeTokens: runtimeCompaction.beforeTokens,
              afterTokens: runtimeCompaction.afterTokens,
              contextLimit: runtimeCompaction.contextLimit,
              targetAfterTokens: runtimeCompaction.targetAfterTokens,
              provider: turn.provider,
              model: turn.model,
              entryCount: runtimeCompaction.entryCount,
              retainedEntryCount: runtimeCompaction.retainedEntryCount,
              fidelity: runtimeCompaction.fidelity,
              attempts: boundedCompactionAttempts(runtimeCompaction.attempts),
              latencyMs: boundedRuntimeLatency(runtimeCompaction.latencyMs),
              retainedSummaryCharacters: boundedCompactionSummaryCharacters(
                runtimeCompaction.retainedSummaryCharacters ?? summary.length,
              ),
              failureClass: runtimeCompaction.failureClass,
              suppressionReason: boundedRuntimeReason(runtimeCompaction.suppressionReason),
              startedAt: new Date(startedAt).toISOString(),
              completedAt: new Date().toISOString(),
            };
          }
        } else if (runtimeCompaction) {
          const failureClass = boundedRuntimeReason(runtimeCompaction.failureClass) || "unknown";
          const status = failureClass === "cancelled"
            ? "cancelled"
            : failureClass.startsWith("suppressed") || runtimeCompaction.suppressionReason
              ? "suppressed"
              : "failed";
          compaction = {
            generation: `runtime-compact-${turn.id}-${failureClass}`,
            status,
            trigger: "auto",
            phase: "mid_turn",
            beforeTokens: runtimeCompaction.beforeTokens,
            afterTokens: runtimeCompaction.afterTokens,
            contextLimit: runtimeCompaction.contextLimit,
            targetAfterTokens: runtimeCompaction.targetAfterTokens,
            provider: turn.provider,
            model: turn.model,
            entryCount: runtimeCompaction.entryCount,
            retainedEntryCount: runtimeCompaction.retainedEntryCount,
            fidelity: runtimeCompaction.fidelity,
            attempts: boundedCompactionAttempts(runtimeCompaction.attempts),
            latencyMs: boundedRuntimeLatency(runtimeCompaction.latencyMs),
            retainedSummaryCharacters: boundedCompactionSummaryCharacters(
              runtimeCompaction.retainedSummaryCharacters,
            ),
            failureClass,
            suppressionReason: status === "suppressed"
              ? boundedRuntimeReason(runtimeCompaction.suppressionReason) || failureClass
              : undefined,
            startedAt: new Date(startedAt).toISOString(),
            completedAt: new Date().toISOString(),
          };
        }
        return {
          ...touchChat(current),
          sessionId: response.sessionId || response.session_id || current.sessionId,
          compaction,
          items: [
            ...durableItems,
            { id: responseItemId, type: "agent", response, elapsedSeconds, providerLabel: turn.providerLabel, model: turn.model, createdAt: new Date().toISOString() },
          ],
        };
      }
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      if (options?.restoreOnFailure) {
        const snapshot = options.restoreOnFailure;
        updateChat(chatId, (current) => ({
          ...current,
          sessionId: snapshot.sessionId,
          title: snapshot.title || current.title,
          updatedAt: snapshot.updatedAt || current.updatedAt,
          items: stripTransientConversationItems(snapshot.items),
        }));
        options.onFailure?.(text);
      } else {
      if (userItemId && text.toLowerCase().includes("cancel")) {
        updateChat(chatId, (current) => ({
          ...touchChat(current),
          sessionId: "",
          items: current.items.filter((item) => item.id !== userItemId),
        }));
      }
      appendToChat(chatId, { id: `error-${Date.now()}`, type: "error", text });
      updateChat(chatId, (current) => ({
        ...touchChat(current),
        items: stripTransientConversationItems(current.items),
      }));
      }
      if (isRuntimeSessionVerificationError(text)) {
        handleRuntimeSessionFailure(text);
      } else {
        setError(text);
      }
      return false;
    } finally {
      updateChat(chatId, (current) => {
        const items = current.items.filter(
          (item) => item.type !== "streaming" || item.clientTurnId !== turn.id,
        );
        return items.length === current.items.length ? current : { ...current, items };
      });
      if (activeTurnAbortRef.current === abortController) {
        activeTurnAbortRef.current = null;
      }
      streamingTurnChatRef.current.delete(turn.id);
      setCurrentTurn(null);
    }
  }

  function stopCurrentRun() {
    stopRequestedRef.current = true;
    setStopRequested(true);
    queueRef.current = [];
    setQueued([]);
    const current = currentTurn;
    if (current?.clientTurnId) {
      void requestAgentRunCancel(endpoint, {
        clientTurnId: current?.clientTurnId,
        reason: "user_stop",
      })
        .then(() => refreshRuntimeRuns(false))
        .catch(() => undefined);
    }
    activeTurnAbortRef.current?.abort();
  }

  return {
    sending,
    queued,
    currentTurn,
    stopRequested,
    isRunning,
    submitTurn,
    runTurnNow,
    stopCurrentRun,
    applyRuntimeDelta,
  };
}

function deduplicateRequestAttachments(attachments: ChatAttachment[]): ChatAttachment[] {
  const seen = new Set<string>();
  return attachments.filter((attachment) => {
    const key = attachment.payloadHash
      ? `payload:${attachment.payloadHash}`
      : `attachment:${attachment.id}`;
    if (seen.has(key)) {
      return false;
    }
    seen.add(key);
    return true;
  });
}

function boundedRuntimeLatency(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.min(24 * 60 * 60 * 1_000, Math.round(value))
    : undefined;
}

function boundedRuntimeReason(value: unknown): string | undefined {
  return typeof value === "string" ? value.trim().slice(0, 80) || undefined : undefined;
}
