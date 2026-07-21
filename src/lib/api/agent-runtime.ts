import { invoke } from "@tauri-apps/api/core";
import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentApproval, AgentApprovalExecution, AgentDesktopAction, AgentGoal, AgentGoalBackgroundAcknowledgement, AgentGoalBackgroundState, AgentGoalDelivery, AgentMemory, AgentMessageAttachment, AgentProgress, AgentQuestion, AgentRuntimeResponse, AgentRuntimeRun, AgentRuntimeRunLedger, DesktopBridgeStatus, DesktopRuntimeSnapshot } from "./types";

export type ChatHistoryEntry = {
  role: "user" | "agent";
  text: string;
};

export type AgentHistoryContextBudget = {
  targetTokens?: number;
  realContextLimit?: number;
};

export type AgentHistoryCompactionPhase =
  | "standalone"
  | "pre_turn"
  | "mid_turn"
  | (string & {});

export type CompactAgentHistoryOptions = {
  history: ChatHistoryEntry[];
  signal?: AbortSignal;
  trigger?: "manual" | "auto";
  phase?: AgentHistoryCompactionPhase;
  sourceDigest?: string;
  language?: string;
  provider?: string;
  model?: string;
  targetTokens?: number;
  realContextLimit?: number;
  contextBudget?: AgentHistoryContextBudget;
};

export type AgentHistoryCompactionRedaction = {
  paths?: number;
  secrets?: number;
  avatarBlueprintIds?: number;
  total?: number;
};

export type AgentHistoryCompactionDetails = {
  schema?: string;
  summary: string;
  entryCount?: number;
  retainedEntryCount?: number;
  sourceDigest?: string;
  summaryDigest?: string;
  clientDigestMatched?: boolean | null;
  fidelity?: "full" | "fitted" | "fallback";
  redaction?: AgentHistoryCompactionRedaction;
  redactions?: AgentHistoryCompactionRedaction;
  trigger?: "manual" | "auto";
  phase?: AgentHistoryCompactionPhase;
  language?: string;
  targetTokens?: number;
  realContextLimit?: number | null;
  estimatedInputTokens?: number;
  attempts?: number;
  providerAttempts?: number;
  failureClass?: string;
  fallbackReason?: string;
};

export type CompactAgentHistoryResponse = AgentHistoryCompactionDetails & {
  ok: boolean;
  provider?: string;
  model?: string;
};

