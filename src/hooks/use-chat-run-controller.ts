import { useEffect, useMemo, useRef, useState } from "react";
import { AgentTurnDeliveryUnconfirmedError, ApiError } from "../lib/api/http";
import { useTranslation } from "react-i18next";
import { markRuntimeStreamStopping } from "../lib/runtime-stream-owner";
import {
  applyAgentRuntimeDeltaToStreamingItem,
  finalizeCancelledStreamingTurn,
  normalizeAgentRuntimePhase,
  normalizeAgentRuntimeTimelineEvent,
  type AgentRuntimeDeltaEvent,
} from "../lib/chat-streaming";
import type { ChatAttachment, ChatThread, ConversationItem } from "../lib/chat-types";
import { recordLocalChatInput } from "../lib/chat-local-inputs";
import {
  isUnstartedChat,
  materializeRuntimeTimeline,
  mergeRuntimeTimelines,
  stripTransientConversationItems,
} from "../lib/chat-thread";
import { RuntimeQueueArbitrator } from "../lib/runtime-queue-arbitration";
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
  staleBranch?: boolean;
  queueTransition?: "enqueue" | "dispatch";
};

export type CurrentTurn = {
  chatId: string;
  planMode?: boolean;
  clientTurnId: string;
  sessionId?: string;
  text: string;
  startedAt: number;
  providerLabel: string;
  model: string;
  computerUseRequested?: boolean;
};

