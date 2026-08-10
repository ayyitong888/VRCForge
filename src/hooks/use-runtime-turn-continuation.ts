import { useCallback, useEffect, useRef } from "react";
import type { AgentRuntimeResponse } from "../lib/api";
import type { ChatThread, ConversationItem } from "../lib/chat-types";

const RUNTIME_TURN_EVENT_SCHEMA = "vrcforge.runtime_turn_event.v1";
const RUNTIME_CONTINUATION_SOURCES = new Set(["shell_process_finished", "sub_agent_finished"]);
const MAX_PENDING_CONTINUATIONS = 32;
const MAX_DELIVERED_CONTINUATIONS = 512;

type RuntimeTurnEvent = {
  schema?: string;
  continuationSource?: string;
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  plan?: {
    summary?: string;
    reply?: string;
    planner?: string;
    nextStep?: string;
    taskCompletion?: Record<string, unknown>;
  };
};

type UseRuntimeTurnContinuationParams = {
  chats: ChatThread[];
  appendToChat: (chatId: string, item: ConversationItem) => void;
};

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

function responseFromRuntimeTurnEvent(event: RuntimeTurnEvent): AgentRuntimeResponse {
  const sessionId = event.sessionId!.trim();
  const turnId = event.turnId!.trim();
  return {
    ok: true,
    session_id: sessionId,
    sessionId,
    turn_id: turnId,
    turnId,
    clientTurnId: event.clientTurnId?.trim() || undefined,
    observe: {},
    plan: {
      summary: event.plan?.summary || "",
      reply: event.plan?.reply || event.plan?.summary || "",
      planner: event.plan?.planner || "runtime",
      shellNeeded: false,
      nextStep: event.plan?.nextStep || "done",
      taskCompletion: event.plan?.taskCompletion,
    },
  };
}

export function useRuntimeTurnContinuationDelivery({
  chats,
  appendToChat,
}: UseRuntimeTurnContinuationParams) {
  const chatsRef = useRef(chats);
  const appendToChatRef = useRef(appendToChat);
  const pendingRef = useRef(new Map<string, RuntimeTurnEvent>());
  const deliveredRef = useRef(new Set<string>());
  const deliveredOrderRef = useRef<string[]>([]);
  chatsRef.current = chats;
  appendToChatRef.current = appendToChat;

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
    const alreadyStored = ownerChat.items.some(
      (item) => item.type === "agent"
        && (
          (event.clientTurnId?.trim()
            && item.response.clientTurnId === event.clientTurnId.trim())
          || (item.response.turnId || item.response.turn_id) === event.turnId
        ),
    );
    deliveredRef.current.add(key);
    deliveredOrderRef.current.push(key);
    while (deliveredOrderRef.current.length > MAX_DELIVERED_CONTINUATIONS) {
      const oldest = deliveredOrderRef.current.shift();
      if (oldest) {
        deliveredRef.current.delete(oldest);
      }
    }
    pendingRef.current.delete(key);
    if (!alreadyStored) {
      appendToChatRef.current(ownerChat.id, {
        id: `task-continuation-${continuationId}`,
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