export async function sendAgentMessage(
  endpoint: string,
  message: string,
  sessionId?: string,
  history?: ChatHistoryEntry[],
  agentName?: string,
  options: { signal?: AbortSignal; attachments?: AgentMessageAttachment[]; projectPath?: string; provider?: string; providerLabel?: string; model?: string; contextLimit?: number; clientTurnId?: string; goalDeliveryId?: string; computerUseRequested?: boolean; computerUseGrantId?: string; computerUseVisualTheme?: "light" | "dark"; computerUseVisualAccent?: string } = {},
): Promise<AgentRuntimeResponse> {
  const request = {
    agentName: agentName || "desktop-agent",
    sessionId: sessionId || undefined,
    clientTurnId: options.clientTurnId || undefined,
    goalDeliveryId: options.goalDeliveryId || undefined,
    message,
    history: history ?? [],
    attachments: options.attachments ?? [],
    projectPath: options.projectPath || undefined,
    provider: options.provider || undefined,
    providerLabel: options.providerLabel || undefined,
    model: options.model || undefined,
    contextLimit: options.contextLimit && options.contextLimit > 0 ? Math.floor(options.contextLimit) : undefined,
    computerUseRequested: Boolean(options.computerUseRequested),
    computerUseGrantId: options.computerUseGrantId,
    computerUseVisualTheme: options.computerUseVisualTheme,
    computerUseVisualAccent: options.computerUseVisualAccent,
  };
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentRuntimeResponse>("send_agent_message", { request }, options.signal);
  }
  return requestJson(`${endpoint}/api/app/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: options.signal,
    body: JSON.stringify({
      agent_name: request.agentName,
      session_id: request.sessionId || null,
      clientTurnId: request.clientTurnId,
      goalDeliveryId: request.goalDeliveryId,
      message: request.message,
      history: request.history,
      attachments: request.attachments,
      projectPath: request.projectPath,
      provider: request.provider,
      providerLabel: request.providerLabel,
      model: request.model,
      contextLimit: request.contextLimit,
      computerUseRequested: request.computerUseRequested,
      computerUseGrantId: request.computerUseGrantId,
      computerUseVisualTheme: request.computerUseVisualTheme,
      computerUseVisualAccent: request.computerUseVisualAccent,
    }),
  });
}

export async function issueComputerUseTurnGrant(
  endpoint: string,
  payload: { sessionId?: string; clientTurnId: string; projectRoot?: string },
): Promise<{ ok: boolean; schema?: string; grantId: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("issue_computer_use_turn_grant", {
      request: { ...payload, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/computer-use/grants`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function fetchAgentRuns(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string; clientTurnId?: string } = {},
): Promise<AgentRuntimeRunLedger> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.clientTurnId) {
    query.set("clientTurnId", params.clientTurnId);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentRuntimeRunLedger>("fetch_agent_runs", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson<AgentRuntimeRunLedger>(`${endpoint}/api/app/agent/runs${suffix}`, { preferTauriIpc: true });
}

export async function requestAgentRunCancel(
  endpoint: string,
  payload: { sessionId?: string; turnId?: string; clientTurnId?: string; reason?: string },
): Promise<{ ok: boolean; status?: string; event?: AgentRuntimeRun }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_agent_run_cancel", { request: { ...payload, timeoutMs: 30000 } });
  }
  return requestJson(`${endpoint}/api/app/agent/runs/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function recordAgentRunQueued(
  endpoint: string,
  payload: {
    sessionId?: string;
    clientTurnId: string;
    message?: string;
    attachments?: AgentMessageAttachment[];
    provider?: string;
    providerLabel?: string;
    model?: string;
    projectPath?: string;
    projectRoot?: string;
  },
): Promise<{ ok: boolean; status?: string; event?: AgentRuntimeRun }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("record_agent_run_queued", { request: { ...payload, timeoutMs: 30000 } });
  }
  return requestJson(`${endpoint}/api/app/agent/runs/queue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function fetchAgentDesktopActions(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; schema?: string; actions: AgentDesktopAction[]; count: number; activeActions?: AgentDesktopAction[]; activeCount?: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_desktop_actions", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/desktop-actions${suffix}`, { preferTauriIpc: true });
}

export async function requestAgentDesktopAction(
  endpoint: string,
  payload: { action: string; prompt?: string; sessionId?: string; clientTurnId?: string; projectPath?: string; projectRoot?: string; params?: Record<string, unknown> },
): Promise<{ ok: boolean; schema?: string; status?: string; action?: string; event?: AgentDesktopAction; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_agent_desktop_action", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/desktop-actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 60000,
  });
}

