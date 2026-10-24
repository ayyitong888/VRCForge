import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  applyAgentRuntimeDeltaToStreamingItem,
  finalizeCancelledStreamingTurn,
  normalizeAgentRuntimePhase,
  normalizeAgentRuntimeTimelineEvent,
  type AgentRuntimeDeltaEvent,
} from "../lib/chat-streaming";
import type { ChatAttachment, ChatThread, ConversationItem } from "../lib/chat-types";
import {
  materializeRuntimeTimeline,
  mergeRuntimeTimelines,
  stripTransientConversationItems,
} from "../lib/chat-thread";
import { RuntimeQueueArbitrator, takeNextRunnableQueuedTurn } from "../lib/runtime-queue-arbitration";
import { settleTerminalSteerDelivery } from "../lib/chat-steer-delivery";
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
  collectCompactionRecovery,
  serializeChatAttachments,
} from "../lib/conversation-utils";
import { isRuntimeSessionVerificationError } from "../lib/app-runtime";
import {
  boundedCompactionAttempts,
  boundedCompactionSummaryCharacters,
} from "../lib/chat-compaction-state";
import { contextUsageBoundaryItemId, fingerprintCompactionSource, mergeCompactionRecovery, projectRuntimeCompactionItems } from "../lib/context-compaction";
import {
  cancelAgentRunFollowup,
  fetchAgentQuestions,
  issueComputerUseTurnGrant,
  listAgentRunFollowups,
  recordAgentRunQueued,
  requestAgentRunCancel,
  requestChatTitle,
  sendAgentMessage,
} from "../lib/api";
import { DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS, type AgentRuntimeFollowup } from "../lib/api/agent-runtime";
import { projectVisionFailureNotice } from "../lib/vision-failure-notice";
import { createSteerDeliveryTrace, type SteerDeliveryTraceEvent } from "../lib/chat-steer-delivery";
import {
  applyGeneratedChatTitle,
  chatTitleRunSucceeded,
  shouldGenerateChatTitle,
} from "../lib/chat-title";

// Keep the client launch cap aligned with RuntimeLaneBudget's 12 background
// slots; the backend retains four interactive slots within the total 16.
export const MAX_BACKGROUND_TURNS = 12;
export const MAX_BACKGROUND_TURNS_PER_SCOPE = 4;

export type QueuedTurn = {
  planMode?: boolean;
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
  projectType?: "general" | "unity";
  goalDelivery?: {
    deliveryId: string;
    userItemId: string;
    agentItemId: string;
  };
  computerUseRequested?: boolean;
  computerUseVisualTheme?: "light" | "dark";
  computerUseVisualAccent?: string;
  queueId?: string;
  queueLaneId?: string;
  queueSequence?: number;
  queueStatus?: "steering" | "queued" | "waiting_for_resources" | "delivery_unverified" | "paused" | "cancelled";
  questionId?: string;
};

export type CurrentTurn = {
  planMode?: boolean;
  clientTurnId?: string;
  sessionId?: string;
  text: string;
  startedAt: number;
  providerLabel: string;
  model: string;
  computerUseRequested?: boolean;
};

export type SubmitTurnOptions = {
  deliveryMode?: "steer" | "queue";
};

export type QueuedTurnEdit = Partial<Pick<QueuedTurn,
  "text" | "attachments" | "planMode" | "provider" | "providerLabel" | "model" | "contextLimit"
  | "projectPath" | "projectType" | "computerUseRequested" | "computerUseVisualTheme" | "computerUseVisualAccent"
>>;

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

