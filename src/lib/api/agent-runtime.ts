import { invoke } from "@tauri-apps/api/core";
import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentApproval, AgentApprovalExecution, AgentDesktopAction, AgentGoal, AgentMemory, AgentMessageAttachment, AgentRuntimeResponse, AgentRuntimeRun, AgentRuntimeRunLedger, DesktopRuntimeSnapshot } from "./types";

export type ChatHistoryEntry = {
  role: "user" | "agent";
  text: string;
};

export async function sendAgentMessage(
  endpoint: string,
  message: string,
  sessionId?: string,
  history?: ChatHistoryEntry[],
  agentName?: string,
  options: { signal?: AbortSignal; attachments?: AgentMessageAttachment[]; projectPath?: string; provider?: string; providerLabel?: string; model?: string; clientTurnId?: string } = {},
): Promise<AgentRuntimeResponse> {
  const request = {
    agentName: agentName || "desktop-agent",
    sessionId: sessionId || undefined,
    clientTurnId: options.clientTurnId || undefined,
    message,
    history: history ?? [],
    attachments: options.attachments ?? [],
    projectPath: options.projectPath || undefined,
    provider: options.provider || undefined,
    providerLabel: options.providerLabel || undefined,
    model: options.model || undefined,
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
      message: request.message,
      history: request.history,
      attachments: request.attachments,
      projectPath: request.projectPath,
      provider: request.provider,
      providerLabel: request.providerLabel,
      model: request.model,
    }),
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
): Promise<{ ok: boolean; schema?: string; actions: AgentDesktopAction[]; count: number }> {
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
  payload: { title?: string; goal?: string; summary?: string; sessionId?: string; projectPath?: string; projectRoot?: string },
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
  payload: { status: string; summary?: string; note?: string; sessionId?: string; projectRoot?: string },
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
  payload: { scope?: string; reason?: string; projectRoot?: string } = {},
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

export async function compactAgentHistory(
  endpoint: string,
  history: ChatHistoryEntry[],
): Promise<{ ok: boolean; summary: string; provider?: string; model?: string; entryCount?: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("compact_agent_history", {
      request: { body: { history }, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent/compact`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ history }),
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
