import { useCallback, useEffect, useRef } from "react";
import type { AgentQuestion, AgentRuntimeResponse, AgentRuntimeRun } from "../lib/api";
import type { ChatThread, ConversationItem } from "../lib/chat-types";

const RUNTIME_TURN_EVENT_SCHEMA = "vrcforge.runtime_turn_event.v1";
const RUNTIME_CONTINUATION_SOURCES = new Set(["approval_finished", "shell_process_finished", "sub_agent_finished", "question_answered", "foreground_completed"]);
const MAX_PENDING_CONTINUATIONS = 32;
const MAX_DELIVERED_CONTINUATIONS = 512;
const PLANNER_FAILURE_RETRY_ATTEMPTS_LIMIT = 100;
const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/;

type PlannerFailure = NonNullable<AgentRuntimeResponse["plan"]["plannerFailure"]>;

function safePlannerIdentifier(value: unknown): string | undefined {
  return typeof value === "string" && SAFE_IDENTIFIER.test(value) ? value : undefined;
}

function plannerFailureRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined;
}

function sanitizePlannerFailure(value: unknown): PlannerFailure | undefined {
  const source = plannerFailureRecord(value);
  if (!source) return undefined;
  const result: PlannerFailure = {};
  for (const key of ["code", "phase", "transportPhase"] as const) {
    const identifier = safePlannerIdentifier(source[key]);
    if (identifier) result[key] = identifier;
  }
  if (typeof source.retryable === "boolean") result.retryable = source.retryable;
  if (Number.isInteger(source.retryAttempts)
    && typeof source.retryAttempts === "number"
    && source.retryAttempts >= 0
    && source.retryAttempts <= PLANNER_FAILURE_RETRY_ATTEMPTS_LIMIT) {
    result.retryAttempts = source.retryAttempts;
  }
  if (source.retryDecision === "NO_MORE_RETRY" || source.retryDecision === "CANCELLED") {
    result.retryDecision = source.retryDecision;
  }
  const providerSource = plannerFailureRecord(source.providerError);
  if (providerSource) {
    const providerError: NonNullable<PlannerFailure["providerError"]> = {};
    for (const key of ["type", "code"] as const) {
      const identifier = safePlannerIdentifier(providerSource[key]);
      if (identifier) providerError[key] = identifier;
    }
    if (Number.isInteger(providerSource.statusCode)
      && typeof providerSource.statusCode === "number"
      && providerSource.statusCode >= 100
      && providerSource.statusCode <= 599) {
      providerError.statusCode = providerSource.statusCode;
    }
    if (typeof providerSource.streamStarted === "boolean") {
      providerError.streamStarted = providerSource.streamStarted;
    }
    if (Object.keys(providerError).length > 0) result.providerError = providerError;
  }
  return Object.keys(result).length > 0 ? result : undefined;
}

export type RuntimeTurnEvent = {
  schema?: string;
  continuationSource?: string;
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  contextUsage?: AgentRuntimeResponse["contextUsage"];
  plan?: {
    summary?: string;
    reply?: string;
    planner?: string;
    nextStep?: string;
    reason?: string;
    publicMessages?: Array<{
      id: string;
      text: string;
      phase?: "commentary" | "final_answer";
    }>;
    plannerFailure?: AgentRuntimeResponse["plan"]["plannerFailure"];
    taskCompletion?: Record<string, unknown>;
  };
  timeline?: AgentRuntimeResponse["timeline"];
};

export function questionContinuationDisplay(
  question: Pick<AgentQuestion, "status" | "runtimeContinuation">,
): "pending" | "active" | "settled" {
  const continuation = String(question.runtimeContinuation?.status || "").toLowerCase();
  if (["queued", "running", "claimed", "cancelling"].includes(continuation)) return "active";
  return String(question.status || "pending").toLowerCase() === "pending" ? "pending" : "settled";
}

export function questionContinuationCancelTarget(
  question: Pick<AgentQuestion, "sessionId" | "runtimeContinuation">,
): { sessionId?: string; turnId?: string; clientTurnId?: string } | null {
  const continuation = question.runtimeContinuation;
  if (!continuation?.turnId && !continuation?.clientTurnId) return null;
  return {
    sessionId: continuation.sessionId || question.sessionId,
    turnId: continuation.turnId,
    clientTurnId: continuation.clientTurnId,
  };
}

