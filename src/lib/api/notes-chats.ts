import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentNotes } from "./types";

export async function fetchAgentNotes(endpoint: string): Promise<AgentNotes> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AgentNotes>("fetch_agent_notes", {});
  }
  return requestJson<AgentNotes>(`${endpoint}/api/app/agent-notes`);
}

export async function saveAgentNotes(endpoint: string, content: string): Promise<{ ok: boolean; path: string; bytes: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("save_agent_notes", {
      request: { body: { content }, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/agent-notes`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export type ChatSourceRevision = {
  storeId: string;
  scope: string;
  exists: boolean;
  digest: string;
  status: string;
  projectPath?: string;
};

export type ChatRecoveryMarker = {
  storeId: string;
  scope: string;
  status: string;
  reason: string;
  requiresApproval: boolean;
  invalidCount?: number;
  quarantinedCount?: number;
};

export type StoredChats<T> = {
  ok: boolean;
  path: string;
  exists: boolean;
  chats: T[];
  count: number;
  sources?: Array<ChatSourceRevision & Record<string, unknown>>;
  recoveries?: ChatRecoveryMarker[];
  writeBlocked?: boolean;
};

export async function fetchChats<T>(endpoint: string, projectPaths: string[] = []): Promise<StoredChats<T>> {
  const params = new URLSearchParams();
  for (const projectPath of projectPaths) {
    if (projectPath.trim()) {
      params.append("projectPath", projectPath);
    }
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<StoredChats<T>>("fetch_chats", {
      request: { projectPaths, timeoutMs: 60000 },
    });
  }
  return requestJson<StoredChats<T>>(`${endpoint}/api/app/chats${suffix}`);
}

export async function saveChats<T>(
  endpoint: string,
  chats: T[],
  sourceRevisions: ChatSourceRevision[] = [],
): Promise<{
  ok: boolean;
  path: string;
  count: number;
  appCount?: number;
  projectPaths?: Array<Record<string, unknown>>;
  sourceRevisions?: ChatSourceRevision[];
}> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("save_chats", {
      request: { body: { chats, sourceRevisions }, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chats, sourceRevisions }),
  });
}
