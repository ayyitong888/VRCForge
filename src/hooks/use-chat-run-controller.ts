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
  projectRuntimeResponseForDisplay,
  stripTransientConversationItems,
} from "../lib/chat-thread";
import { RuntimeQueueArbitrator, takeNextRunnableQueuedTurn } from "../lib/runtime-queue-arbitration";
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
  cancelAgentRunFollowup,
  issueComputerUseTurnGrant,
  recordAgentRunQueued,
  requestAgentRunCancel,
  sendAgentMessage,
} from "../lib/api";
import { DEFAULT_BACKGROUND_MAX_AGENTIC_TURNS } from "../lib/api/agent-runtime";
import { projectVisionFailureNotice } from "../lib/vision-failure-notice";

export const MAX_BACKGROUND_TURNS = 2;

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
};

export type CurrentTurn = {
  clientTurnId?: string;
  sessionId?: string;
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
  persistChatsNow?: () => Promise<void>;
  chats?: ChatThread[];
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
  chats,
}: UseChatRunControllerParams) {
  const { t } = useTranslation();
  const [sending, setSending] = useState(false);
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [currentTurn, setCurrentTurn] = useState<CurrentTurn | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const queueRef = useRef<QueuedTurn[]>([]);
  const sendingRef = useRef(false);
  const currentTurnRef = useRef<CurrentTurn | null>(null);
  const stopRequestedRef = useRef(false);
  const queueArbitratorRef = useRef(new RuntimeQueueArbitrator());
  const queueDispatchTailRef = useRef<Promise<void>>(Promise.resolve());
  const queueCancellationGenerationRef = useRef(0);
  const steerIntentRef = useRef(new Map<string, QueuedTurn>());
  const streamingTurnChatRef = useRef(new Map<string, string>());
  const activeTurnAbortRef = useRef<AbortController | null>(null);
  const backgroundTurnAbortRefs = useRef(new Map<string, AbortController>());
  const rehydratedRef = useRef(false);

  useEffect(() => {
    if (rehydratedRef.current || !chats?.length) return;
    rehydratedRef.current = true;
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
      // An accepted steer or completed follow-up whose UI commit was
      // interrupted is intentionally not replayed. The user input stays
      // visible as delivery_unverified so a crash cannot duplicate tool work.
      if (item.queueStatus === "delivery_unverified") continue;
      if (!["queued", "waiting_for_resources", "paused"].includes(item.queueStatus || "")) continue;
      const env = item.queueEnvelope;
      restored.push({ id: item.clientTurnId, text: item.text, attachments: item.attachments || [], provider: env.provider || "", providerLabel: env.providerLabel || env.provider || "", model: env.model || "", contextLimit: env.contextLimit, chatId: chat.id, sessionId: env.sessionId || chat.sessionId, projectPath: env.projectPath || chat.projectPath, projectType: env.projectType || chat.projectType, computerUseRequested: env.computerUseRequested, computerUseVisualTheme: env.computerUseVisualTheme, computerUseVisualAccent: env.computerUseVisualAccent, queuedFrom: true, queueId: env.queueId, queueLaneId: env.laneId || chat.id, queueSequence: env.sequence, queueStatus: item.queueStatus as QueuedTurn["queueStatus"] });
    }
    restored.sort((a, b) => (a.queueSequence ?? Number.MAX_SAFE_INTEGER) - (b.queueSequence ?? Number.MAX_SAFE_INTEGER) || a.id.localeCompare(b.id));
    queueRef.current = restored;
    setQueued(restored);
    if (interruptedSteerRecovered) {
      queueMicrotask(() => void persistChatsNow?.().catch(() => undefined));
    }
    if (restored[0]?.queueStatus === "queued") {
      queueMicrotask(() => void resumeQueuedTurns());
    }
  }, [chats]);

  function isRunning() {
    return sendingRef.current;
  }

  function markQueuedTurnStatus(turnId: string, status: QueuedTurn["queueStatus"]): void {
    queueRef.current = queueRef.current.map((turn) => turn.id === turnId ? { ...turn, queueStatus: status } : turn);
    setQueued([...queueRef.current]);
    for (const chat of chats || []) {
      if (!chat.items.some((item) => item.type === "user" && item.clientTurnId === turnId)) continue;
      updateChat(chat.id, (current) => ({
        ...touchChat(current),
        items: current.items.map((item) => item.type === "user" && item.clientTurnId === turnId
          ? { ...item, queueStatus: status }
          : item),
      }));
      break;
    }
    void persistChatsNow?.().catch(() => undefined);
  }

  async function resumeQueuedTurns(): Promise<void> {
    if (sendingRef.current || stopRequestedRef.current || queueRef.current.length === 0) return;
    const head = queueRef.current[0];
    if (!head) return;
    if (head.queueStatus === "waiting_for_resources" || head.queueStatus === "delivery_unverified" || !head.queueId) {
      const result = await recordAgentRunQueued(endpoint, {
        sessionId: head.sessionId,
        laneId: head.queueLaneId || head.chatId,
        clientTurnId: head.id,
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
  }

  async function cancelQueuedTurns(): Promise<void> {
    const pending = [...queueRef.current];
    queueCancellationGenerationRef.current += 1;
    queueRef.current = [];
    queueArbitratorRef.current.stop();
    setQueued([]);
    for (const chat of chats || []) {
      if (!chat.items.some((item) => item.type === "user" && CANCELLABLE_QUEUE_STATUSES.has(item.queueStatus || ""))) continue;
      updateChat(chat.id, (current) => ({
        ...touchChat(current),
        items: current.items.map((item) => item.type === "user" && CANCELLABLE_QUEUE_STATUSES.has(item.queueStatus || "")
          ? { ...item, queueStatus: "cancelled" }
          : item),
      }));
    }
    for (const turn of pending) {
      if (turn.queueId && (turn.queueLaneId || turn.chatId)) {
        await cancelAgentRunFollowup(endpoint, turn.queueId, { sessionId: turn.queueLaneId || turn.chatId || "" }).catch(() => undefined);
      }
      for (const chat of chats || []) {
        if (!chat.items.some((item) => item.type === "user" && item.clientTurnId === turn.id)) continue;
        updateChat(chat.id, (current) => ({
          ...touchChat(current),
          items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
            ? { ...item, queueStatus: "cancelled" }
            : item),
        }));
        break;
      }
    }
    await persistChatsNow?.().catch(() => undefined);
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

  async function submitTurn(turn: QueuedTurn): Promise<SubmitTurnResult> {
    if ((sendingRef.current || queueRef.current.length > 0) && !turn.queueId) {
      const reservation = queueArbitratorRef.current.reserve(turn.id, queueRef.current.length);
      if (!reservation) return "queued";
      const cancellationGeneration = queueCancellationGenerationRef.current;
      const ownerChatId = turn.chatId && getChatById(turn.chatId) ? turn.chatId : ensureActiveChat();
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
      // Persist the immutable user intent before any network arbitration. This
      // makes queued/steered turns restart-visible and preserves attachments.
      const existing = ownerChat?.items.some(
        (item) => item.type === "user" && item.clientTurnId === turn.id,
      );
      if (!existing) {
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
              attachments: storedAttachments,
              queuedFrom: true,
              queueStatus: "steering",
              clientTurnId: turn.id,
               queueEnvelope: { provider: turn.provider, providerLabel: turn.providerLabel, model: turn.model, contextLimit: turn.contextLimit, projectPath: queuedTurn.projectPath, projectType: queuedTurn.projectType, sessionId: queuedTurn.sessionId, laneId: queuedTurn.queueLaneId, computerUseRequested: turn.computerUseRequested, computerUseVisualTheme: turn.computerUseVisualTheme, computerUseVisualAccent: turn.computerUseVisualAccent },
              createdAt: new Date().toISOString(),
            }],
          };
        });
        try {
          await persistChatsNow?.();
        } catch (cause) {
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
      }
      steerIntentRef.current.set(turn.id, queuedTurn);
      const targetClientTurnId = sendingRef.current ? currentTurnRef.current?.clientTurnId : undefined;
      const dispatch = queueDispatchTailRef.current
        .catch(() => undefined)
        .then(async () => {
          if (stopRequestedRef.current) return null;
          return recordAgentRunQueued(endpoint, {
            sessionId: queuedTurn.sessionId,
            laneId: queuedTurn.queueLaneId,
            clientTurnId: turn.id,
            targetClientTurnId,
            message: turn.text,
            attachments: serializeChatAttachments(turn.attachments),
            provider: turn.provider,
            providerLabel: turn.providerLabel,
            model: turn.model,
            projectPath: queuedTurn.projectPath,
            projectRoot: queuedTurn.projectPath,
            projectType: queuedTurn.projectType,
          }).catch(() => null);
        });
      queueDispatchTailRef.current = dispatch.then(() => undefined, () => undefined);
       let queueResult = await dispatch;
       if (cancellationGeneration !== queueCancellationGenerationRef.current) {
         if (queueResult?.queueId && queuedTurn.queueLaneId) {
           await cancelAgentRunFollowup(endpoint, queueResult.queueId, { sessionId: queuedTurn.queueLaneId }).catch(() => undefined);
         }
         updateChat(ownerChatId, (current) => ({
           ...touchChat(current),
           items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
             ? { ...item, queueStatus: "cancelled" }
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
          try {
            await persistChatsNow?.();
          } catch (cause) {
            steerIntentRef.current.delete(turn.id);
            updateChat(ownerChatId, (current) => ({
             ...touchChat(current),
             items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
               ? { ...item, queuedFrom: true, queueStatus: "delivery_unverified" }
               : item),
           }));
           const message = cause instanceof Error ? cause.message : String(cause);
           setError(message);
           notifyFailure?.("send", message);
           return "queued";
         }
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
    if (
      !turn.goalDelivery?.deliveryId
      || backgroundTurnAbortRefs.current.has(turn.id)
      || backgroundTurnAbortRefs.current.size >= MAX_BACKGROUND_TURNS
    ) {
      return false;
    }
    const abortController = new AbortController();
    backgroundTurnAbortRefs.current.set(turn.id, abortController);
    try {
      const chat = getChatById(chatId);
      return await runSingleTurn(chatId, turn, {
        background: true,
        abortController,
        baseItems: stripTransientConversationItems(chat?.items || []),
        sessionId: turn.sessionId || chat?.sessionId || undefined,
      });
    } finally {
      backgroundTurnAbortRefs.current.delete(turn.id);
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
         (item) => item.type !== "user" || item.clientTurnId !== turn.id,
       );
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
        queueStatus: turn.queueId ? "queued" : undefined,
        clientTurnId: turn.queueId ? turn.id : undefined,
        queueEnvelope: turn.queueId ? {
          provider: turn.provider,
          providerLabel: turn.providerLabel,
          model: turn.model,
          contextLimit: turn.contextLimit,
          projectPath: turn.projectPath,
          projectType: turn.projectType,
          sessionId: turn.sessionId,
          laneId: turn.queueLaneId || turn.chatId,
          computerUseRequested: turn.computerUseRequested,
          computerUseVisualTheme: turn.computerUseVisualTheme,
          computerUseVisualAccent: turn.computerUseVisualAccent,
          queueId: turn.queueId,
          sequence: turn.queueSequence,
        } : undefined,
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
      }
      const computerUseGrant = !background && turn.computerUseRequested
        ? await issueComputerUseTurnGrant(targetEndpoint, {
            sessionId: chatSessionId || undefined,
            clientTurnId: turn.id,
            projectRoot: chat?.projectPath || activeRuntimeProjectPath || undefined,
          })
        : null;
      runtimeRequestStarted = true;
      const response = await sendAgentMessage(targetEndpoint, messageForModel, chatSessionId || undefined, history, chatAgentName, {
        signal: abortController.signal,
        attachments: serializeChatAttachments(requestAttachments),
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
      const responseForDisplay = projectRuntimeResponseForDisplay(response, (key) => t(key));
      const durableTimeline = materializeRuntimeTimeline(responseForDisplay);
      const durableResponse = responseForDisplay.timeline
        ? { ...responseForDisplay, timeline: durableTimeline }
        : responseForDisplay;
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
            attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
            items: [
              ...current.items.filter(
                (item) => item.id !== userItem.id
                  && item.id !== responseItemId
                  && !(item.type === "streaming" && item.clientTurnId === turn.id),
              ),
              storedUserItem,
              {
                id: responseItemId,
                type: "agent",
                response: durableResponse,
                timeline: durableTimeline,
                elapsedSeconds,
                providerLabel: turn.providerLabel,
                model: turn.model,
                createdAt: new Date().toISOString(),
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
            { id: responseItemId, type: "agent", response: responseForChat, timeline: mergedTimeline, elapsedSeconds, providerLabel: turn.providerLabel, model: turn.model, createdAt: new Date().toISOString() },
          ],
        };
      }
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : String(cause);
      const cancelled = abortController.signal.aborted || text.trim().toLowerCase() === "request cancelled.";
      if (!background && turn.queueId && (!runtimeRequestStarted || text.includes("followup_queue_not_ready"))) {
        turn.queueStatus = "waiting_for_resources";
        markQueuedTurnStatus(turn.id, "waiting_for_resources");
        return false;
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
      if (cancelled) {
        const cancelledText = t("chat.cancelled");
        const completedAt = new Date().toISOString();
        const elapsedSeconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
        updateChat(chatId, (current) => ({
          ...touchChat(current),
          items: current.items.map((item) => item.type === "streaming" && item.clientTurnId === turn.id
            ? finalizeCancelledStreamingTurn(item, {
                sessionId: current.sessionId || turn.sessionId,
                cancelledText,
                elapsedSeconds,
                completedAt,
              })
            : item),
        }));
        if (persistChatsNow) {
          await persistChatsNow().catch(() => undefined);
        }
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
    // Keep pending turns durable and visible; only the backend cancel ack may
    // transition the active turn to cancelled.
    // React state can lag while a send completes; the ref is the CAS owner
    // for the currently active turn and avoids cancelling a stale turn.
    const current = currentTurnRef.current;
    if (current?.clientTurnId) {
      void requestAgentRunCancel(endpoint, {
        sessionId: current.sessionId,
        clientTurnId: current.clientTurnId,
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