export function questionContinuationStatus(raw: string, nextStep = ""): string {
  if (["cancelled", "canceled"].includes(raw) || ["cancelled", "stopped"].includes(nextStep)) return "cancelled";
  if (["cancel_requested", "cancelling"].includes(raw) || nextStep === "cancel_requested") return "cancelling";
  if (["failed", "denied", "interrupted"].includes(raw) || nextStep.includes("failed") || nextStep === "error") return "failed";
  if (raw === "blocked" || ["pending_approval", "pending_verification", "needs_user_action", "paused"].includes(nextStep)) return "blocked";
  if (["done", "completed"].includes(nextStep)) return "completed";
  if (["running", "claimed", "waiting_for_model", "waiting_for_tool"].includes(raw)) return "running";
  return raw === "completed" ? "delivered" : raw;
}

export function projectAgentQuestionContinuation(
  question: AgentQuestion,
  runs: AgentRuntimeRun[],
): AgentQuestion {
  const raw = String(question.runtimeContinuationStatus || question.runtimeContinuation?.status || "").toLowerCase();
  if (!raw) return question;
  const marker = `:question:${question.questionId}`;
  const run = runs.find((candidate) => candidate.sessionId === question.sessionId
    && String(candidate.clientTurnId || "").endsWith(marker));
  const runStatus = String(run?.status || "").toLowerCase();
  const nextStep = String(run?.nextStep || "").toLowerCase();
  const status = questionContinuationStatus(runStatus || raw, nextStep);
  return {
    ...question,
    runtimeContinuation: {
      ...question.runtimeContinuation,
      questionId: question.questionId,
      status,
      sessionId: question.sessionId,
      turnId: run?.turnId || question.runtimeContinuation?.turnId,
      clientTurnId: run?.clientTurnId || question.runtimeContinuation?.clientTurnId,
    },
  };
}

type UseRuntimeTurnContinuationParams = {
  chats: ChatThread[];
  appendToChat: (chatId: string, item: ConversationItem) => void;
  updateChat?: (chatId: string, updater: (chat: ChatThread) => ChatThread) => boolean;
  onContinuation?: (event: RuntimeTurnEvent) => void;
};

export function runtimeContinuationDeliveryState(
  event: RuntimeTurnEvent,
  items: ConversationItem[],
): "append" | "defer" | "stored" | "replace-failed" {
  const clientTurnId = event.clientTurnId?.trim();
  const matches = (item: ConversationItem) => item.type === "agent"
    && ((clientTurnId && item.response.clientTurnId === clientTurnId)
      || (item.response.turnId || item.response.turn_id) === event.turnId);
  if (event.continuationSource === "foreground_completed"
    && items.some((item) => item.type === "streaming" && clientTurnId && item.clientTurnId === clientTurnId)) {
    return "defer";
  }
  const stored = items.find(matches);
  if (!stored || stored.type !== "agent") return "append";
  if (event.continuationSource === "foreground_completed"
    && ["cancelled", "failed", "error"].includes(String(stored.response.status || "").toLowerCase())) {
    return "replace-failed";
  }
  return "stored";
}

function runtimeTurnEvent(value: unknown): RuntimeTurnEvent | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const event = value as RuntimeTurnEvent;
  if (
    event.schema !== RUNTIME_TURN_EVENT_SCHEMA
    || !RUNTIME_CONTINUATION_SOURCES.has(event.continuationSource || "")
    || !event.sessionId?.trim()
    || !event.turnId?.trim()
  ) {
    return null;
  }
  return event;
}