type DispatchedTurn = {
  chatId: string;
  clientTurnId: string;
  sessionId: string;
  endpoint: string;
  turnId?: string;
  confirmProviderConnection?: () => void;
  followup?: FollowupAdmission;
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
  pinnedSessionId?: string;
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
): { chat?: ChatThread; staleChat?: ChatThread; allowFallback: boolean } {
  const laneOwner = chats.find((chat) => chat.id === entry.sessionId)
    || chats.find((chat) => chat.id === existingChatId);
  if (laneOwner) return followsChatSession(laneOwner, entry.runtimeSessionId)
    ? { chat: laneOwner, allowFallback: false }
    : { staleChat: laneOwner, allowFallback: false };
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

function followsChatSession(chat: ChatThread, queuedSessionId?: string): boolean {
  return !queuedSessionId || queuedSessionId === chat.sessionId || isUnstartedChat(chat);
}

function queueLane(turn: QueuedTurn): string {
  return turn.queueLaneId || turn.chatId || turn.sessionId || "";
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
  prepareTurnContext?: (input: PrepareTurnContextInput) => Promise<PreparedTurnContext | null>;
  persistChatsNow: () => Promise<void>;
  reportChatSaveError: (cause: unknown) => void;
  traceSteerDelivery?: (event: SteerDeliveryTraceEvent) => void;
  chats?: ChatThread[];
  chatStorageReady?: boolean;
  runtimeQueueVersion?: number;
  hasPendingChildDelivery?: (sessionId: string) => boolean;
  bindRuntimeProviderConnection?: (endpoint: string, provider: string, model: string) => (() => void) | undefined;
};

type FollowupReceipt = Pick<AgentRuntimeDeltaEvent, "sessionId" | "clientTurnId" | "turnId" | "startedAt">;
type FollowupAdmission = { admitted: boolean };

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
  prepareTurnContext,
  persistChatsNow,
  reportChatSaveError,
  traceSteerDelivery,
  chats,
  chatStorageReady = true,
  runtimeQueueVersion = 0,
  hasPendingChildDelivery,
  bindRuntimeProviderConnection,
}: UseChatRunControllerParams) {
  const { t } = useTranslation();
  const [sending, setSending] = useState(false);
  const [queued, setQueued] = useState<QueuedTurn[]>([]);
  const [queueTransitions, setQueueTransitions] = useState<QueuedTurn[]>([]);
  const visibleQueue = useMemo(() => {
    if (!queueTransitions.length) return queued;
    return [
      ...queueTransitions.filter((turn) => turn.queueTransition === "dispatch"),
      ...queued.filter((turn) => !queueTransitions.some((transition) => transition.id === turn.id)),
      ...queueTransitions.filter((turn) => turn.queueTransition === "enqueue"),
    ];
  }, [queued, queueTransitions]);
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
  const dispatchedTurnRef = useRef<DispatchedTurn | null>(null);
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
    if (!chatStorageReady || !chats) return;
    let cancelled = false;
    const restored: QueuedTurn[] = [];
    let interruptedSteerRecovered = false;
    const initialRestore = !rehydratedRef.current;
    if (initialRestore) {
      rehydratedRef.current = true;
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
      const unconfirmedFollowup = item.queueStatus === "delivery_unverified"
        && item.composerQueued === true && Boolean(item.queueEnvelope.queueId);
      if (!unconfirmedFollowup && !["queued", "waiting_for_resources", "paused"].includes(item.queueStatus || "")) continue;
      const env = item.queueEnvelope;
      const staleBranch = !followsChatSession(chat, env.sessionId);
      if (staleBranch && !env.queueId) continue;
      restored.push({ planMode: item.planMode === true, id: item.clientTurnId, text: item.text, attachments: item.attachments || [], provider: env.provider || "", providerLabel: env.providerLabel || env.provider || "", model: env.model || "", contextLimit: env.contextLimit, chatId: chat.id, sessionId: env.sessionId || chat.sessionId, projectPath: env.projectPath || chat.projectPath, projectType: env.projectType || chat.projectType, computerUseRequested: env.computerUseRequested, computerUseVisualTheme: env.computerUseVisualTheme, computerUseVisualAccent: env.computerUseVisualAccent, queuedFrom: true, queueId: env.queueId, queueLaneId: env.laneId || chat.id, queueSequence: env.sequence, staleBranch, queueStatus: staleBranch ? "paused" : item.queueStatus as QueuedTurn["queueStatus"] });
    }
    queueRef.current = restored;
    setQueued(restored);
    if (interruptedSteerRecovered) void persistChatsNow?.().catch(() => undefined);
    }

    void listAgentRunFollowups(endpoint, { includeTerminal: false }).then(({ items }) => {
      if (cancelled) return;
      const next = [...queueRef.current];
      for (const entry of items || []) {
        if (!entry.queueId || !entry.clientTurnId || !entry.sessionId) continue;
        // A live claim belongs to an in-flight dispatch whose turn stream
        // already owns the UI; re-syncing it would resurrect a ghost copy.
        if (!initialRestore && (entry.status === "claimed"
          || (dispatchedTurnRef.current?.clientTurnId === entry.clientTurnId
            && dispatchedTurnRef.current.sessionId === entry.runtimeSessionId))) continue;
        const existingTurn = next.find((candidate) => candidate.queueId === entry.queueId
          || (candidate.id === entry.clientTurnId && candidate.queueLaneId === entry.sessionId
            && (!entry.runtimeSessionId || candidate.sessionId === entry.runtimeSessionId)));
        if (entry.questionId || entry.continuationSource === "question_answered"
          || entry.clientTurnId.startsWith("question-answer:")) continue;
        const laneId = entry.sessionId;
        const owner = resolveFollowupRestoreOwner(entry, chats || [], existingTurn?.chatId);
        let chat = owner.chat || owner.staleChat;
        if (!chat && owner.allowFallback) {
          const createdChatId = ensureActiveChat();
          chat = getChatById(createdChatId);
        }
        if (chat) chat = getChatById(chat.id);
        if (!chat) {
          if (existingTurn) next.splice(next.indexOf(existingTurn), 1);
          setError("Queued work could not be matched to one existing chat.");
          continue;
        }
        const runtimeSessionId = entry.runtimeSessionId || chat.sessionId || laneId;
        const staleBranch = !followsChatSession(chat, runtimeSessionId);
        const savedItem = chat.items.find((item): item is Extract<ConversationItem, { type: "user" }> => (
          item.type === "user" && item.clientTurnId === entry.clientTurnId
          && (!staleBranch || (item.queueEnvelope?.sessionId === runtimeSessionId && item.queueEnvelope.queueId === entry.queueId))
        ));
        const attachments = savedItem?.attachments || (entry.attachments || []) as ChatAttachment[];
        const unresolvedAttachment = attachments.some((attachment) => (
          !savedItem && attachment.payloadKind !== "vault_file"
        ));
        const pending = entry.status === "pending";
        const status: QueuedTurn["queueStatus"] = staleBranch ? "paused" : pending
          ? (unresolvedAttachment ? "paused" : "queued")
          : entry.status === "paused" ? "paused" : "delivery_unverified";
        const turn: QueuedTurn = {
          planMode: savedItem?.planMode === true || entry.planMode === true,
          id: entry.clientTurnId,
          text: savedItem?.text || entry.message || "",
          attachments,
          provider: entry.provider || "",
          providerLabel: entry.providerLabel || entry.provider || "",
          model: entry.model || "",
          chatId: chat.id,
          sessionId: runtimeSessionId,
          projectPath: entry.projectPath || entry.projectRoot || chat.projectPath,
          projectType: entry.projectType || chat.projectType,
          queuedFrom: true,
          queueId: entry.queueId,
          queueLaneId: laneId,
          queueSequence: entry.sequence,
          queueStatus: status,
          staleBranch,
        };
        const oldIndex = existingTurn ? next.indexOf(existingTurn) : -1;
        if (oldIndex >= 0) next.splice(oldIndex, 1);
        next.push(turn);
        if (staleBranch) continue;
        const queueEnvelope = {
          ...savedItem?.queueEnvelope,
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
        };
        if (savedItem && (savedItem.composerQueued !== true || savedItem.queueStatus !== status
          || savedItem.queueEnvelope?.queueId !== entry.queueId)) {
          updateChat(chat.id, (current) => !followsChatSession(current, runtimeSessionId) ? current : ({
            ...touchChat(current),
            items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
              ? { ...item, queuedFrom: true, composerQueued: true, queueStatus: status, queueEnvelope }
              : item),
          }));
        } else if (!savedItem) {
          updateChat(chat.id, (current) => !followsChatSession(current, runtimeSessionId) ? current : ({
            ...touchChat(current),
            sessionId: turn.sessionId || current.sessionId,
            items: [...current.items, {
              id: `user-${turn.id}`,
              type: "user",
              text: turn.text,
              planMode: turn.planMode === true,
              attachments,
              queuedFrom: true,
              composerQueued: true,
              queueStatus: status,
              clientTurnId: turn.id,
              queueEnvelope,
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
        if (nextQueueTurn()?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
      }
    }).catch(() => {
      if (!cancelled && nextQueueTurn()?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
    });
    return () => { cancelled = true; };
    // runtimeQueueVersion bumps on backend agentRuntimeQueue events so entries
    // enqueued after mount (e.g. externally queued followups) reach the pending
    // queue instead of silently waiting for the next app restart.
  }, [chatStorageReady, runtimeQueueVersion]);

  function isRunning() {
    return sendingRef.current;
  }

  function nextQueueTurn(): QueuedTurn | undefined {
    const blockedLanes = new Set<string>();
    for (const turn of queueRef.current) {
      const lane = queueLane(turn);
      if (turn.staleBranch) blockedLanes.add(lane);
      else if (!blockedLanes.has(lane)) return turn;
    }
    return undefined;
  }

  function dequeueTurn(turn: QueuedTurn): void {
    queueRef.current = queueRef.current.filter((candidate) => candidate !== turn);
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

  function markQueuedTurnStatus(owner: QueuedTurn, status: QueuedTurn["queueStatus"]): void {
    queueRef.current = queueRef.current.map((turn) => turn.id === owner.id && turn.chatId === owner.chatId
      && turn.sessionId === owner.sessionId ? { ...turn, queueStatus: status } : turn);
    setQueued([...queueRef.current]);
    if (owner.chatId) {
      updateChat(owner.chatId, (current) => !followsChatSession(current, owner.sessionId)
        || !current.items.some((item) => item.type === "user" && item.clientTurnId === owner.id) ? current : ({
        ...touchChat(current),
        items: current.items.map((item) => item.type === "user" && item.clientTurnId === owner.id
          ? {
            ...item,
            queueStatus: status,
            composerQueued: status === "queued" || status === "waiting_for_resources" || status === "paused" || status === "cancelled",
            queuedFrom: status !== "delivery_unverified",
          }
          : item),
      }));
    }
    void persistChatsNow?.().catch(() => undefined);
  }

  async function resumeQueuedTurns(manual = false): Promise<void> {
    if (queueResumeInFlightRef.current) {
      queueResumeRequestedRef.current = true;
      return;
    }
    if (sendingRef.current || stopRequestedRef.current || queueCancellationInFlightRef.current || queueRef.current.length === 0) return;
    queueResumeInFlightRef.current = true;
    try {
      const head = nextQueueTurn();
      if (!head || (!manual && (head.queueStatus === "paused" || head.queueStatus === "delivery_unverified"))) return;
      const owner = head.chatId ? getChatById(head.chatId) : undefined;
      if (!owner || !followsChatSession(owner, head.sessionId)) {
        head.staleBranch = true;
        markQueuedTurnStatus(head, "paused");
        if (nextQueueTurn()?.queueStatus === "queued") queueResumeRequestedRef.current = true;
        return;
      }
      if (await hasPendingParentWorkForTurn(head) || nextQueueTurn() !== head) return;
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
        if (nextQueueTurn() !== head) return;
        if (!result?.accepted || result.mode !== "followup" || !result.queueId) {
          markQueuedTurnStatus(head, "waiting_for_resources");
          return;
        }
        if (result.status === "acked") {
          if (head.queueStatus === "delivery_unverified") {
            await refreshRuntimeRuns(false);
            return;
          }
          dequeueTurn(head);
          markQueuedTurnStatus(head, "delivery_unverified");
          setQueued([...queueRef.current]);
          if (nextQueueTurn()?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
          return;
        }
        if (result.status === "cancelled") {
          dequeueTurn(head);
          markQueuedTurnStatus(head, "cancelled");
          setQueued([...queueRef.current]);
          if (nextQueueTurn()?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
          return;
        }
        if (result.status === "claimed") return;
        head.queueId = result.queueId;
        head.queueSequence = result.sequence;
      }
      dequeueTurn(head);
      markQueuedTurnStatus(head, "queued");
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

  async function hasPendingParentWorkForTurn(turn: QueuedTurn): Promise<boolean> {
    const targetSessionId = turn.sessionId || getChatById(turn.chatId || "")?.sessionId;
    if (!targetSessionId) return false;
    if (hasPendingChildDelivery?.(targetSessionId)) return true;
    try {
      const result = await fetchAgentQuestions(endpoint, {
        sessionId: targetSessionId,
        projectRoot: turn.projectPath || undefined,
        activeOnly: true,
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
      if (turn.queueStatus !== "delivery_unverified") markQueuedTurnStatus(turn, "paused");
    }
    let failed = false;
    try {
      for (const turn of pending) {
        if (!await removeQueuedTurn(turn.id, turn.chatId)) failed = true;
      }
      if (failed) setError(t("chat.queueCancellationFailed"));
      await persistChatsNow?.().catch(reportChatSaveError);
    } finally {
      queueCancellationInFlightRef.current = false;
    }
  }

  function applyFollowupReceipt(receipt: FollowupReceipt) {
    const { clientTurnId, sessionId, startedAt, turnId } = receipt;
    if (!clientTurnId || !sessionId || !startedAt || !turnId) return;
    const active = dispatchedTurnRef.current;
    const owner = active?.clientTurnId === clientTurnId && active.sessionId === sessionId ? active : undefined;
    if (owner?.turnId && owner.turnId !== turnId) return;
    if (owner) owner.turnId = turnId;
    const queued = queueRef.current.find((turn) => turn.id === clientTurnId && turn.sessionId === sessionId && turn.queueId);
    const chatId = owner?.followup ? owner.chatId : queued?.chatId;
    const chat = chatId ? getChatById(chatId) : undefined;
    if (!chat || chat.sessionId !== sessionId) return;
    const item = chat.items.find((candidate) => candidate.type === "user"
      && candidate.clientTurnId === clientTurnId && candidate.composerQueued && candidate.queueEnvelope?.queueId);
    if (!item) return;
    if (owner?.followup) owner.followup.admitted = true;
    updateChat(chat.id, (current) => current.sessionId !== sessionId ? current : ({
      ...touchChat(current),
      items: current.items.map((candidate) => candidate.type === "user" && candidate.id === item.id
        ? { ...candidate, createdAt: startedAt, queuedFrom: false, composerQueued: undefined, queueStatus: undefined, queueEnvelope: undefined }
        : candidate.type === "streaming" && candidate.clientTurnId === clientTurnId
          ? { ...candidate, createdAt: startedAt }
          : candidate),
    }));
    if (queued) {
      queueRef.current = queueRef.current.filter((turn) => turn !== queued);
      setQueued([...queueRef.current]);
    }
    setQueueTransitions((current) => current.filter((turn) => turn.id !== clientTurnId || turn.queueTransition !== "dispatch"));
    void persistChatsNow().catch(reportChatSaveError);
    if (queued && nextQueueTurn()?.queueStatus === "queued") queueMicrotask(() => void resumeQueuedTurns());
  }

  function applyRuntimeDelta(delta: AgentRuntimeDeltaEvent) {
    if (delta.phase === "preparing") applyFollowupReceipt(delta);
    const clientTurnId = String(delta.clientTurnId || "").trim();
    const phase = normalizeAgentRuntimePhase(delta.phase);
    const timelineEvent = normalizeAgentRuntimeTimelineEvent(delta.timelineEvent);
    if (!clientTurnId || (!delta.textDelta && !phase && !timelineEvent && delta.activity !== true)) {
      return;
    }
    const owner = dispatchedTurnRef.current;
    if (!owner || owner.clientTurnId !== clientTurnId || owner.sessionId !== delta.sessionId
      || (owner.turnId && owner.turnId !== delta.turnId)
      || stopRequestedRef.current
      || getChatById(owner.chatId)?.sessionId !== owner.sessionId) {
      return;
    }
    if (delta.turnId) owner.turnId = delta.turnId;
    // The backend's provider activity or an accepted model message proves the
    // connection. Local phase changes, retries and tool receipts don't.
    if ((delta.activity === true && phase === "waiting_for_model")
      || (timelineEvent?.kind === "assistant" && timelineEvent.payload?.messageId && timelineEvent.payload.summary?.trim())) {
      owner.confirmProviderConnection?.();
    }
    updateChat(owner.chatId, (chat) => {
      if (chat.sessionId !== owner.sessionId) return chat;
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
    if (visibleOwnerChat && !followsChatSession(visibleOwnerChat, turn.sessionId)) return "not_accepted";
    const targetSessionId = turn.sessionId || visibleOwnerChat?.sessionId;
    if (!turn.queueId && targetSessionId && hasPendingChildDelivery?.(targetSessionId)) {
      return enqueueFollowupTurn(turn);
    }
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
        sessionId: turn.sessionId || ownerChat?.sessionId || sessionId || `session-${turn.id}`,
        projectPath: turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
        projectType: turn.projectType || ownerChat?.projectType || ((turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath) ? "unity" : "general"),
        queueLaneId: ownerChatId,
      };
      trace("reservation_acquired", { chatId: ownerChatId, sessionId: queuedTurn.sessionId });
      trace("steer_context_resolved", { chatId: ownerChatId, sessionId: queuedTurn.sessionId });
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
      }
      await preserveLocalInput(endpoint, ownerChatId, turn.id);
      steerIntentRef.current.set(turn.id, queuedTurn);
      const active = dispatchedTurnRef.current;
      const localOwner = active?.chatId === ownerChatId && active.sessionId === queuedTurn.sessionId ? active : undefined;
      const current = currentTurnRef.current;
      const localPreparing = current?.chatId === ownerChatId && current.clientTurnId === continuedTurn?.clientTurnId;
      const target = localOwner || (!localPreparing ? continuedTurn : undefined);
      const targetClientTurnId = target
        && (turn.planMode === true) === ((localOwner ? currentTurnRef.current?.planMode : visibleOwnerChat?.planMode) === true)
        ? target.clientTurnId : undefined;
      const queueEndpoint = localOwner?.endpoint || endpoint;
      const stoppingOwner = () => stopRequestedRef.current
        && currentTurnRef.current?.chatId === ownerChatId
        && currentTurnRef.current.sessionId === queuedTurn.sessionId;
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
          if (stoppingOwner()) {
            trace("dispatch_skipped_stop", { targetClientTurnId });
            return null;
          }
          if (getChatById(ownerChatId)?.sessionId !== queuedTurn.sessionId) return null;
          trace("rpc_started", { targetClientTurnId });
          const rpcStartedAt = performance.now();
          try {
            const result = await recordAgentRunQueued(queueEndpoint, {
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
           ? await cancelAgentRunFollowup(queueEndpoint, queueResult.queueId, { sessionId: queuedTurn.queueLaneId }).catch(() => null)
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
             ? { ...item, composerQueued: Boolean(confirmed || queuedTurn.queueId), queueStatus: confirmed ? "cancelled" : queuedTurn.queueStatus,
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
       if (stoppingOwner() && queueResult?.accepted === true && queueResult.mode === "steer") {
         queueResult = await recordAgentRunQueued(queueEndpoint, {
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
         stopRequested: stoppingOwner(),
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
           reportChatSaveError(cause);
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
         queueStatus: stoppingOwner() ? "paused" : backpressured ? "waiting_for_resources" : "queued",
        };
       steerIntentRef.current.delete(turn.id);
       queueRef.current.push(durableTurn);
       queueRef.current.sort((a, b) => (a.queueSequence ?? Number.MAX_SAFE_INTEGER) - (b.queueSequence ?? Number.MAX_SAFE_INTEGER));
       updateChat(ownerChatId, (current) => ({
         ...touchChat(current),
         items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
           ? { ...item, composerQueued: true, queueStatus: durableTurn.queueStatus,
               queueEnvelope: { ...item.queueEnvelope, queueId: durableTurn.queueId, sequence: durableTurn.queueSequence } }
           : item),
       }));
       setQueued([...queueRef.current]);
       void persistChatsNow?.().catch(reportChatSaveError);
      void refreshRuntimeRuns(false).catch(() => undefined);
      // The active run may have reached its finally block while the queue
      // request was in flight. In that race, take ownership of this queued
      // turn immediately; runTurnNow re-checks sendingRef to prevent a
      // concurrent drain from starting a second runner.
       if (!backpressured && !stopRequestedRef.current && (arbitration === "start" || !sendingRef.current) && nextQueueTurn() === durableTurn) {
         dequeueTurn(durableTurn);
        setQueued([...queueRef.current]);
        queueArbitratorRef.current.runnerStarted();
         void submitTurn(durableTurn);
       } else if (!sendingRef.current && !stopRequestedRef.current && nextQueueTurn()?.queueStatus === "queued") {
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
        const queuedCandidate = nextQueueTurn();
        if (queuedCandidate?.queueStatus === "queued" && await hasPendingParentWorkForTurn(queuedCandidate)) {
          break;
        }
        next = queuedCandidate?.queueStatus === "queued" ? queuedCandidate : undefined;
        if (next) dequeueTurn(next);
        setQueued([...queueRef.current]);
      }
      if (initialTurnSucceeded) return "started";
      const inputRetained = getChatById(chatId)?.items.some((item) => item.type === "user" && item.clientTurnId === turn.id);
      return inputRetained ? "failed" : "not_accepted";
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
    if (ownerChat && !followsChatSession(ownerChat, turn.sessionId)) return "not_accepted";
    const queuedTurn: QueuedTurn = {
      ...turn,
      queuedFrom: true,
      chatId: ownerChatId,
      sessionId: turn.sessionId || ownerChat?.sessionId || sessionId || `session-${turn.id}`,
      projectPath: turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath || undefined,
      projectType: turn.projectType || ownerChat?.projectType || ((turn.projectPath || ownerChat?.projectPath || activeRuntimeProjectPath) ? "unity" : "general"),
      queueLaneId: ownerChatId,
      queueStatus: "queued",
    };
    // Show the composer row during persistence and queue admission. The
    // runnable queue still only receives the result of admission below.
    setQueueTransitions((current) => [...current, { ...queuedTurn, queueTransition: "enqueue" }]);
    try {
      return await persistQueuedFollowup(turn, queuedTurn, ownerChatId, ownerChat);
    } finally {
      setQueueTransitions((current) => current.filter((item) => item.id !== turn.id || item.queueTransition !== "enqueue"));
    }
  }

  async function persistQueuedFollowup(
    turn: QueuedTurn, queuedTurn: QueuedTurn, ownerChatId: string, ownerChat?: ChatThread,
  ): Promise<SubmitTurnResult> {
    const trace = createSteerDeliveryTrace(traceSteerDelivery, {
      chatId: ownerChatId,
      sessionId: queuedTurn.sessionId,
      clientTurnId: turn.id,
    });
    trace("submit_received", { pendingDispatches: queueDispatchPendingRef.current, requestedDeliveryMode: "queue" });
    const existing = ownerChat?.items.some((item) => item.type === "user" && item.clientTurnId === turn.id);
    if (!existing) {
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
            queueStatus: "delivery_unverified",
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
    }

    await preserveLocalInput(endpoint, ownerChatId, turn.id);
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
      reportChatSaveError(cause);
    });
    if (!sendingRef.current && !stopRequestedRef.current && nextQueueTurn() === queuedTurn) {
      void resumeQueuedTurns();
    }
    return result?.accepted === true && result.mode === "followup" && Boolean(result.queueId) ? "queued" : "not_accepted";
  }

  async function preserveLocalInput(targetEndpoint: string, chatId: string, clientTurnId: string) {
    const owner = getChatById(chatId);
    if (owner) await recordLocalChatInput(targetEndpoint, owner, clientTurnId).catch(reportChatSaveError);
  }

  async function removeQueuedTurn(turnId: string, ownerChatId?: string): Promise<boolean> {
    const turn = queueRef.current.find((candidate) => candidate.id === turnId
      && (!ownerChatId || candidate.chatId === ownerChatId));
    if (!turn) return false;
    if (!turn.queueId && turn.queueStatus === "delivery_unverified") return false;
    if (turn.queueId) {
      const cancelled = await cancelAgentRunFollowup(endpoint, turn.queueId, {
        sessionId: turn.queueLaneId || turn.chatId || "",
      }).catch(() => null);
      if (cancelled?.status !== "cancelled") return false;
    }
    queueRef.current = queueRef.current.filter((candidate) => candidate.id !== turnId || candidate.chatId !== turn.chatId
      || candidate.sessionId !== turn.sessionId || candidate.queueId !== turn.queueId);
    setQueued([...queueRef.current]);
    const chatId = turn.chatId || turn.queueLaneId;
    if (chatId) {
      updateChat(chatId, (current) => {
        const ownsInput = (item: ConversationItem): item is Extract<ConversationItem, { type: "user" }> =>
          item.type === "user" && item.clientTurnId === turnId && (turn.staleBranch
            ? item.queueEnvelope?.sessionId === turn.sessionId && item.queueEnvelope?.queueId === turn.queueId
            : followsChatSession(current, turn.sessionId));
        if (!current.items.some(ownsInput)) return current;
        return {
          ...touchChat(current),
          items: current.items.map((item) => ownsInput(item) ? { ...item, queueStatus: "cancelled" } : item),
        };
      });
      if (!turn.staleBranch) await preserveLocalInput(endpoint, chatId, turnId);
      void persistChatsNow?.().catch(reportChatSaveError);
    }
    if (turn.staleBranch && nextQueueTurn()?.queueStatus === "queued") void resumeQueuedTurns();
    return true;
  }

  async function editQueuedTurn(turnId: string, ownerChatId?: string): Promise<QueuedTurn | null> {
    const current = queueRef.current.find((candidate) => candidate.id === turnId
      && (!ownerChatId || candidate.chatId === ownerChatId));
    if (!current || !await removeQueuedTurn(turnId, ownerChatId)) return null;
    // Editing withdraws the queued send; only a new composer submission sends it again.
    return current;
  }

  async function steerQueuedTurn(turnId: string, ownerChatId?: string): Promise<SubmitTurnResult> {
    const current = queueRef.current.find((candidate) => candidate.id === turnId
      && (!ownerChatId || candidate.chatId === ownerChatId));
    if (!current || current.staleBranch) return "failed";
    if (current.queueStatus === "delivery_unverified" && !current.queueId) return "failed";
    if (current.queueId) {
      const cancelled = await cancelAgentRunFollowup(endpoint, current.queueId, {
        sessionId: current.queueLaneId || current.chatId || "",
      }).catch(() => null);
      if (cancelled?.status !== "cancelled") return "failed";
    }
    queueRef.current = queueRef.current.filter((candidate) => candidate.id !== turnId || candidate.chatId !== current.chatId
      || candidate.sessionId !== current.sessionId || candidate.queueId !== current.queueId);
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
    const presentingFollowup = !background && Boolean(turn.queueId);
    const finishPresentation = () => {
      if (presentingFollowup) setQueueTransitions((current) => current.filter(
        (item) => item.id !== turn.id || item.queueTransition !== "dispatch",
      ));
    };
    if (presentingFollowup) {
      setQueueTransitions((current) => [...current, { ...turn, chatId, queueTransition: "dispatch" }]);
    }
    const abortController = options?.abortController || new AbortController();
    let userItemId = "";
    let chatSessionId = "";
    let runtimeRequestStarted = false;
    let savingBeforeDispatch = false;
    let followup: FollowupAdmission | undefined;
    if (!background) {
      activeTurnAbortRef.current = abortController;
      const activeTurn = {
        chatId,
        clientTurnId: turn.id,
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
          if (background) throw new Error(t("agent.coreDisconnectedSend"));
          if (turn.queueId) turn.queueStatus = "waiting_for_resources";
          return false;
        }
        targetEndpoint = readyEndpoint;
      }
      // A durable queue row owns its native branch. Let the runtime compact
      // that branch rather than resetting the session during preparation.
      const pinnedQueueSession = presentingFollowup ? turn.sessionId : undefined;
      const prepared = !options?.baseItems && prepareTurnContext
        ? await prepareTurnContext({
            endpoint: targetEndpoint,
            chatId,
            turn,
            signal: abortController.signal,
            pinnedSessionId: pinnedQueueSession,
          })
        : null;
      const chat = getChatById(chatId);
      if (pinnedQueueSession && (!chat || !followsChatSession(chat, pinnedQueueSession))) {
        throw new ApiError("Queued work could not be matched to one existing chat.", 409, {
          code: "followup_admission_rejected",
        });
      }
      const baseItems = (prepared?.baseItems ?? options?.baseItems ?? chat?.items ?? []).filter(
        (item) => (item.type !== "user" || item.clientTurnId !== turn.id)
          && !(item.type === "user" && (item.composerQueued
            || (item.queuedFrom && item.queueStatus === "delivery_unverified"))),
      );
      const shouldGenerateTitle = shouldGenerateChatTitle(chat, baseItems.length, background);
      chatSessionId = (pinnedQueueSession ?? prepared?.sessionId ?? options?.sessionId ?? chat?.sessionId) || `session-${turn.id}`;
      if (presentingFollowup) followup = { admitted: false };
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
        clientTurnId: turn.id,
        createdAt: new Date(startedAt).toISOString(),
        ...(presentingFollowup ? {
          queuedFrom: true,
          composerQueued: true,
          queueStatus: "delivery_unverified",
          queueEnvelope: {
            planMode: turn.planMode === true,
            provider: turn.provider,
            providerLabel: turn.providerLabel,
            model: turn.model,
            contextLimit: turn.contextLimit,
            projectPath: turn.projectPath,
            projectType: turn.projectType,
            sessionId: chatSessionId,
            laneId: turn.queueLaneId || chatId,
            computerUseRequested: turn.computerUseRequested,
            computerUseVisualTheme: turn.computerUseVisualTheme,
            computerUseVisualAccent: turn.computerUseVisualAccent,
            queueId: turn.queueId,
            sequence: turn.queueSequence,
          },
        } : {}),
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
              storedUserItem,
              streamingItem,
            ],
          };
        });
        if (options?.baseItems) {
          // A historical edit or retry replaces the chat branch.
          savingBeforeDispatch = true;
          await persistChatsNow();
          savingBeforeDispatch = false;
        } else {
          await preserveLocalInput(targetEndpoint, chatId, turn.id);
          void persistChatsNow().catch(reportChatSaveError);
        }
      }
      const computerUseGrant = !background && turn.computerUseRequested
        ? await issueComputerUseTurnGrant(targetEndpoint, {
            sessionId: chatSessionId || undefined,
            clientTurnId: turn.id,
            projectRoot: chat?.projectPath || activeRuntimeProjectPath || undefined,
          })
        : null;
      if (abortController.signal.aborted) throw new Error("Request cancelled.");
      if (!background && getChatById(chatId)?.sessionId !== chatSessionId) {
        throw new AgentTurnDeliveryUnconfirmedError("The chat changed before the Agent turn could start.");
      }
      runtimeRequestStarted = true;
      const confirmProviderConnection = bindRuntimeProviderConnection?.(targetEndpoint, turn.provider, turn.model);
      const owner: DispatchedTurn = { chatId, clientTurnId: turn.id, sessionId: chatSessionId,
        endpoint: targetEndpoint, confirmProviderConnection, followup };
      if (!background) {
        const activeTurn = currentTurnRef.current;
        if (activeTurn?.clientTurnId === turn.id && activeTurn.sessionId !== chatSessionId) {
          const dispatchedTurn = { ...activeTurn, sessionId: chatSessionId };
          currentTurnRef.current = dispatchedTurn;
          setCurrentTurn(dispatchedTurn);
        }
        dispatchedTurnAbortRef.current = abortController;
        dispatchedTurnRef.current = owner;
      }
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
      const responseSessionId = response.sessionId || response.session_id;
      const responseTurnId = response.turnId || response.turn_id;
      if (responseSessionId !== owner.sessionId || response.clientTurnId !== owner.clientTurnId
        || (response.session_id && response.session_id !== owner.sessionId)
        || !responseTurnId || (response.turn_id && response.turn_id !== responseTurnId)
        || (owner.turnId && owner.turnId !== responseTurnId)) {
        throw new AgentTurnDeliveryUnconfirmedError("Agent turn response identity did not match the active request.");
      }
      owner.turnId = responseTurnId;
      if (!background && getChatById(chatId)?.sessionId !== owner.sessionId) return false;
      if (followup && !followup.admitted) {
        applyFollowupReceipt({
          sessionId: responseSessionId,
          clientTurnId: response.clientTurnId,
          turnId: responseTurnId,
          startedAt: response.startedAt,
        });
        if (!followup.admitted) throw new AgentTurnDeliveryUnconfirmedError("Follow-up delivery could not be confirmed.");
      }
      if (response.ok && response.plan?.planner === "llm" && !response.plan.plannerFailure) {
        confirmProviderConnection?.();
      }
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
      };
      if (background) {
        const responseItemId = turn.goalDelivery?.agentItemId || response.turnId || response.turn_id;
        updateChat(chatId, (current) => {
          if (current.sessionId && current.sessionId !== chatSessionId) return current;
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
        void persistChatsNow().catch(reportChatSaveError);
      }
      await refresh(targetEndpoint);
      await refreshRuntimeRuns(false, targetEndpoint);
      return true;

      function applyRuntimeResponseToChat(current: ChatThread): ChatThread {
        if (current.sessionId !== chatSessionId) return current;
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
              composerQueued: true,
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
              composerQueued: true,
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
            ? { ...item, queuedFrom: false, composerQueued: undefined, queueStatus: undefined, queueEnvelope: undefined }
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
            timelineEventId: durableTimeline.find((event) => event.kind === "context_compaction" && event.payload.status === "completed")?.id,
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
      const interrupted = cause instanceof AgentTurnDeliveryUnconfirmedError
        || abortController.signal.aborted || text.toLowerCase().includes("cancel");
      const queueFailure = cause instanceof ApiError && cause.detail && typeof cause.detail === "object"
        ? cause.detail as { code?: string; message?: string } : null;
      if (presentingFollowup && !followup?.admitted) {
        const rejected = queueFailure?.code === "followup_admission_rejected";
        const notReady = queueFailure?.code === "followup_queue_not_ready" || text.includes("followup_queue_not_ready");
        const status = rejected || (!runtimeRequestStarted && abortController.signal.aborted) ? "paused"
          : !runtimeRequestStarted || notReady ? "waiting_for_resources" : "delivery_unverified";
        turn.queueStatus = status;
        const queueOwner = getChatById(chatId);
        const staleBranch = !queueOwner || !followsChatSession(queueOwner, turn.sessionId);
        queueRef.current = [{ ...turn, chatId, staleBranch }, ...queueRef.current.filter((item) => item.id !== turn.id
          || item.chatId !== chatId || item.sessionId !== turn.sessionId)];
        setQueued([...queueRef.current]);
        updateChat(chatId, (current) => !followsChatSession(current, turn.sessionId) ? current : ({
          ...touchChat(current),
          items: current.items.map((item) => item.type === "user" && item.clientTurnId === turn.id
            ? { ...item, queuedFrom: true, composerQueued: true, queueStatus: status }
            : item),
        }));
        if (savingBeforeDispatch) reportChatSaveError(cause);
        else if (!abortController.signal.aborted && !notReady) setError(queueFailure?.message || text);
        void persistChatsNow().catch(reportChatSaveError);
        return false;
      }
      if (!background) {
        const chat = getChatById(chatId);
        const settlement = settleTerminalSteerDelivery(
          chat?.items || [],
          steerIntentRef.current,
          { chatId, sessionId: chatSessionId || turn.sessionId },
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
        updateChat(chatId, (current) => current.sessionId !== chatSessionId ? current : ({
          ...touchChat(current),
          items: current.items.map((item) => {
            if (item.type !== "streaming" || item.clientTurnId !== turn.id) return item;
            const placeholder = finalizeCancelledStreamingTurn(item, {
              sessionId: chatSessionId,
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
        if (isRuntimeSessionVerificationError(text)) handleRuntimeSessionFailure(text);
        return false;
      }
      const sessionFailure = isRuntimeSessionVerificationError(text);
      if (savingBeforeDispatch) reportChatSaveError(cause);
      if (options?.restoreOnFailure) {
        const snapshot = options.restoreOnFailure;
        updateChat(chatId, (current) => current.sessionId !== chatSessionId ? current : ({
          ...current,
          sessionId: snapshot.sessionId,
          title: snapshot.title || current.title,
          updatedAt: snapshot.updatedAt || current.updatedAt,
          items: stripTransientConversationItems(snapshot.items),
        }));
        if (!savingBeforeDispatch && !sessionFailure) (options.onFailure ?? setError)(text);
      } else {
        if (!savingBeforeDispatch && !sessionFailure) {
          appendToChat(chatId, { id: `error-${Date.now()}`, type: "error", text,
            ...(cause instanceof ApiError && cause.code ? { code: cause.code } : {}) });
        }
        updateChat(chatId, (current) => ({
          ...touchChat(current),
          items: stripTransientConversationItems(current.items),
        }));
      }
      if (sessionFailure) handleRuntimeSessionFailure(text);
      return false;
    } finally {
      updateChat(chatId, (current) => {
        if (current.sessionId !== chatSessionId) return current;
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
          dispatchedTurnRef.current = null;
        }
        currentTurnRef.current = null;
        setCurrentTurn(null);
      }
      finishPresentation();
    }
  }

  function stopCurrentRun(ownerChatId?: string): boolean {
    const current = currentTurnRef.current;
    if (!current || (ownerChatId && current.chatId !== ownerChatId)) return false;
    if (stopRequestedRef.current) return true;
    stopRequestedRef.current = true;
    setStopRequested(true);
    const sessionId = current.sessionId || getChatById(current.chatId)?.sessionId;
    for (const turn of queueRef.current) {
      if (turn.chatId === current.chatId && turn.sessionId === sessionId) markQueuedTurnStatus(turn, "paused");
    }
    // Keep pending turns durable and visible; only the backend terminal receipt
    // may transition the active turn to cancelled.
    // React state can lag while a send completes; the ref is the CAS owner
    // for the currently active turn and avoids cancelling a stale turn.
    const controller = activeTurnAbortRef.current;
    if (controller && dispatchedTurnAbortRef.current !== controller) {
      // No backend turn has been dispatched yet. Stop local preparation and
      // let the pre-dispatch signal check prevent admission after pending I/O.
      controller.abort();
      return true;
    }
    const owner = dispatchedTurnRef.current;
    if (owner && owner.chatId === current.chatId && owner.clientTurnId === current.clientTurnId) {
      const { chatId, clientTurnId, sessionId } = owner;
      const showStopping = (stopping: boolean) => updateChat(chatId, (chat) => chat.sessionId === sessionId
        ? markRuntimeStreamStopping(chat, clientTurnId, stopping) : chat);
      showStopping(true);
      const rejectStop = (message: string) => {
        if (dispatchedTurnRef.current !== owner) return;
        stopRequestedRef.current = false;
        setStopRequested(false);
        showStopping(false);
        setError(message);
      };
      void requestAgentRunCancel(owner.endpoint, {
        sessionId,
        clientTurnId,
        ...(owner.turnId ? { turnId: owner.turnId } : {}),
        reason: "user_stop",
      })
        .then((result) => {
          if (!result.ok) {
            rejectStop(result.status || "Agent cancellation was not accepted.");
          }
          void refreshRuntimeRuns(false, owner.endpoint).catch(() => undefined);
        }, (cause) => rejectStop(cause instanceof Error ? cause.message : String(cause)));
    }
    // A cancel request only asks the backend to stop. Keep the owned response
    // reader alive until its real terminal receipt, including in-flight tools.
    return true;
  }

  return {
    sending,
    queued: visibleQueue,
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
    applyFollowupReceipt,
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
