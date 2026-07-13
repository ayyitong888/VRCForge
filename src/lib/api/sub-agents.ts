import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type SubAgentRole = {
  id: string;
  title: string;
  description?: string;
  toolProfile?: string;
  readOnly?: boolean;
};

export type SubAgentTask = {
  schema?: string;
  id: string;
  role: string;
  displayName: string;
  task: string;
  parentSessionId?: string;
  projectPath?: string;
  toolProfile?: string;
  status: "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled" | string;
  createdAt?: string;
  startedAt?: string;
  stoppedAt?: string;
  updatedAt?: string;
  cancelRequested?: boolean;
  summary?: string;
  error?: string;
  eventCount?: number;
  mergedAt?: string;
  mergedChatId?: string;
  mergeDecision?: "adopted" | "dismissed" | string;
  result?: Record<string, unknown> | null;
  paramsSummary?: Record<string, unknown>;
  events?: Array<{ timestamp?: string; event?: string; data?: Record<string, unknown> }>;
};

export type SubAgentTaskList = {
  ok: boolean;
  schema: string;
  tasks: SubAgentTask[];
  count: number;
  roles?: SubAgentRole[];
  maxConcurrent?: number;
  runningCount?: number;
};

export async function fetchSubAgents(endpoint: string, includeEvents = false): Promise<SubAgentTaskList> {
  const suffix = includeEvents ? "?includeEvents=true" : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SubAgentTaskList>("fetch_sub_agents", {
      request: { includeEvents, timeoutMs: 30000 },
    });
  }
  return requestJson<SubAgentTaskList>(`${endpoint}/api/app/sub-agents${suffix}`);
}

export async function createSubAgent(
  endpoint: string,
  request: {
    role: string;
    task?: string;
    displayName?: string;
    parentSessionId?: string;
    projectPath?: string;
    params?: Record<string, unknown>;
  },
): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_sub_agent", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchSubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_sub_agent", {
      request: { id: taskId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}`);
}

export async function cancelSubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("cancel_sub_agent", {
      request: { id: taskId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/cancel`, { method: "POST" });
}

export async function retrySubAgent(endpoint: string, taskId: string): Promise<{ ok: boolean; task: SubAgentTask }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("retry_sub_agent", {
      request: { id: taskId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/retry`, { method: "POST" });
}

export async function mergeSubAgent(
  endpoint: string,
  taskId: string,
  request: { decision: "adopted" | "dismissed"; chatId?: string },
): Promise<{ ok: boolean; task: SubAgentTask; message?: string }> {
  const body = { decision: request.decision, chatId: request.chatId ?? "" };
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("merge_sub_agent", {
      request: { id: taskId, body, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/sub-agents/${encodeURIComponent(taskId)}/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