export async function cancelAgentDesktopAction(
  endpoint: string,
  actionId: string,
  reason = "User requested cancellation.",
): Promise<{ ok: boolean; schema?: string; status?: string; action?: AgentDesktopAction; idempotent?: boolean }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("cancel_agent_desktop_action", {
      request: { id: actionId, body: { reason }, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/desktop-actions/${encodeURIComponent(actionId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
    timeoutMs: 30000,
  });
}

export async function fetchDesktopBridgeStatus(endpoint: string): Promise<DesktopBridgeStatus> {
  return requestJson<DesktopBridgeStatus>(`${endpoint}/api/app/agent/desktop-bridge`, { preferTauriIpc: true, timeoutMs: 15000 });
}

export async function fetchAgentGoals(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; schema?: string; goals: AgentGoal[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_goals", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals${suffix}`, { preferTauriIpc: true });
}

export async function createAgentGoal(
  endpoint: string,
  payload: {
    title?: string;
    goal?: string;
    summary?: string;
    wakeAt?: string;
    wakeEveryMinutes?: number;
    sessionId?: string;
    chatId?: string;
    projectPath?: string;
    projectRoot?: string;
  },
): Promise<{ ok: boolean; goal: AgentGoal }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_agent_goal", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateAgentGoal(
  endpoint: string,
  goalId: string,
  payload: { status: string; summary?: string; note?: string; wakeAt?: string; wakeEveryMinutes?: number; sessionId?: string; chatId?: string; projectRoot?: string },
): Promise<{ ok: boolean; goal: AgentGoal }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("update_agent_goal", {
      request: { id: goalId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals/${encodeURIComponent(goalId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchDueAgentGoals(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; schema?: string; now?: string; goals: AgentGoal[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_due_agent_goals", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals/due${suffix}`, { preferTauriIpc: true });
}

export async function wakeAgentGoal(
  endpoint: string,
  goalId: string,
  payload: { sessionId?: string; chatId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; goal: AgentGoal; delivery: AgentGoalDelivery; resumePrompt?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("wake_agent_goal", {
      request: { id: goalId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals/${encodeURIComponent(goalId)}/wake`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function bindAgentGoalOwner(
  endpoint: string,
  goalId: string,
  payload: { chatId: string; sessionId?: string; projectRoot?: string },
): Promise<{ ok: boolean; goal: AgentGoal }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("bind_agent_goal_owner", {
      request: { id: goalId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals/${encodeURIComponent(goalId)}/bind-owner`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchRecoverableAgentGoalDeliveries(
  endpoint: string,
  params: { limit?: number; chatId?: string } = {},
): Promise<{ ok: boolean; schema?: string; deliveries: AgentGoalDelivery[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.chatId) {
    query.set("chatId", params.chatId);
  }
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_recoverable_agent_goal_deliveries", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson(`${endpoint}/api/app/agent/goals/deliveries/recoverable${suffix}`, {
    timeoutMs: 30000,
  });
}

export async function materializeAgentGoalDelivery(
  endpoint: string,
  deliveryId: string,
  payload: { chatId: string; expectedRevision?: number },
): Promise<{ ok: boolean; schema?: string; delivery: AgentGoalDelivery }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("materialize_agent_goal_delivery", {
      request: { id: deliveryId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(
    `${endpoint}/api/app/agent/goals/deliveries/${encodeURIComponent(deliveryId)}/materialized`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function deferAgentGoalDelivery(
  endpoint: string,
  deliveryId: string,
  payload: { expectedRevision?: number } = {},
): Promise<{ ok: boolean; schema?: string; delivery: AgentGoalDelivery }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("defer_agent_goal_delivery", {
      request: { id: deliveryId, body: payload, timeoutMs: 30000 },
    });
  }
  return requestJson(
    `${endpoint}/api/app/agent/goals/deliveries/${encodeURIComponent(deliveryId)}/defer`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs: 30000,
    },
  );
}

export async function fetchAgentGoalBackgroundState(
  endpoint: string,
  chatId = "",
): Promise<AgentGoalBackgroundState> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_goal_background_state", {
      request: { chatId, timeoutMs: 30000 },
    });
  }
  const query = chatId ? `?chatId=${encodeURIComponent(chatId)}` : "";
  return requestJson(`${endpoint}/api/app/agent/goals/background${query}`, { timeoutMs: 30000 });
}

export async function acknowledgeAgentGoalBackgroundState(
  endpoint: string,
  payload: {
    chatId: string;
    kind: "recap" | "toast" | "provider";
    deliveries: AgentGoalBackgroundAcknowledgement[];
  },
): Promise<AgentGoalBackgroundState> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("acknowledge_agent_goal_background_state", {
      request: { body: payload, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/goals/background/ack`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 30000,
  });
}

export async function fetchAgentProgress(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; schema?: string; items: AgentProgress[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_progress", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/progress${suffix}`, { preferTauriIpc: true });
}

export async function replaceAgentProgress(
  endpoint: string,
  payload: { items?: Array<Partial<AgentProgress> & { step?: string; content?: string }>; plan?: Array<Partial<AgentProgress> & { step?: string; content?: string }>; sessionId?: string; projectPath?: string; projectRoot?: string },
): Promise<{ ok: boolean; schema?: string; items: AgentProgress[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("replace_agent_progress", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/progress/replace`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function createAgentProgress(
  endpoint: string,
  payload: Partial<AgentProgress> & { step?: string; content?: string; sessionId?: string; projectPath?: string; projectRoot?: string },
): Promise<{ ok: boolean; progress: AgentProgress }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_agent_progress", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function updateAgentProgress(
  endpoint: string,
  progressId: string,
  payload: Partial<AgentProgress> & { description?: string; sessionId?: string; projectRoot?: string },
): Promise<{ ok: boolean; progress: AgentProgress }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("update_agent_progress", {
      request: { id: progressId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/progress/${encodeURIComponent(progressId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteAgentProgress(
  endpoint: string,
  progressId: string,
  payload: { sessionId?: string; projectRoot?: string } = {},
): Promise<{ ok: boolean; progress: AgentProgress }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("delete_agent_progress", {
      request: { id: progressId, body: payload, timeoutMs: 60000 },
    });
  }
  const query = new URLSearchParams();
  if (payload.sessionId) {
    query.set("sessionId", payload.sessionId);
  }
  if (payload.projectRoot) {
    query.set("projectRoot", payload.projectRoot);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson(`${endpoint}/api/app/agent/progress/${encodeURIComponent(progressId)}${suffix}`, {
    method: "DELETE",
  });
}

export async function fetchAgentQuestions(
  endpoint: string,
  params: { limit?: number; sessionId?: string; projectRoot?: string; includeAnswered?: boolean } = {},
): Promise<{ ok: boolean; schema?: string; questions: AgentQuestion[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.includeAnswered) {
    query.set("includeAnswered", "true");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_questions", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/questions${suffix}`, { preferTauriIpc: true });
}

export async function createAgentQuestion(
  endpoint: string,
  payload: { header?: string; question?: string; prompt?: string; options?: unknown[]; choices?: unknown[]; owner?: string; sessionId?: string; projectPath?: string; projectRoot?: string },
): Promise<{ ok: boolean; question: AgentQuestion }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_agent_question", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function answerAgentQuestion(
  endpoint: string,
  questionId: string,
  payload: { answer?: string; value?: string; optionId?: string; selectedOptionId?: string; sessionId?: string; projectRoot?: string },
): Promise<{ ok: boolean; question: AgentQuestion }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("answer_agent_question", {
      request: { id: questionId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/questions/${encodeURIComponent(questionId)}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function fetchAgentMemory(
  endpoint: string,
  params: { limit?: number; projectRoot?: string; scope?: string } = {},
): Promise<{ ok: boolean; schema?: string; memories: AgentMemory[]; count: number }> {
  const query = new URLSearchParams();
  if (params.limit) {
    query.set("limit", String(params.limit));
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.scope) {
    query.set("scope", params.scope);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_memory", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory${suffix}`, { preferTauriIpc: true });
}

export async function createAgentMemory(
  endpoint: string,
  payload: { text?: string; content?: string; scope?: string; kind?: string; source?: string; projectPath?: string; projectRoot?: string },
): Promise<{ ok: boolean; memory: AgentMemory }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_agent_memory", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function deleteAgentMemory(
  endpoint: string,
  memoryId: string,
  payload: { reason?: string } = {},
): Promise<{ ok: boolean; memory: AgentMemory }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("delete_agent_memory", {
      request: { id: memoryId, body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function clearAgentMemory(
  endpoint: string,
  payload: { scope: "user" | "project"; reason?: string; projectRoot?: string },
): Promise<{ ok: boolean; cleared: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("clear_agent_memory", {
      request: { body: payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/memory/clear`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function compactAgentHistory(
  endpoint: string,
  history: ChatHistoryEntry[],
  signal?: AbortSignal,
): Promise<CompactAgentHistoryResponse>;
export function compactAgentHistory(
  endpoint: string,
  options: CompactAgentHistoryOptions,
): Promise<CompactAgentHistoryResponse>;
export async function compactAgentHistory(
  endpoint: string,
  historyOrOptions: ChatHistoryEntry[] | CompactAgentHistoryOptions,
  signal?: AbortSignal,
): Promise<CompactAgentHistoryResponse> {
  const options: CompactAgentHistoryOptions = Array.isArray(historyOrOptions)
    ? { history: historyOrOptions, signal }
    : historyOrOptions;
  const { signal: requestSignal, contextBudget, ...requestBody } = options;
  const body = {
    ...requestBody,
    targetTokens: requestBody.targetTokens ?? contextBudget?.targetTokens,
    realContextLimit: requestBody.realContextLimit ?? contextBudget?.realContextLimit,
  };
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<CompactAgentHistoryResponse>("compact_agent_history", {
      request: { body, timeoutMs: 120000 },
    }, requestSignal);
  }
  return requestJson<CompactAgentHistoryResponse>(`${endpoint}/api/app/agent/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: requestSignal,
    timeoutMs: 120000,
  });
}

export async function approveAgentApproval(
  endpoint: string,
  approvalId: string,
  scope: { expectedProjectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approval?: AgentApproval; execution?: AgentApprovalExecution }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("approve_agent_approval", {
      request: { approvalId, ...scope, timeoutMs: 180000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
    timeoutMs: 180000,
  });
}

export async function fetchDesktopRuntimeSnapshot(
  endpoint: string,
  params: { sessionId?: string; projectRoot?: string; includePatch?: boolean; globalOnly?: boolean } = {},
): Promise<DesktopRuntimeSnapshot> {
  if (hasTauriInternals()) {
    return invoke<DesktopRuntimeSnapshot>("desktop_runtime_snapshot", {
      request: {
        sessionId: params.sessionId,
        projectRoot: params.projectRoot,
        includePatch: params.includePatch,
        globalOnly: params.globalOnly,
        timeoutMs: 30000,
      },
    });
  }
  const query = new URLSearchParams();
  if (params.sessionId) {
    query.set("sessionId", params.sessionId);
  }
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.includePatch) {
    query.set("includePatch", "true");
  }
  if (params.globalOnly) {
    query.set("globalOnly", "true");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return requestJson<DesktopRuntimeSnapshot>(`${endpoint}/api/app/runtime/snapshot${suffix}`);
}

export async function fetchAgentApprovals(
  endpoint: string,
  params: { projectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approvals: AgentApproval[]; count: number }> {
  const query = new URLSearchParams();
  if (params.projectRoot) {
    query.set("projectRoot", params.projectRoot);
  }
  if (params.globalOnly) {
    query.set("globalOnly", "1");
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_agent_approvals", {
      request: { ...params, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals${suffix}`, { preferTauriIpc: true });
}

export async function rejectAgentApproval(
  endpoint: string,
  approvalId: string,
  scope: { expectedProjectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approval?: AgentApproval; message?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("reject_agent_approval", {
      request: { approvalId, ...scope, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/reject`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(scope),
    timeoutMs: 60000,
  });
}

export async function requestApprovalRevision(
  endpoint: string,
  approvalId: string,
  payload: { reason?: string; note?: string; expectedProjectRoot?: string; globalOnly?: boolean } = {},
): Promise<{ ok: boolean; approval?: AgentApproval; message?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_approval_revision", {
      request: { approvalId, ...payload, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/approvals/${encodeURIComponent(approvalId)}/revision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    timeoutMs: 60000,
  });
}
