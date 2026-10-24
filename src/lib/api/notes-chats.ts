import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentNotes } from "./types";
import type { ChatStoragePatches } from "../chat-storage-delta";
import { chatStorageOriginId } from "../chat-storage-origin";

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
  chatDigests?: Record<string, string>;
  incremental?: boolean;
  chatManifest?: { schema: "vrcforge.chat_manifest.v1"; complete: boolean; ids: string[]; scopeDigest: string };
  /** Field/container names only; never includes metadata values or items. */
  chatMetadataShapes?: Record<string, Record<string, unknown>>;
};

export type ChatTitleResponse = {
  ok: boolean;
  title: string;
  reason?: string;
};

export async function requestChatTitle(
  endpoint: string,
  message: string,
  provider?: string,
  model?: string,
  signal?: AbortSignal,
): Promise<ChatTitleResponse> {
  const body = { message, provider, model };
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ChatTitleResponse>("generate_chat_title", {
      request: { body, timeoutMs: 16000 },
    }, signal);
  }
  return requestJson<ChatTitleResponse>(`${endpoint}/api/app/chat-title`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
    timeoutMs: 16000,
  });
}

export async function fetchChats<T>(endpoint: string, projectPaths: string[] = [], knownChatDigests?: Record<string, string>): Promise<StoredChats<T>> {
  const params = new URLSearchParams();
  for (const projectPath of projectPaths) {
    if (projectPath.trim()) {
      params.append("projectPath", projectPath);
    }
  }
  if (knownChatDigests !== undefined) params.set("knownChatDigests", JSON.stringify(knownChatDigests));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<StoredChats<T>>("fetch_chats", {
      request: { projectPaths, knownChatDigests, timeoutMs: 60000 },
    });
  }
  return requestJson<StoredChats<T>>(`${endpoint}/api/app/chats${suffix}`);
}

export async function saveChats<T>(
  endpoint: string,
  chats: T[],
  sourceRevisions: ChatSourceRevision[] = [],
  chatPatches?: ChatStoragePatches,
): Promise<{
  ok: boolean;
  path: string;
  count: number;
  appCount?: number;
  projectPaths?: Array<Record<string, unknown>>;
  sourceRevisions?: ChatSourceRevision[];
  chatDigests?: Record<string, string>;
  chatMetadataShapes?: Record<string, Record<string, unknown>>;
}> {
  // Patched records are reconstructed against their authoritative server digest.
  // Sending their full bodies as well doubles the payload without contributing
  // data; retain complete bodies only for new/unpatched records.
  const wireChats = chatPatches ? chats.map((chat) => {
    const id = (chat as { id?: unknown } | null)?.id;
    return typeof id === "string" && Object.prototype.hasOwnProperty.call(chatPatches, id)
      ? { id } : chat;
  }) : chats;
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("save_chats", {
      request: {
        body: { chats: wireChats, sourceRevisions, chatOriginId: chatStorageOriginId(), ...(chatPatches ? { chatPatches } : {}) },
        timeoutMs: 60000,
      },
    });
  }
  return requestJson(`${endpoint}/api/app/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chats: wireChats, sourceRevisions, chatOriginId: chatStorageOriginId(), ...(chatPatches ? { chatPatches } : {}) }),
  });
}
