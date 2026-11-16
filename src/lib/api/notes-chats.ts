import { ApiError, hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentNotes } from "./types";
import type { ChatStoragePatch, ChatStoragePatches } from "../chat-storage-delta";
import type { ChatSidebarCacheEntry } from "../chat-sidebar-cache";
import type { ChatThread } from "../chat-types";
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
  return fetchChatStorage<StoredChats<T>>(endpoint, { projectPaths, knownChatDigests });
}

export type ChatCatalogEntry = ChatSidebarCacheEntry & {
  sourceProjectPath: string;
  sourceStoreId: string;
};

export type ChatCatalog = {
  ok: boolean;
  entries: ChatCatalogEntry[];
  sources: ChatSourceRevision[];
  recoveries: ChatRecoveryMarker[];
  writeBlocked: boolean;
  pendingChatIds: string[];
};

export type ChatRecordSnapshot<T> = StoredChats<T> & {
  hasOlder: boolean;
  nextBefore: string | null;
  sourceRevision?: ChatSourceRevision | null;
};

export function fetchChatCatalog(endpoint: string, projectPaths: string[], signal?: AbortSignal): Promise<ChatCatalog> {
  return fetchChatStorage<ChatCatalog>(endpoint, { projectPaths, catalog: true }, signal);
}

export function fetchChatRecord<T = unknown>(
  endpoint: string,
  chatId: string,
  sourceProjectPath: string,
  options: { before?: string; full?: boolean; signal?: AbortSignal } = {},
): Promise<ChatRecordSnapshot<T>> {
  return fetchChatStorage<ChatRecordSnapshot<T>>(endpoint, {
    chatId, chatProjectPath: sourceProjectPath, before: options.before, full: options.full,
  }, options.signal);
}

type ChatStorageQuery = {
  projectPaths?: string[];
  knownChatDigests?: Record<string, string>;
  catalog?: boolean;
  chatId?: string;
  chatProjectPath?: string;
  before?: string;
  full?: boolean;
};

async function fetchChatStorage<T>(
  endpoint: string,
  query: ChatStorageQuery,
  signal?: AbortSignal,
): Promise<T> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<T>("fetch_chats", { request: { ...query, timeoutMs: 60000 } }, signal);
  }
  const params = new URLSearchParams();
  for (const projectPath of query.projectPaths ?? []) {
    if (projectPath.trim()) {
      params.append("projectPath", projectPath);
    }
  }
  if (query.knownChatDigests !== undefined) params.set("knownChatDigests", JSON.stringify(query.knownChatDigests));
  if (query.catalog) params.set("catalog", "1");
  if (query.chatId !== undefined) params.set("chatId", query.chatId);
  if (query.chatProjectPath !== undefined) params.set("chatProjectPath", query.chatProjectPath);
  if (query.before !== undefined) params.set("before", query.before);
  if (query.full) params.set("full", "1");
  const suffix = params.size ? `?${params.toString()}` : "";
  return requestJson<T>(`${endpoint}/api/app/chats${suffix}`, { signal });
}

export type ChatSaveReceipt = {
  ok: boolean;
  path: string;
  count: number;
  appCount?: number;
  projectPaths?: Array<Record<string, unknown>>;
  sourceRevisions?: ChatSourceRevision[];
  chatDigests?: Record<string, string>;
  chatMetadataShapes?: Record<string, Record<string, unknown>>;
};

export type ChatRecordSaveRequest = {
  chatId: string;
  projectPath: string;
  sourceRevisions: ChatSourceRevision[];
  chat?: ChatThread;
  chatPatch?: ChatStoragePatch;
  delete?: true;
};

export async function saveChatRecord(
  endpoint: string,
  record: ChatRecordSaveRequest,
  chatSaveId = globalThis.crypto.randomUUID(),
): Promise<ChatSaveReceipt> {
  const body = { ...record, chatOriginId: chatStorageOriginId(), chatSaveId };
  const submit = () => hasTauriInternals()
    ? invokeTauriWithAbort<ChatSaveReceipt>("save_chat_record", { request: { body, timeoutMs: 60000 } })
    : requestJson<ChatSaveReceipt>(`${endpoint}/api/app/chats/single`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  try {
    return await submit();
  } catch (cause) {
    if (!(cause instanceof ApiError) || cause.status !== 0) throw cause;
    return submit();
  }
}

export async function saveChats<T>(
  endpoint: string,
  chats: T[],
  sourceRevisions: ChatSourceRevision[] = [],
  chatPatches?: ChatStoragePatches,
  chatSaveId = globalThis.crypto.randomUUID(),
): Promise<ChatSaveReceipt> {
  // Patched records are reconstructed against their authoritative server digest.
  // Sending their full bodies as well doubles the payload without contributing
  // data; retain complete bodies only for new/unpatched records.
  const wireChats = chatPatches ? chats.map((chat) => {
    const id = (chat as { id?: unknown } | null)?.id;
    return typeof id === "string" && Object.prototype.hasOwnProperty.call(chatPatches, id)
      ? { id } : chat;
  }) : chats;
  const body = {
    chats: wireChats,
    sourceRevisions,
    chatOriginId: chatStorageOriginId(),
    chatSaveId,
    ...(chatPatches ? { chatPatches } : {}),
  };
  const submit = () => hasTauriInternals()
    ? invokeTauriWithAbort<ChatSaveReceipt>("save_chats", { request: { body, timeoutMs: 60000 } })
    : requestJson<ChatSaveReceipt>(`${endpoint}/api/app/chats`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  try {
    return await submit();
  } catch (cause) {
    if (!(cause instanceof ApiError) || cause.status !== 0) throw cause;
    // A transport error cannot tell us whether the backend already committed.
    return submit();
  }
}