type InternalRunSingleTurnOptions = RunSingleTurnOptions & {
  background?: boolean;
  abortController?: AbortController;
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

export type SubmitTurnResult = "started" | "steered" | "queued" | "not_accepted" | "failed";

const CANCELLABLE_QUEUE_STATUSES = new Set([
  "steering",
  "queued",
  "waiting_for_resources",
  "paused",
]);

/** Queue lanes may contain a chat id or an older runtime session id. */
export function resolveFollowupRestoreOwner(
  entry: AgentRuntimeFollowup,
  chats: ChatThread[],
  existingChatId?: string,
): { chat?: ChatThread; allowFallback: boolean } {
  const laneOwner = chats.find((chat) => chat.id === entry.sessionId)
    || chats.find((chat) => chat.id === existingChatId);
  if (laneOwner) return { chat: laneOwner, allowFallback: false };
  const ownsTurn = (chat: ChatThread, cid: string) => chat.items.some((item) =>
    ((item.type === "user" || item.type === "streaming") && (item.clientTurnId === cid || item.id === `user-${cid}`))
    || (item.type === "agent" && item.response.clientTurnId === cid));
  const runtimeSession = entry.runtimeSessionId || entry.sessionId;
  const sessionOwners = chats.filter((chat) => chat.sessionId === runtimeSession);
  const exactInputOwners = sessionOwners.filter((chat) => ownsTurn(chat, entry.clientTurnId));
  if (exactInputOwners.length === 1) return { chat: exactInputOwners[0], allowFallback: false };
  const targetOwners = entry.targetClientTurnId
    ? sessionOwners.filter((chat) => ownsTurn(chat, entry.targetClientTurnId!)) : [];
  if (targetOwners.length === 1) return { chat: targetOwners[0], allowFallback: false };
  if (sessionOwners.length === 1) return { chat: sessionOwners[0], allowFallback: false };
  return { allowFallback: !sessionOwners.length && !entry.runtimeSessionId && !entry.targetClientTurnId
    && !entry.questionId && entry.continuationSource !== "question_answered"
    && !entry.clientTurnId.startsWith("question-answer:") };
}

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
  refreshBackgroundGoals: () => void;
  handleRuntimeSessionFailure: (message: string) => void;
  setError: (message: string) => void;
  notifyFailure?: (kind: "vision" | "send", message: string) => void;
  prepareTurnContext?: (input: PrepareTurnContextInput) => Promise<PreparedTurnContext | null>;
  persistChatsNow: () => Promise<void>;
  traceSteerDelivery?: (event: SteerDeliveryTraceEvent) => void;
  chats?: ChatThread[];
  chatStorageReady?: boolean;
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
  refreshBackgroundGoals,
  handleRuntimeSessionFailure,
  setError,
  notifyFailure,
  prepareTurnContext,
  persistChatsNow,
  traceSteerDelivery,
  chats,
  chatStorageReady = true,
}: UseChatRunControllerParams) {
  const { t } = useTranslation();
  const [sending, setSending] = useState(false);
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [currentTurn, setCurrentTurn] = useState<CurrentTurn | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const queueRef = useRef<QueuedTurn[]>([]);
  const queueResumeInFlightRef = useRef(false);
  const queueResumeRequestedRef = useRef(false);
  const sendingRef = useRef(false);
  const currentTurnRef = useRef<CurrentTurn | null>(null);
  const stopRequestedRef = useRef(false);
  const queueArbitratorRef = useRef(new RuntimeQueueArbitrator());
  const queueDispatchTailRef = useRef<Promise<void>>(Promise.resolve());
  const queueDispatchPendingRef = useRef(0);
  const queueCancellationGenerationRef = useRef(0);
  const queueCancellationInFlightRef = useRef(false);
  const steerIntentRef = useRef(new Map<string, QueuedTurn>());
  const streamingTurnChatRef = useRef(new Map<string, string>());
  const activeTurnAbortRef = useRef<AbortController | null>(null);
  const dispatchedTurnAbortRef = useRef<AbortController | null>(null);
  const backgroundTurnAbortRefs = useRef(new Map<string, AbortController>());
  const titleGenerationAttemptedRef = useRef(new Set<string>());
  const rehydratedRef = useRef(false);

  useEffect(() => {
    for (const [inputId, intent] of steerIntentRef.current) {
      const item = chats?.find((chat) => chat.id === intent.chatId)?.items.find(
        (candidate) => candidate.type === "user" && candidate.clientTurnId === inputId,
      );
      if (item?.type === "user" && item.queuedFrom === false && item.queueStatus === undefined) {
        steerIntentRef.current.delete(inputId);
      }
    }
  }, [chats]);

  useEffect(() => {
    if (rehydratedRef.current || !chatStorageReady || !chats) return;
    rehydratedRef.current = true;
    let cancelled = false;
    const restored: QueuedTurn[] = [];
    let interruptedSteerRecovered = false;
    for (const chat of chats) for (const item of chat.items) {
      if (item.type !== "user" || !item.clientTurnId || !item.queueEnvelope) continue;
      if (item.queueStatus === "steering") {
        interruptedSteerRecovered = true;
        updateChat(chat.id, (current) => ({
          ...touchChat(current),
          items: current.items.map((candidate) => candidate.id === item.id
            ? { ...candidate, queueStatus: "delivery_unverified" }
            : candidate),
        }));
        continue;
      }
      if (item.queueStatus === "delivery_unverified") continue;
      if (!["queued", "waiting_for_resources", "paused"].includes(item.queueStatus || "")) continue;
      const env = item.queueEnvelope;
      restored.push({ planMode: item.planMode === true, id: item.clientTurnId, text: item.text, attachments: item.attachments || [], provider: env.provider || "", providerLabel: env.providerLabel || env.provider || "", model: env.model || "", contextLimit: env.contextLimit, chatId: chat.id, sessionId: env.sessionId || chat.sessionId, projectPath: env.projectPath || chat.projectPath, projectType: env.projectType || chat.projectType, computerUseRequested: env.computerUseRequested, computerUseVisualTheme: env.computerUseVisualTheme, computerUseVisualAccent: env.computerUseVisualAccent, queuedFrom: true, queueId: env.queueId, queueLaneId: env.laneId || chat.id, queueSequence: env.sequence, queueStatus: item.queueStatus as QueuedTurn["queueStatus"] });
    }
    queueRef.current = restored;
    setQueued(restored);
    if (interruptedSteerRecovered) void persistChatsNow?.().catch(() => undefined);

    void listAgentRunFollowups(endpoint, { includeTerminal: false }).then(({ items }) => {
      if (cancelled) return;
      const next = [...queueRef.current];
      for (const entry of items || []) {
        if (!entry.queueId || !entry.clientTurnId || !entry.sessionId) continue;
        const existingTurn = next.find((candidate) => candidate.id === entry.clientTurnId);
        const laneId = entry.sessionId;
        const owner = resolveFollowupRestoreOwner(entry, chats || [], existingTurn?.chatId);
        let chat = owner.chat;
        if (!chat && owner.allowFallback) {
          const createdChatId = ensureActiveChat();
          chat = getChatById(createdChatId);
        }
        if (!chat) {
          setError("Queued work could not be matched to one existing chat.");
          continue;
        }
        const savedItem = chat.items.find((item): item is Extract<ConversationItem, { type: "user" }> => (
          item.type === "user" && item.clientTurnId === entry.clientTurnId
        ));
        const questionId = entry.questionId || (entry.clientTurnId.startsWith("question-answer:")
          ? entry.clientTurnId.slice("question-answer:".length) : undefined);
        const savedAnswer = questionId ? chat.items.find((item): item is Extract<ConversationItem, { type: "question_answer" }> => item.type === "question_answer" && item.questionId === questionId) : undefined;
        if ((!entry.questionId && questionId && !savedAnswer) || (entry.continuationSource === "question_answered" && !questionId)) {
          setError("The queued reply could not be matched to its saved question answer.");
          continue;
        }
        const attachments = savedItem?.attachments || savedAnswer?.attachments || (entry.attachments || []) as ChatAttachment[];
        const unresolvedAttachment = attachments.some((attachment) => (
          !savedItem && !savedAnswer && attachment.payloadKind !== "vault_file"
        ));
        const pending = entry.status === "pending";
        const status: QueuedTurn["queueStatus"] = pending
          ? (unresolvedAttachment ? "paused" : "queued")
          : "delivery_unverified";
        const turn: QueuedTurn = {
          planMode: savedItem?.planMode === true || entry.planMode === true,
          id: entry.clientTurnId,
          text: savedItem?.text || entry.message || "",
          attachments,
          provider: entry.provider || "",
          providerLabel: entry.providerLabel || entry.provider || "",
          model: entry.model || "",
          chatId: chat.id,
          sessionId: entry.runtimeSessionId || chat.sessionId || laneId,
          projectPath: entry.projectPath || entry.projectRoot || chat.projectPath,
          projectType: entry.projectType || chat.projectType,
          queuedFrom: true,
          queueId: entry.queueId,
          queueLaneId: laneId,
          queueSequence: entry.sequence,
          queueStatus: status,
          questionId: entry.questionId || (savedAnswer ? questionId : undefined),
        };
        const oldIndex = next.findIndex((candidate) => candidate.id === turn.id);
        if (oldIndex >= 0) next.splice(oldIndex, 1);
        next.push(turn);
        if (!savedItem && !turn.questionId) {
          updateChat(chat.id, (current) => ({
            ...touchChat(current),
            sessionId: turn.sessionId || current.sessionId,
            items: [...current.items, {
              id: `user-${turn.id}`,
              type: "user",
              text: turn.text,
              planMode: turn.planMode === true,
              attachments,
              queuedFrom: true,
              composerQueued: pending,
              queueStatus: status,
              clientTurnId: turn.id,
              queueEnvelope: {
                planMode: turn.planMode === true,
                provider: turn.provider,
                providerLabel: turn.providerLabel,
                model: turn.model,
                projectPath: turn.projectPath,
                projectType: turn.projectType,
                sessionId: turn.sessionId,
                laneId,
                queueId: turn.queueId,
                sequence: turn.queueSequence,
              },
              createdAt: new Date().toISOString(),
            }],
          }));
        }
      }
      next.sort((a, b) => (a.queueSequence ?? Number.MAX_SAFE_INTEGER) - (b.queueSequence ?? Number.MAX_SAFE_INTEGER) || a.id.localeCompare(b.id));
      queueRef.current = next;
      setQueued(next);
      if (next.some((turn) => turn.queueStatus === "queued")) {
        void persistChatsNow?.().catch(() => undefined);
        if (next[0]?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
      }
    }).catch(() => {
      if (!cancelled && restored[0]?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
    });
    return () => { cancelled = true; };
  }, [chatStorageReady]);

  function isRunning() {
    return sendingRef.current;
  }

  async function maybeGenerateChatTitle(
    chatId: string,
    message: string,
    provider: string,
    model: string,
    fallbackTitle: string,
  ): Promise<void> {
    const current = getChatById(chatId);
    if (!current || current.titleSource === "manual" || current.title !== fallbackTitle) {
      return;
    }
    if (titleGenerationAttemptedRef.current.has(chatId)) return;
    titleGenerationAttemptedRef.current.add(chatId);
    try {
      const result = await requestChatTitle(endpoint, message, provider, model);
      const generatedTitle = result.ok ? result.title.trim() : "";
      if (!generatedTitle) return;
      updateChat(chatId, (current) => applyGeneratedChatTitle(current, fallbackTitle, generatedTitle));
      await persistChatsNow?.().catch(() => undefined);
    } catch {
      // The first-message title remains the durable fallback when this
      // independent, best-effort request cannot complete.
    }
  }

  function markQueuedTurnStatus(turnId: string, status: QueuedTurn["queueStatus"]): void {
    queueRef.current = queueRef.current.map((turn) => turn.id === turnId ? { ...turn, queueStatus: status } : turn);
    setQueued([...queueRef.current]);
    for (const chat of chats || []) {
      if (!chat.items.some((item) => item.type === "user" && item.clientTurnId === turnId)) continue;
      updateChat(chat.id, (current) => ({
        ...touchChat(current),
        items: current.items.map((item) => item.type === "user" && item.clientTurnId === turnId
          ? {
            ...item,
            queueStatus: status,
            composerQueued: status === "queued" || status === "waiting_for_resources" || status === "paused" || status === "cancelled",
            queuedFrom: status !== "delivery_unverified",
          }
          : item),
      }));
      break;
    }
    void persistChatsNow?.().catch(() => undefined);
  }

  async function resumeQueuedTurns(): Promise<void> {
    if (queueResumeInFlightRef.current) {
      queueResumeRequestedRef.current = true;
      return;
    }
    if (sendingRef.current || stopRequestedRef.current || queueCancellationInFlightRef.current || queueRef.current.length === 0) return;
    queueResumeInFlightRef.current = true;
    try {
      const head = queueRef.current[0];
      if (!head || await hasPendingQuestionForTurn(head)) return;
      if (head.queueStatus === "waiting_for_resources" || head.queueStatus === "delivery_unverified" || !head.queueId) {
        const result = await recordAgentRunQueued(endpoint, {
          sessionId: head.sessionId,
          laneId: head.queueLaneId || head.chatId,
          clientTurnId: head.id,
          planMode: head.planMode === true,
          message: head.text,
          attachments: serializeChatAttachments(head.attachments),
          provider: head.provider,
          providerLabel: head.providerLabel,
          model: head.model,
          projectPath: head.projectPath,
          projectRoot: head.projectPath,
          projectType: head.projectType,
        }).catch(() => null);
        if (!result?.accepted || result.mode !== "followup" || !result.queueId) {
          markQueuedTurnStatus(head.id, "waiting_for_resources");
          return;
        }
        if (result.status === "acked") {
          queueRef.current.shift();
          markQueuedTurnStatus(head.id, "delivery_unverified");
          setQueued([...queueRef.current]);
          if (queueRef.current[0]?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
          return;
        }
        if (result.status === "cancelled") {
          queueRef.current.shift();
          markQueuedTurnStatus(head.id, "cancelled");
          setQueued([...queueRef.current]);
          if (queueRef.current[0]?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
          return;
        }
        head.queueId = result.queueId;
        head.queueSequence = result.sequence;
      }
      queueRef.current.shift();
      markQueuedTurnStatus(head.id, "queued");
      setQueued([...queueRef.current]);
      void submitTurn({ ...head, queueStatus: "queued" });
    } finally {
      queueResumeInFlightRef.current = false;
      if (queueResumeRequestedRef.current) {
        queueResumeRequestedRef.current = false;
        if (!sendingRef.current && !stopRequestedRef.current && queueRef.current.length > 0) {
          queueMicrotask(() => void resumeQueuedTurns());
        }
      }
    }
  }

  async function hasPendingQuestionForTurn(turn: QueuedTurn): Promise<boolean> {
    const targetSessionId = turn.sessionId || getChatById(turn.chatId || "")?.sessionId;
    if (!targetSessionId) return false;
    try {
      const result = await fetchAgentQuestions(endpoint, {
        sessionId: targetSessionId,
        projectRoot: turn.projectPath || undefined,
      });
      return result.questions.some((question) => {
        if ((question.runtimeContinuation?.sessionId || question.sessionId) !== targetSessionId) return false;
        const continuation = question.runtimeContinuation;
        const status = continuation?.status || question.runtimeContinuationStatus || "";
        const active = ["running", "claimed", "cancelling"].includes(status)
          || (status === "queued" && Boolean(continuation?.clientTurnId || continuation?.turnId
            || question.runtimeContinuationClientTurnId || question.runtimeContinuationTurnId));
        return active || (question.status === "pending" && (question.blocking !== false || question.needsUserAction === true));
      });
    } catch {
      // Do not start another native turn when question state cannot be verified.
      return true;
    }
  }

  async function cancelQueuedTurns(): Promise<void> {
    if (queueCancellationInFlightRef.current) return;
    queueCancellationInFlightRef.current = true;
    const pending = [...queueRef.current];
    queueCancellationGenerationRef.current += 1;
    queueArbitratorRef.current.stop();
    // Stop local draining while cancellation is pending. Only an authoritative
    // cancelled receipt may remove a durable follow-up from the visible queue.
    for (const turn of pending) {
      if (turn.queueStatus !== "delivery_unverified") markQueuedTurnStatus(turn.id, "paused");
    }
    let failed = false;
    try {
      for (const turn of pending) {
        if (!await removeQueuedTurn(turn.id)) failed = true;
      }
      if (failed) setError(t("chat.queueCancellationFailed"));
      await persistChatsNow?.().catch(cause => setError(cause instanceof Error ? cause.message : String(cause)));
    } finally {
      queueCancellationInFlightRef.current = false;
    }
  }

  function applyRuntimeDelta(delta: AgentRuntimeDeltaEvent) {
    const clientTurnId = String(delta.clientTurnId || "").trim();
    const phase = normalizeAgentRuntimePhase(delta.phase);
    const timelineEvent = normalizeAgentRuntimeTimelineEvent(delta.timelineEvent);
    if (!clientTurnId || (!delta.textDelta && !phase && !timelineEvent && delta.activity !== true)) {
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
      items[index] = applyAgentRuntimeDeltaToStreamingItem(item, delta);
      return { ...chat, items };
    });
  }

  function clearTurnTransientItems(chatId: string, clientTurnId: string, userItemId: string): boolean {
    const chat = getChatById(chatId);
    if (!chat?.items.some(
      (item) => item.id === userItemId
        || (item.type === "streaming" && item.clientTurnId === clientTurnId),
    )) {
      return false;
    }
    updateChat(chatId, (current) => ({
      ...touchChat(current),
      items: current.items.filter(
        (item) => item.id !== userItemId
          && !(item.type === "streaming" && item.clientTurnId === clientTurnId),
      ),
    }));
    return true;
  }

  async function submitTurn(turn: QueuedTurn, options: SubmitTurnOptions = {}): Promise<SubmitTurnResult> {
    if (options.deliveryMode === "queue") return enqueueFollowupTurn(turn);
    const visibleOwnerChatId = turn.chatId && getChatById(turn.chatId) ? turn.chatId : ensureActiveChat();
    const visibleOwnerChat = getChatById(visibleOwnerChatId);
    const continuedTurn = [...(visibleOwnerChat?.items || [])].reverse().find(
      (item): item is Extract<ConversationItem, { type: "streaming" }> => item.type === "streaming" && Boolean(item.clientTurnId),
    );
    const trace = createSteerDeliveryTrace(traceSteerDelivery, {
      chatId: turn.chatId,
      sessionId: turn.sessionId,
      clientTurnId: turn.id,
    });
    trace("submit_received", { pendingDispatches: queueDispatchPendingRef.current, requestedDeliveryMode: options.deliveryMode || "steer" });
    if ((sendingRef.current || continuedTurn || queueRef.current.length > 0) && !turn.queueId) {
      const reservation = queueArbitratorRef.current.reserve(turn.id, queueRef.current.length);
      if (!reservation) {
        trace("reservation_rejected");
        return "queued";
      }
      const cancellationGeneration = queueCancellationGenerationRef.current;
      const ownerChatId = visibleOwnerChatId;
      const ownerChat = getChatById(ownerChatId);
      const queuedTurn = {
        ...turn,
        queuedFrom: true,
        chatId: ownerChatId,
        sessionId: turn.sessionId || ownerChat?.sessionId || sessionId || undefined,
        projectPath: turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
        projectType: turn.projectType || ownerChat?.projectType || ((turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath) ? "unity" : "general"),
        queueLaneId: ownerChatId,
      };
      trace("reservation_acquired", { chatId: ownerChatId, sessionId: queuedTurn.sessionId });
      trace("steer_context_resolved", { chatId: ownerChatId, sessionId: queuedTurn.sessionId });
      // The transcript owns the full visible input and its chat/session binding.
      // Make it durable before any runtime delivery can produce a response.
      const existing = ownerChat?.items.some(
        (item) => item.type === "user" && item.clientTurnId === turn.id,
      );
      if (!existing && !turn.questionId) {
        updateChat(ownerChatId, (current) => {
          const attachmentPayloads = { ...(current.attachmentPayloads || {}) };
          const storedAttachments = turn.attachments.map((attachment) => (
            persistAttachmentReference(attachment, attachmentPayloads)
          ));
          return {
            ...touchChat(current),
            sessionId: queuedTurn.sessionId || current.sessionId,
            attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
            items: [...current.items, {
              id: `user-${turn.id}`,
              type: "user",
              text: turn.text,
              planMode: turn.planMode === true,
              attachments: storedAttachments,
              queuedFrom: true,
              queueStatus: "steering",
              clientTurnId: turn.id,
               queueEnvelope: { planMode: turn.planMode === true, provider: turn.provider, providerLabel: turn.providerLabel, model: turn.model, contextLimit: turn.contextLimit, projectPath: queuedTurn.projectPath, projectType: queuedTurn.projectType, sessionId: queuedTurn.sessionId, laneId: queuedTurn.queueLaneId, computerUseRequested: turn.computerUseRequested, computerUseVisualTheme: turn.computerUseVisualTheme, computerUseVisualAccent: turn.computerUseVisualAccent },
              createdAt: new Date().toISOString(),
            }],
          };
        });
        const persistStartedAt = performance.now();
        try {
          trace("persist_started");
          await persistChatsNow();
          trace("persist_resolved", { waitMs: performance.now() - persistStartedAt });
        } catch (cause) {
          trace("persist_rejected", {
            waitMs: performance.now() - persistStartedAt,
            errorType: cause instanceof Error ? cause.name : typeof cause,
          });
          const message = cause instanceof Error ? cause.message : String(cause);
          setError(message);
          notifyFailure?.("send", message);
          queueArbitratorRef.current.settle(turn.id, {
            acceptedSteer: false,
            stopRequested: false,
            cancelled: true,
          }, sendingRef.current || queueRef.current.length > 0);
          updateChat(ownerChatId, (current) => ({
            ...touchChat(current),
            items: current.items.filter((item) => item.type !== "user" || item.clientTurnId !== turn.id),
          }));
          return "not_accepted";
        }
      } else {
        trace("persist_skipped_existing", { chatId: ownerChatId, sessionId: queuedTurn.sessionId });
      }
      steerIntentRef.current.set(turn.id, queuedTurn);
      const activeTurn = sendingRef.current ? currentTurnRef.current : continuedTurn;
      const targetClientTurnId = activeTurn
        && (turn.planMode === true) === ((sendingRef.current ? currentTurnRef.current?.planMode : visibleOwnerChat?.planMode) === true)
        ? activeTurn.clientTurnId : undefined;
      const dispatchQueuedAt = performance.now();
      const pendingDispatches = ++queueDispatchPendingRef.current;
      trace("dispatch_wait_started", { targetClientTurnId, pendingDispatches });
      const dispatch = queueDispatchTailRef.current
        .catch(() => undefined)
        .then(async () => {
          queueDispatchPendingRef.current = Math.max(0, queueDispatchPendingRef.current - 1);
          trace("dispatch_wait_resolved", {
            targetClientTurnId,
            waitMs: performance.now() - dispatchQueuedAt,
            pendingDispatches: queueDispatchPendingRef.current,
          });
          if (stopRequestedRef.current) {
            trace("dispatch_skipped_stop", { targetClientTurnId });
            return null;
          }
          trace("rpc_started", { targetClientTurnId });
          const rpcStartedAt = performance.now();
          try {
            const result = await recordAgentRunQueued(endpoint, {
              sessionId: queuedTurn.sessionId,
              laneId: queuedTurn.queueLaneId,
              clientTurnId: turn.id,
              targetClientTurnId,
              planMode: turn.planMode === true,
              message: turn.text,
              attachments: serializeChatAttachments(turn.attachments),
              provider: turn.provider,
              providerLabel: turn.providerLabel,
              model: turn.model,
              projectPath: queuedTurn.projectPath,
              projectRoot: queuedTurn.projectPath,
              projectType: queuedTurn.projectType,
            });
            trace("rpc_ack", {
              targetClientTurnId,
              durationMs: performance.now() - rpcStartedAt,
              accepted: result?.accepted === true,
              mode: result?.mode,
            });
            if (result?.accepted !== true) trace("rpc_rejected", { targetClientTurnId });
            return result;
          } catch (cause) {
            trace("rpc_failed", {
              targetClientTurnId,
              durationMs: performance.now() - rpcStartedAt,
              errorType: cause instanceof Error ? cause.name : typeof cause,
            });
            return null;
          }
        });
      queueDispatchTailRef.current = dispatch.then(() => undefined, () => undefined);
       let queueResult = await dispatch;
       if (cancellationGeneration !== queueCancellationGenerationRef.current) {
         const cancellation = queueResult?.queueId && queuedTurn.queueLaneId
           ? await cancelAgentRunFollowup(endpoint, queueResult.queueId, { sessionId: queuedTurn.queueLaneId }).catch(() => null)
           : null;
         const confirmed = cancellation?.status === "cancelled";
         if (!confirmed) {
           // Admission can finish after Cancel all. Preserve that unresolved
           // delivery instead of claiming the server cancelled it.
           queuedTurn.queueId = queueResult?.queueId;
           queuedTurn.queueSequence = queueResult?.sequence;
           queuedTurn.queueStatus = queuedTurn.queueId ? "paused" : "delivery_unverified";
           queueRef.current = [...queueRef.current.filter(item => item.id !== turn.id), queuedTurn];
           setQueued([...queueRef.current]);
           setError(t("chat.queueCancellationFailed"));
         }
         updateChat(ownerChatId, (current) => ({
           ...touchChat(current),
           items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
             ? { ...item, queueStatus: confirmed ? "cancelled" : queuedTurn.queueStatus,
                 queueEnvelope: { ...item.queueEnvelope, queueId: queuedTurn.queueId, sequence: queuedTurn.queueSequence } }
             : item),
         }));
         steerIntentRef.current.delete(turn.id);
         await reservation.decision;
         return "failed";
       }
       // A steer accepted concurrently with Stop would otherwise disappear
       // with the cancelled active turn. Re-submit the same immutable input to
       // the durable follow-up lane before releasing the reservation.
       if (stopRequestedRef.current && queueResult?.accepted === true && queueResult.mode === "steer") {
         queueResult = await recordAgentRunQueued(endpoint, {
           sessionId: queuedTurn.sessionId,
           laneId: queuedTurn.queueLaneId,
           clientTurnId: turn.id,
           planMode: turn.planMode === true,
           message: turn.text,
           attachments: serializeChatAttachments(turn.attachments),
           provider: turn.provider,
           providerLabel: turn.providerLabel,
           model: turn.model,
           projectPath: queuedTurn.projectPath,
           projectRoot: queuedTurn.projectPath,
           projectType: queuedTurn.projectType,
         }).catch(() => null);
       }
       const backpressured = queueResult?.accepted !== true;
       queueArbitratorRef.current.settle(turn.id, {
         acceptedSteer: queueResult?.accepted === true && queueResult.mode === "steer",
         stopRequested: stopRequestedRef.current,
       }, sendingRef.current || stopRequestedRef.current || queueRef.current.length > 0);
      const arbitration = await reservation.decision;
       if (arbitration === "steered") {
          const persistStartedAt = performance.now();
          trace("persist_started");
          void persistChatsNow?.().then(() => {
            trace("persist_resolved", { waitMs: performance.now() - persistStartedAt });
          }).catch((cause) => {
           trace("persist_rejected", { waitMs: performance.now() - persistStartedAt, errorType: cause instanceof Error ? cause.name : typeof cause });
           // Runtime admission already succeeded. A transcript save failure
           // cannot retract the input or turn an accepted steer into a retry.
           const message = cause instanceof Error ? cause.message : String(cause);
           setError(message);
           notifyFailure?.("send", message);
         });
         return "steered";
       }
       if (arbitration === "drop") {
         steerIntentRef.current.delete(turn.id);
         return "failed";
       }
       const durableTurn: QueuedTurn = {
         ...queuedTurn,
         queueId: queueResult?.queueId,
         queueSequence: queueResult?.sequence,
         queueStatus: stopRequestedRef.current ? "paused" : backpressured ? "waiting_for_resources" : "queued",
        };
       steerIntentRef.current.delete(turn.id);
       queueRef.current.push(durableTurn);
       queueRef.current.sort((a, b) => (a.queueSequence ?? Number.MAX_SAFE_INTEGER) - (b.queueSequence ?? Number.MAX_SAFE_INTEGER));
       updateChat(ownerChatId, (current) => ({ ...touchChat(current), items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id ? { ...item, queueStatus: durableTurn.queueStatus, queueEnvelope: { ...item.queueEnvelope, queueId: durableTurn.queueId, sequence: durableTurn.queueSequence } } : item) }));
       await persistChatsNow?.().catch((cause) => {
         const message = cause instanceof Error ? cause.message : String(cause);
         setError(message);
         notifyFailure?.("send", message);
       });
      setQueued([...queueRef.current]);
      void refreshRuntimeRuns(false).catch(() => undefined);
      // The active run may have reached its finally block while the queue
      // request was in flight. In that race, take ownership of this queued
      // turn immediately; runTurnNow re-checks sendingRef to prevent a
      // concurrent drain from starting a second runner.
       if (!backpressured && !stopRequestedRef.current && (arbitration === "start" || !sendingRef.current) && queueRef.current[0]?.id === durableTurn.id) {
         queueRef.current.shift();
        setQueued([...queueRef.current]);
        queueArbitratorRef.current.runnerStarted();
         void submitTurn(durableTurn);
       } else if (!sendingRef.current && !stopRequestedRef.current && queueRef.current[0]?.queueStatus === "queued") {
         void resumeQueuedTurns();
      }
      return "queued";
    }

    trace("submit_direct");
    const chatId = visibleOwnerChatId;
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
         if (!succeeded && next.queueStatus === "waiting_for_resources") {
           if (!queueRef.current.some((item) => item.id === next?.id)) queueRef.current.unshift(next);
           setQueued([...queueRef.current]);
           break;
         }
        if (isInitialTurn) {
          initialTurnSucceeded = succeeded;
          isInitialTurn = false;
        }
        if (stopRequestedRef.current) {
          break;
        }
        const queuedCandidate = queueRef.current[0];
        if (queuedCandidate?.queueStatus === "queued" && await hasPendingQuestionForTurn(queuedCandidate)) {
          break;
        }
        next = takeNextRunnableQueuedTurn(queueRef.current);
        setQueued([...queueRef.current]);
      }
      return initialTurnSucceeded ? "started" : "failed";
    } finally {
      setQueued([...queueRef.current]);
      sendingRef.current = false;
      setSending(false);
      setStopRequested(false);
      stopRequestedRef.current = false;
    }
  }

  async function enqueueFollowupTurn(turn: QueuedTurn): Promise<SubmitTurnResult> {
    const ownerChatId = turn.chatId && getChatById(turn.chatId) ? turn.chatId : ensureActiveChat();
    const ownerChat = getChatById(ownerChatId);
    const queuedTurn: QueuedTurn = {
      ...turn,
      queuedFrom: true,
      chatId: ownerChatId,
      sessionId: turn.sessionId || ownerChat?.sessionId || sessionId || undefined,
      projectPath: turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
      projectType: turn.projectType || ownerChat?.projectType || ((turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath) ? "unity" : "general"),
      queueLaneId: ownerChatId,
      queueStatus: "queued",
    };
    const trace = createSteerDeliveryTrace(traceSteerDelivery, {
      chatId: ownerChatId,
      sessionId: queuedTurn.sessionId,
      clientTurnId: turn.id,
    });
    trace("submit_received", { pendingDispatches: queueDispatchPendingRef.current, requestedDeliveryMode: "queue" });
    const existing = ownerChat?.items.some((item) => item.type === "user" && item.clientTurnId === turn.id);
    if (!existing && !turn.questionId) {
      updateChat(ownerChatId, (current) => {
        const attachmentPayloads = { ...(current.attachmentPayloads || {}) };
        const storedAttachments = turn.attachments.map((attachment) => persistAttachmentReference(attachment, attachmentPayloads));
        return {
          ...touchChat(current),
          sessionId: queuedTurn.sessionId || current.sessionId,
          attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
          items: [...current.items, {
            id: `user-${turn.id}`,
            type: "user",
            text: turn.text,
            planMode: turn.planMode === true,
            attachments: storedAttachments,
            queuedFrom: true,
            composerQueued: true,
            queueStatus: "queued",
            clientTurnId: turn.id,
            queueEnvelope: {
              planMode: turn.planMode === true,
              provider: turn.provider,
              providerLabel: turn.providerLabel,
              model: turn.model,
              contextLimit: turn.contextLimit,
              projectPath: queuedTurn.projectPath,
              projectType: queuedTurn.projectType,
              sessionId: queuedTurn.sessionId,
              laneId: queuedTurn.queueLaneId,
              computerUseRequested: turn.computerUseRequested,
              computerUseVisualTheme: turn.computerUseVisualTheme,
              computerUseVisualAccent: turn.computerUseVisualAccent,
            },
            createdAt: new Date().toISOString(),
          }],
        };
      });
      {
        const persistStartedAt = performance.now();
        try {
          trace("persist_started", { requestedDeliveryMode: "queue" });
          await persistChatsNow();
          trace("persist_resolved", { requestedDeliveryMode: "queue", waitMs: performance.now() - persistStartedAt });
        } catch (cause) {
          trace("persist_rejected", {
            requestedDeliveryMode: "queue",
            waitMs: performance.now() - persistStartedAt,
            errorType: cause instanceof Error ? cause.name : typeof cause,
          });
          updateChat(ownerChatId, (current) => ({
            ...touchChat(current),
            items: current.items.filter((item) => item.type !== "user" || item.clientTurnId !== turn.id),
          }));
          const message = cause instanceof Error ? cause.message : String(cause);
          setError(message);
          notifyFailure?.("send", message);
          return "not_accepted";
        }
      }
    }

    trace("rpc_started", { requestedDeliveryMode: "queue" });
    const rpcStartedAt = performance.now();
    const result = await recordAgentRunQueued(endpoint, {
      sessionId: queuedTurn.sessionId,
      laneId: queuedTurn.queueLaneId,
      clientTurnId: queuedTurn.id,
      planMode: queuedTurn.planMode === true,
      message: queuedTurn.text,
      attachments: serializeChatAttachments(queuedTurn.attachments),
      provider: queuedTurn.provider,
      providerLabel: queuedTurn.providerLabel,
      model: queuedTurn.model,
      projectPath: queuedTurn.projectPath,
      projectRoot: queuedTurn.projectPath,
      projectType: queuedTurn.projectType,
    }).catch((cause) => {
      trace("rpc_failed", {
        requestedDeliveryMode: "queue",
        durationMs: performance.now() - rpcStartedAt,
        errorType: cause instanceof Error ? cause.name : typeof cause,
      });
      return null;
    });
    trace("rpc_ack", {
      requestedDeliveryMode: "queue",
      durationMs: performance.now() - rpcStartedAt,
      accepted: result?.accepted === true,
      mode: result?.mode,
    });
    if (!result) {
      queuedTurn.queueStatus = "delivery_unverified";
    } else if (result.accepted !== true || result.mode !== "followup" || !result.queueId) {
      trace("rpc_rejected", { requestedDeliveryMode: "queue" });
      queuedTurn.queueStatus = "waiting_for_resources";
    } else {
      queuedTurn.queueId = result.queueId;
      queuedTurn.queueSequence = result.sequence;
      queuedTurn.queueStatus = "queued";
    }
    queueRef.current = queueRef.current.filter((candidate) => candidate.id !== queuedTurn.id);
    queueRef.current.push(queuedTurn);
    queueRef.current.sort((a, b) => (a.queueSequence ?? Number.MAX_SAFE_INTEGER) - (b.queueSequence ?? Number.MAX_SAFE_INTEGER) || a.id.localeCompare(b.id));
    setQueued([...queueRef.current]);
    updateChat(ownerChatId, (current) => ({
      ...touchChat(current),
      items: current.items.map((item) => item.type === "user" && item.clientTurnId === queuedTurn.id
        ? {
          ...item,
          queuedFrom: true,
          queueStatus: queuedTurn.queueStatus,
          queueEnvelope: {
            ...item.queueEnvelope,
            queueId: queuedTurn.queueId,
            sequence: queuedTurn.queueSequence,
          },
        }
        : item),
    }));
    const persistStartedAt = performance.now();
    trace("persist_started", { requestedDeliveryMode: "queue" });
    void persistChatsNow?.().then(() => {
      trace("persist_resolved", { requestedDeliveryMode: "queue", waitMs: performance.now() - persistStartedAt });
    }).catch((cause) => {
      trace("persist_rejected", {
        requestedDeliveryMode: "queue",
        waitMs: performance.now() - persistStartedAt,
        errorType: cause instanceof Error ? cause.name : typeof cause,
      });
      // The durable follow-up and in-memory queue row remain authoritative;
      // transcript failure must never erase or relabel accepted work.
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      notifyFailure?.("send", message);
    });
    if (!sendingRef.current && !stopRequestedRef.current && queueRef.current[0]?.id === queuedTurn.id) {
      void resumeQueuedTurns();
    }
    return result?.accepted === true && result.mode === "followup" && Boolean(result.queueId) ? "queued" : "not_accepted";
  }

  async function removeQueuedTurn(turnId: string): Promise<boolean> {
    const turn = queueRef.current.find((candidate) => candidate.id === turnId);
    if (!turn) return false;
    if (!turn.queueId && turn.queueStatus === "delivery_unverified") return false;
    if (turn.queueId) {
      const cancelled = await cancelAgentRunFollowup(endpoint, turn.queueId, {
        sessionId: turn.queueLaneId || turn.chatId || "",
      }).catch(() => null);
      if (cancelled?.status !== "cancelled") return false;
    }
    queueRef.current = queueRef.current.filter((candidate) => candidate.id !== turnId);
    setQueued([...queueRef.current]);
    const chatId = turn.chatId || turn.queueLaneId;
    if (chatId) {
      updateChat(chatId, (current) => ({
        ...touchChat(current),
        items: current.items.map((item) => item.type === "user" && item.clientTurnId === turnId
          ? { ...item, queueStatus: "cancelled" }
          : item),
      }));
      await persistChatsNow?.().catch(() => undefined);
    }
    return true;
  }

  async function editQueuedTurn(turnId: string): Promise<QueuedTurn | null> {
    const current = queueRef.current.find((candidate) => candidate.id === turnId);
    if (!current || !await removeQueuedTurn(turnId)) return null;
    // Editing withdraws the queued send; only a new composer submission sends it again.
    return current;
  }

  async function steerQueuedTurn(turnId: string): Promise<SubmitTurnResult> {
    const current = queueRef.current.find((candidate) => candidate.id === turnId);
    if (!current) return "failed";
    if (current.queueStatus === "delivery_unverified" && !current.queueId) return "failed";
    if (current.queueId) {
      const cancelled = await cancelAgentRunFollowup(endpoint, current.queueId, {
        sessionId: current.queueLaneId || current.chatId || "",
      }).catch(() => null);
      if (cancelled?.status !== "cancelled") return "failed";
    }
    queueRef.current = queueRef.current.filter((candidate) => candidate.id !== turnId);
    setQueued([...queueRef.current]);
    const chatId = current.chatId || current.queueLaneId;
    if (chatId) {
      updateChat(chatId, (chat) => ({
        ...touchChat(chat),
        items: chat.items.map((item) => item.type === "user" && item.clientTurnId === turnId
          ? { ...item, queueStatus: "cancelled" }
          : item),
      }));
      await persistChatsNow?.().catch(() => undefined);
    }
    return submitTurn({
      ...current,
      id: createClientTurnId(current.id),
      queueId: undefined,
      queueSequence: undefined,
      queueStatus: undefined,
      queuedFrom: false,
    }, { deliveryMode: "steer" });
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
       setQueued([...queueRef.current]);
      sendingRef.current = false;
      setSending(false);
      setStopRequested(false);
      stopRequestedRef.current = false;
    }
  }

  async function runBackgroundTurn(chatId: string, turn: QueuedTurn): Promise<boolean> {
    const chat = getChatById(chatId);
    const scopeKey = `${turn.sessionId || chat?.sessionId || ""}:${turn.projectPath || chat?.projectPath || ""}`;
    const scopeCount = [...backgroundTurnAbortRefs.current.keys()].filter((id) => id.startsWith(`${scopeKey}:`)).length;
    if (
      !turn.goalDelivery?.deliveryId
      || [...backgroundTurnAbortRefs.current.keys()].some((id) => id.endsWith(`:${turn.id}`))
      || backgroundTurnAbortRefs.current.size >= MAX_BACKGROUND_TURNS
      || scopeCount >= MAX_BACKGROUND_TURNS_PER_SCOPE
    ) {
      return false;
    }
    const abortController = new AbortController();
    backgroundTurnAbortRefs.current.set(`${scopeKey}:${turn.id}`, abortController);
    try {
      return await runSingleTurn(chatId, turn, {
        background: true,
        abortController,
        baseItems: stripTransientConversationItems(chat?.items || []),
        sessionId: turn.sessionId || chat?.sessionId || undefined,
      });
    } finally {
      backgroundTurnAbortRefs.current.delete(`${scopeKey}:${turn.id}`);
    }
  }

  async function runSingleTurn(chatId: string, turn: QueuedTurn, options?: InternalRunSingleTurnOptions): Promise<boolean> {
    const startedAt = Date.now();
    const background = options?.background === true;
    const abortController = options?.abortController || new AbortController();
    let userItemId = "";
    let runtimeRequestStarted = false;
    if (!background) {
      activeTurnAbortRef.current = abortController;
      const activeTurn = {
        clientTurnId: turn.id,
        sessionId: turn.sessionId || getChatById(chatId)?.sessionId,
        text: turn.text,
        planMode: turn.planMode === true,
        startedAt,
        providerLabel: turn.providerLabel,
        model: turn.model,
        computerUseRequested: turn.computerUseRequested,
      };
      currentTurnRef.current = activeTurn;
      setCurrentTurn(activeTurn);
    }
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
      const baseItems = (prepared?.baseItems ?? options?.baseItems ?? chat?.items ?? []).filter(
        (item) => (item.type !== "user" || item.clientTurnId !== turn.id)
          && !(item.type === "user" && item.composerQueued),
      );
      const shouldGenerateTitle = shouldGenerateChatTitle(chat, baseItems.length, background);
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
          .filter((item) => item.type === "user" || item.type === "question_answer" || item.type === "agent" || item.type === "compact" || item.type === "subagent")
          .map((item) => item.id),
      );
      const summarizedItemIds = new Set(
        baseItems
          .filter((item) => item.type === "user" || item.type === "question_answer" || item.type === "agent" || item.type === "compact")
          .map((item) => item.id),
      );
      const userItem: Extract<ConversationItem, { type: "user" }> = {
        id: turn.goalDelivery?.userItemId || `user-${turn.id}`,
        type: "user",
        text: turn.text,
        planMode: turn.planMode === true,
        attachments: turn.attachments,
        clientTurnId: turn.queueId ? turn.id : undefined,
        createdAt: new Date(startedAt).toISOString(),
      };
      userItemId = userItem.id;
      const streamingItem: ConversationItem = {
        id: `stream-${turn.id}`,
        type: "streaming",
        clientTurnId: turn.id,
        text: "",
        phase: "waiting_for_model",
        providerLabel: turn.providerLabel,
        model: turn.model,
        createdAt: new Date(startedAt).toISOString(),
      };
      const message = turn.text;
      if (!background) {
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
            titleSource: current.title ? current.titleSource : "auto",
            attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
            items: [
              ...stripTransientConversationItems(options?.baseItems ?? current.items).filter(
                (item) => item.id !== userItem.id && item.id !== turn.goalDelivery?.agentItemId,
              ),
              ...(turn.questionId ? [] : [storedUserItem]),
              streamingItem,
            ],
          };
        });
        // A recovered runtime reply must have a durable owning chat and the
        // complete user message even if the app is killed during this turn.
        await persistChatsNow();
      }
      const computerUseGrant = !background && turn.computerUseRequested
        ? await issueComputerUseTurnGrant(targetEndpoint, {
            sessionId: chatSessionId || undefined,
            clientTurnId: turn.id,
            projectRoot: chat?.projectPath || activeRuntimeProjectPath || undefined,
          })
        : null;
      if (abortController.signal.aborted) throw new Error("Request cancelled.");
      runtimeRequestStarted = true;
      if (!background) dispatchedTurnAbortRef.current = abortController;
      const response = await sendAgentMessage(targetEndpoint, messageForModel, chatSessionId || undefined, history, chatAgentName, {
        signal: abortController.signal,
        planMode: turn.planMode === true,
        attachments: serializeChatAttachments(requestAttachments),
        chatId,
        projectPath: chat?.projectPath || activeRuntimeProjectPath || undefined,
        projectType: turn.projectType || chat?.projectType || ((chat?.projectPath || activeRuntimeProjectPath) ? "unity" : "general"),
        provider: turn.provider,
        providerLabel: turn.providerLabel,
        model: turn.model,
        contextLimit: turn.contextLimit,
        maxAgenticTurns: background ? DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS : undefined,
        clientTurnId: turn.id,
        goalDeliveryId: turn.goalDelivery?.deliveryId,
        computerUseRequested: !background && Boolean(turn.computerUseRequested),
        computerUseGrantId: computerUseGrant?.grantId,
        computerUseVisualTheme: turn.computerUseVisualTheme,
         computerUseVisualAccent: turn.computerUseVisualAccent,
         followupQueueId: turn.queueId,
         followupLaneId: turn.queueLaneId || turn.chatId,
         compactionRecovery: collectCompactionRecovery(baseItems),
        });
      const consumedSteerInputIds = new Set(
        (response.consumedSteerInputIds || []).filter((inputId) => typeof inputId === "string" && inputId.length > 0),
      );
      for (const inputId of consumedSteerInputIds) steerIntentRef.current.delete(inputId);
      const deferredSteerFollowups = new Map(
        (response.deferredSteerFollowups || [])
          .filter((item) => item.inputId && item.queueId)
          .map((item) => [item.inputId, item] as const),
      );
      const deferredSteerBackpressure = new Map(
        (response.deferredSteerFollowupOutcomes || [])
          .filter((item) => item.inputId && !deferredSteerFollowups.has(item.inputId) && item.status !== "pending")
          .map((item) => [item.inputId, item] as const),
      );
      const terminalUnverifiedSteerInputIds = new Set(
        [...steerIntentRef.current.entries()]
          .filter(([inputId, intent]) => (
            intent.chatId === chatId
            && intent.sessionId === chatSessionId
            && !consumedSteerInputIds.has(inputId)
            && !deferredSteerFollowups.has(inputId)
            && !deferredSteerBackpressure.has(inputId)
          ))
          .map(([inputId]) => inputId),
      );
      for (const inputId of terminalUnverifiedSteerInputIds) steerIntentRef.current.delete(inputId);
      let deferredQueueChanged = false;
      for (const [inputId, deferred] of deferredSteerFollowups) {
        const intent = steerIntentRef.current.get(inputId);
        if (!intent || queueRef.current.some((queuedTurn) => queuedTurn.id === inputId)) continue;
        queueRef.current.push({
          ...intent,
          queueId: deferred.queueId,
          queueSequence: deferred.sequence,
          queueStatus: "queued",
        });
        steerIntentRef.current.delete(inputId);
        deferredQueueChanged = true;
      }
      for (const [inputId] of deferredSteerBackpressure) {
        const intent = steerIntentRef.current.get(inputId);
        if (!intent || queueRef.current.some((queuedTurn) => queuedTurn.id === inputId)) continue;
        queueRef.current.push({
          ...intent,
          queueStatus: "waiting_for_resources",
        });
        steerIntentRef.current.delete(inputId);
        deferredQueueChanged = true;
      }
      if (deferredQueueChanged) {
        queueRef.current.sort((left, right) => (
          (left.queueSequence ?? Number.MAX_SAFE_INTEGER) - (right.queueSequence ?? Number.MAX_SAFE_INTEGER)
        ));
        setQueued([...queueRef.current]);
      }
      if (!background) {
        const visualFailureNotice = projectVisionFailureNotice(response, (key) => t(key));
        if (visualFailureNotice) {
          notifyFailure?.(visualFailureNotice.kind, visualFailureNotice.message);
        }
      }
      const providerUnavailable = response.backgroundGoalSkipped === true
        && response.status === "provider_unreachable"
        && Boolean(response.providerWarningKey);
      const backgroundCapacityDeferred = response.backgroundGoalDeferred === true
        && response.status === "background_capacity";
      if (
        (providerUnavailable || backgroundCapacityDeferred)
        && Boolean(turn.goalDelivery?.deliveryId)
        && response.goalDeliveryId === turn.goalDelivery?.deliveryId
      ) {
        const transientRemoved = clearTurnTransientItems(chatId, turn.id, userItem.id);
        if (transientRemoved && persistChatsNow) {
          await persistChatsNow().catch(() => undefined);
        }
        refreshBackgroundGoals();
        await Promise.allSettled([
          refresh(targetEndpoint),
          refreshRuntimeRuns(false, targetEndpoint),
        ]);
        return false;
      }
      const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      const responseRecovery = mergeCompactionRecovery(response.contextCompaction?.recovery);
      // Persist runtime facts and its original reply; localization belongs to the card.
      const durableTimeline = materializeRuntimeTimeline(response);
      const durableResponse = {
        ...response,
        ...(response.timeline ? { timeline: durableTimeline } : {}),
        ...(turn.questionId ? { continuationSource: "question_answered" } : {}),
      };
      if (background) {
        const responseItemId = turn.goalDelivery?.agentItemId || response.turnId || response.turn_id;
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
            sessionId: current.sessionId || response.sessionId || response.session_id || chatSessionId,
            title: current.title || (message.length > 24 ? `${message.slice(0, 24)}...` : message),
            titleSource: current.title ? current.titleSource : "auto",
            attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
            items: [
              ...current.items.filter(
                (item) => item.id !== userItem.id
                  && item.id !== responseItemId
                  && !(item.type === "streaming" && item.clientTurnId === turn.id),
              ),
              ...(turn.questionId ? [] : [storedUserItem]),
              {
                id: responseItemId,
                type: "agent",
                response: durableResponse,
                timeline: durableTimeline,
                elapsedSeconds,
                providerLabel: turn.providerLabel,
                model: turn.model,
                compactionRecovery: responseRecovery,
                ...(response.completedAt ? { completedAt: response.completedAt } : {}),
              },
            ],
          };
        });
        await Promise.allSettled([
          refresh(targetEndpoint),
          refreshRuntimeRuns(false, targetEndpoint),
        ]);
        return true;
      }
      let midTurnCompactionApplied = false;
      updateChat(chatId, (current) => ({
        ...applyRuntimeResponseToChat(current),
      }));
      if (shouldGenerateTitle && chatTitleRunSucceeded(response)) {
        const fallbackTitle = message.length > 24 ? `${message.slice(0, 24)}...` : message;
        void maybeGenerateChatTitle(chatId, message, turn.provider, turn.model, fallbackTitle);
      }
      if (midTurnCompactionApplied && persistChatsNow) {
        await persistChatsNow();
      }
      await refresh(targetEndpoint);
      await refreshRuntimeRuns(false, targetEndpoint);
      return true;

      function applyRuntimeResponseToChat(current: ChatThread): ChatThread {
        const responseItemId = turn.goalDelivery?.agentItemId || response.turnId || response.turn_id;
        const streamedItem = current.items.find((item): item is Extract<ConversationItem, { type: "streaming" }> => item.type === "streaming" && item.clientTurnId === turn.id);
        const streamedTimeline = streamedItem?.timeline || [];
        const mergedTimeline = mergeRuntimeTimelines(streamedTimeline, durableTimeline);
        const responseForChat = mergedTimeline.length
          ? { ...durableResponse, timeline: mergedTimeline }
          : durableResponse;
        let durableItems = stripTransientConversationItems(current.items).filter(
          (item) => item.id !== responseItemId,
        ).map((item) => {
          if (item.type !== "user") return item;
          const deferred = deferredSteerFollowups.get(item.clientTurnId || "");
          if (deferred) {
            return {
              ...item,
              queuedFrom: true,
              queueStatus: "queued" as const,
              queueEnvelope: {
                ...item.queueEnvelope,
                queueId: deferred.queueId,
                sequence: deferred.sequence,
              },
            };
          }
          if (deferredSteerBackpressure.has(item.clientTurnId || "")) {
            return {
              ...item,
              queuedFrom: true,
              queueStatus: "waiting_for_resources" as const,
            };
          }
          if (terminalUnverifiedSteerInputIds.has(item.clientTurnId || "")) {
            return {
              ...item,
              queuedFrom: true,
              queueStatus: "delivery_unverified" as const,
            };
          }
          return item.clientTurnId === turn.id || consumedSteerInputIds.has(item.clientTurnId || "")
            ? { ...item, queuedFrom: false, queueStatus: undefined, queueEnvelope: undefined }
            : item;
        });
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
            summarizedItemIds: [...summarizedItemIds],
            contextUsageBoundaryItemId: contextUsageBoundaryItemId(durableItems),
            compactionRecovery: mergeCompactionRecovery(
              collectCompactionRecovery(durableItems),
              responseRecovery,
            ),
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
          const nativeApplied = runtimeCompaction.applied === true && runtimeCompaction.target === "native_history";
          const failureClass = boundedRuntimeReason(runtimeCompaction.failureClass)
            || (nativeApplied ? "" : "unknown");
          const status = nativeApplied
            ? "applied"
            : failureClass === "cancelled"
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
            { id: responseItemId, type: "agent", response: responseForChat, timeline: mergedTimeline, compactionRecovery: responseRecovery, elapsedSeconds, providerLabel: turn.providerLabel, model: turn.model, ...(response.completedAt ? { completedAt: response.completedAt } : {}) },
          ],
        };
      }
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      // A broken/aborted response channel does not prove backend cancellation.
      // Preserve its accepted dialogue as an interrupted transport placeholder;
      // the exact runtime receipt owns the eventual execution status.
      const interrupted = abortController.signal.aborted || text.toLowerCase().includes("cancel");
      if (!background && turn.queueId && (!runtimeRequestStarted || text.includes("followup_queue_not_ready"))) {
        turn.queueStatus = "waiting_for_resources";
        markQueuedTurnStatus(turn.id, "waiting_for_resources");
        return false;
      }
      if (!background) {
        const chat = getChatById(chatId);
        const settlement = settleTerminalSteerDelivery(
          chat?.items || [],
          steerIntentRef.current,
          { chatId, sessionId: turn.sessionId },
        );
        if (settlement.settledIds.length) {
          const unverifiedIds = new Set(settlement.settledIds);
          updateChat(chatId, (current) => ({
            ...touchChat(current),
            items: current.items.map((item) => item.type === "user" && unverifiedIds.has(item.clientTurnId || "")
              ? { ...item, queuedFrom: true, queueStatus: "delivery_unverified" }
              : item),
          }));
          if (persistChatsNow) await persistChatsNow().catch(() => undefined);
        }
      }
      if (!background && turn.queueId) {
        updateChat(chatId, (current) => ({
          ...touchChat(current),
          items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
            ? { ...item, queuedFrom: false, queueStatus: undefined, queueEnvelope: undefined }
            : item),
        }));
      }
      if (background) {
        const transientRemoved = clearTurnTransientItems(chatId, turn.id, userItemId);
        if (transientRemoved && persistChatsNow) {
          await persistChatsNow().catch(() => undefined);
        }
        refreshBackgroundGoals();
        await Promise.allSettled([
          refresh(endpoint),
          refreshRuntimeRuns(false, endpoint),
        ]);
        return false;
      }
      if (interrupted) {
        const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
        updateChat(chatId, (current) => ({
          ...touchChat(current),
          items: current.items.map((item) => {
            if (item.type !== "streaming" || item.clientTurnId !== turn.id) return item;
            const placeholder = finalizeCancelledStreamingTurn(item, {
              sessionId: current.sessionId || turn.sessionId,
              cancelledText: text,
              elapsedSeconds,
            });
            return {
              ...placeholder,
              id: `interrupted-${turn.id}`,
              response: {
                ...placeholder.response,
                status: "interrupted",
                continuationSource: "foreground_interrupted",
                turnId: "",
                turn_id: "",
                plan: {
                  ...placeholder.response.plan,
                  summary: "",
                  reply: placeholder.response.plan.reply === text ? "" : placeholder.response.plan.reply,
                  nextStep: "interrupted",
                },
              },
            };
          }),
        }));
        if (persistChatsNow) {
          await persistChatsNow().catch(() => undefined);
        }
        setError(text);
        return false;
      }
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
      notifyFailure?.("send", text);
      return false;
    } finally {
      updateChat(chatId, (current) => {
        const items = current.items.filter(
          (item) => item.type !== "streaming" || item.clientTurnId !== turn.id,
        );
        return items.length === current.items.length ? current : { ...current, items };
      });
      if (!background) {
        if (activeTurnAbortRef.current === abortController) {
          activeTurnAbortRef.current = null;
        }
        if (dispatchedTurnAbortRef.current === abortController) {
          dispatchedTurnAbortRef.current = null;
        }
        currentTurnRef.current = null;
        setCurrentTurn(null);
      }
      streamingTurnChatRef.current.delete(turn.id);
    }
  }

  function stopCurrentRun() {
    stopRequestedRef.current = true;
    setStopRequested(true);
    for (const turn of queueRef.current) {
      markQueuedTurnStatus(turn.id, "paused");
    }
    // Keep pending turns durable and visible; only the backend terminal receipt
    // may transition the active turn to cancelled.
    // React state can lag while a send completes; the ref is the CAS owner
    // for the currently active turn and avoids cancelling a stale turn.
    const current = currentTurnRef.current;
    const controller = activeTurnAbortRef.current;
    if (controller && dispatchedTurnAbortRef.current !== controller) {
      // No backend turn has been dispatched yet. Stop local preparation and
      // let the pre-dispatch signal check prevent admission after pending I/O.
      controller.abort();
      return;
    }
    if (current?.clientTurnId) {
      void requestAgentRunCancel(endpoint, {
        sessionId: current.sessionId,
        clientTurnId: current.clientTurnId,
        reason: "user_stop",
      })
        .then((result) => {
          if (!result.ok) {
            setError(result.status || "Agent cancellation was not accepted.");
          }
          return refreshRuntimeRuns(false);
        })
        .catch((cause) => setError(cause instanceof Error ? cause.message : String(cause)));
    }
    // A cancel request only asks the backend to stop. Keep the owned response
    // reader alive until its real terminal receipt, including in-flight tools.
  }

  return {
    sending,
    queued,
    currentTurn,
    stopRequested,
    isRunning,
    submitTurn,
    editQueuedTurn,
    removeQueuedTurn,
    steerQueuedTurn,
    runTurnNow,
    runBackgroundTurn,
    stopCurrentRun,
    resumeQueuedTurns,
    cancelQueuedTurns,
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

function createClientTurnId(previousId: string): string {
  const generated = globalThis.crypto?.randomUUID?.();
  return generated || `${previousId}-edit-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