export function responseFromRuntimeTurnEvent(event: RuntimeTurnEvent): AgentRuntimeResponse {
  const sessionId = event.sessionId!.trim();
  const turnId = event.turnId!.trim();
  const plannerFailure = sanitizePlannerFailure(event.plan?.plannerFailure);
  return {
    ok: true,
    session_id: sessionId,
    sessionId,
    turn_id: turnId,
    turnId,
    clientTurnId: event.clientTurnId?.trim() || undefined,
    contextUsage: event.contextUsage,
    observe: {},
    plan: {
      summary: event.plan?.summary || "",
      reply: event.plan?.reply || event.plan?.summary || "",
      planner: event.plan?.planner || "runtime",
      shellNeeded: false,
      nextStep: event.plan?.nextStep || "done",
      reason: event.plan?.reason,
      publicMessages: event.plan?.publicMessages,
      plannerFailure,
      taskCompletion: event.plan?.taskCompletion,
    },
    timeline: event.timeline,
  };
}

export function useRuntimeTurnContinuationDelivery({
  chats,
  appendToChat,
  updateChat,
  onContinuation,
}: UseRuntimeTurnContinuationParams) {
  const chatsRef = useRef(chats);
  const appendToChatRef = useRef(appendToChat);
  const updateChatRef = useRef(updateChat);
  const pendingRef = useRef(new Map<string, RuntimeTurnEvent>());
  const deliveredRef = useRef(new Set<string>());
  const deliveredOrderRef = useRef<string[]>([]);
  chatsRef.current = chats;
  appendToChatRef.current = appendToChat;
  updateChatRef.current = updateChat;
  const onContinuationRef = useRef(onContinuation);
  onContinuationRef.current = onContinuation;

  const deliver = useCallback((value: unknown): boolean => {
    const event = runtimeTurnEvent(value);
    if (!event) {
      return false;
    }
    // Async replay may create a fresh backend turn after a crash, but the
    // continuation client-turn id is deterministic for the owned Shell or
    // sub-agent task. Use it as the visible exactly-once key when available.
    const continuationId = event.clientTurnId?.trim() || event.turnId!;
    const key = `${event.sessionId}:${continuationId}`;
    if (deliveredRef.current.has(key)) {
      return true;
    }
    onContinuationRef.current?.(event);
    const ownerChat = chatsRef.current.find((chat) => chat.sessionId === event.sessionId);
    if (!ownerChat) {
      pendingRef.current.set(key, event);
      while (pendingRef.current.size > MAX_PENDING_CONTINUATIONS) {
        const oldest = pendingRef.current.keys().next().value;
        if (typeof oldest !== "string") {
          break;
        }
        pendingRef.current.delete(oldest);
      }
      return false;
    }
    const deliveryState = runtimeContinuationDeliveryState(event, ownerChat.items);
    if (deliveryState === "defer") {
      pendingRef.current.set(key, event);
      return false;
    }
    deliveredRef.current.add(key);
    deliveredOrderRef.current.push(key);
    while (deliveredOrderRef.current.length > MAX_DELIVERED_CONTINUATIONS) {
      const oldest = deliveredOrderRef.current.shift();
      if (oldest) {
        deliveredRef.current.delete(oldest);
      }
    }
    pendingRef.current.delete(key);
    if (deliveryState === "replace-failed") {
      const replaced = updateChatRef.current?.(ownerChat.id, (chat) => ({
        ...chat,
        items: chat.items.map((item) => item.type === "agent"
          && ((event.clientTurnId?.trim() && item.response.clientTurnId === event.clientTurnId.trim())
            || (item.response.turnId || item.response.turn_id) === event.turnId)
          ? { ...item, response: responseFromRuntimeTurnEvent(event), elapsedSeconds: 0 }
          : item),
      }));
      if (replaced) return true;
    }
    if (deliveryState === "append" || deliveryState === "replace-failed") {
      appendToChatRef.current(ownerChat.id, {
        // Keep the app-owned identifier outside the bridge's secret-prefix
        // filter (the previous task- prefix contains sk-).
        id: `runtime-reply-${continuationId}`,
        type: "agent",
        response: responseFromRuntimeTurnEvent(event),
        elapsedSeconds: 0,
        createdAt: new Date().toISOString(),
      });
    }
    return true;
  }, []);

  useEffect(() => {
    for (const event of [...pendingRef.current.values()]) {
      deliver(event);
    }
  }, [chats, deliver]);

  return deliver;
}
