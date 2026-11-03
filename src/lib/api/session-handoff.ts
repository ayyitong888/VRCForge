import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type SessionHandoffSummary = {
  id: string;
  status: string;
  kind: "handoff" | "question" | "reply" | string;
  source_chat_id: string;
  target_chat_id: string;
  source_revision: number;
  target_revision: number;
  revision: number;
  payloadDigest?: string;
};

export type SessionHandoffSend = {
  sourceChatId: string;
  targetChatId: string;
  payload: Record<string, unknown>;
  kind?: string;
  replyTo?: string;
};

export async function sendSessionHandoff(endpoint: string, request: SessionHandoffSend): Promise<{ ok: boolean; handoff: SessionHandoffSummary }> {
  if (hasTauriInternals()) return invokeTauriWithAbort("session_handoff_action", { request: { handoffId: "send", action: "send", body: request, timeoutMs: 60000 } });
  return requestJson(`${endpoint}/api/app/session-handoff/send`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) });
}

export async function actOnSessionHandoff(endpoint: string, handoffId: string, action: "deliver" | "accept" | "dismiss" | "cancel" | "pause" | "resume") {
  if (hasTauriInternals()) return invokeTauriWithAbort("session_handoff_action", { request: { handoffId, action, timeoutMs: 60000 } });
  return requestJson<{ ok: boolean; handoff: SessionHandoffSummary }>(`${endpoint}/api/app/session-handoff/${encodeURIComponent(handoffId)}/${action}`, { method: "POST" });
}

export type SessionHandoffContext = {
  contextId: string;
  payloadDigest: string;
  payload: Record<string, unknown>;
  /** Harness-authored framing for the model prompt; empty when no payload. */
  promptPrefix: string;
  clientTurnId: string;
};

export async function consumeSessionHandoffContext(endpoint: string, chatId: string, clientTurnId: string): Promise<{ ok: boolean; context: SessionHandoffContext | null }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok: boolean; context: SessionHandoffContext | null }>("consume_session_handoff_context", {
      request: { chatId, clientTurnId, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/session-handoff/consume?chatId=${encodeURIComponent(chatId)}&clientTurnId=${encodeURIComponent(clientTurnId)}`, { method: "POST" });
}

export async function replyToSessionHandoff(endpoint: string, sourceChatId: string, targetChatId: string, replyTo: string, goal: string) {
  return sendSessionHandoff(endpoint, { sourceChatId, targetChatId, replyTo, kind: "reply", payload: { goal: goal.slice(0, 2000), completed: false, decisions: [], blockers: [], nextAction: "", question: "" } });
}
