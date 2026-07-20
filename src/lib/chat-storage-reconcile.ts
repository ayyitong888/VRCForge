import type { ChatThread } from "./chat-types";
import type { ChatSourceRevision } from "./api/notes-chats";

export type ChatStorageReconcileResult =
  | { status: "merged"; chats: ChatThread[]; conflictIds: [] }
  | { status: "conflict"; chats: ChatThread[]; conflictIds: string[] };

export type ChatFingerprintProjector = (chat: ChatThread) => unknown;

const identityChatProjection: ChatFingerprintProjector = (chat) => chat;

export function sourceRevisionsFromPayload(
  sources: Array<ChatSourceRevision & Record<string, unknown>> | undefined,
): ChatSourceRevision[] {
  return (sources || []).map((source) => ({
    storeId: source.storeId,
    scope: source.scope,
    exists: source.exists,
    digest: source.digest,
    status: source.status,
    ...(typeof source.projectPath === "string" ? { projectPath: source.projectPath } : {}),
  }));
}

export function snapshotChatFingerprints(
  chats: ChatThread[],
  project: ChatFingerprintProjector = identityChatProjection,
): Record<string, string> {
  return Object.fromEntries(chats.map((chat) => [chat.id, stableFingerprint(project(chat))]));
}

/**
 * Three-way reconcile a fresh server snapshot with in-memory edits.
 *
 * `baseline` is the last server state observed by this window. A chat changed
 * on only one side wins; divergent changes to the same chat fail closed so a
 * refreshed source digest can never be paired with a stale whole-store write.
 */
export function reconcileChatStorage(
  baseline: Readonly<Record<string, string>>,
  local: ChatThread[],
  remote: ChatThread[],
  project: ChatFingerprintProjector = identityChatProjection,
): ChatStorageReconcileResult {
  const localById = new Map(local.map((chat) => [chat.id, chat]));
  const remoteById = new Map(remote.map((chat) => [chat.id, chat]));
  const allIds = new Set([...Object.keys(baseline), ...localById.keys(), ...remoteById.keys()]);
  const selected = new Map<string, ChatThread>();
  const conflicts: string[] = [];

  for (const id of allIds) {
    const baseFingerprint = baseline[id];
    const localChat = localById.get(id);
    const remoteChat = remoteById.get(id);
    const localFingerprint = localChat ? stableFingerprint(project(localChat)) : undefined;
    const remoteFingerprint = remoteChat ? stableFingerprint(project(remoteChat)) : undefined;
    const localChanged = localFingerprint !== baseFingerprint;
    const remoteChanged = remoteFingerprint !== baseFingerprint;

    if (localChanged && remoteChanged && localFingerprint !== remoteFingerprint) {
      conflicts.push(id);
      continue;
    }
    const winner = localChanged ? localChat : remoteChat;
    if (winner) {
      selected.set(id, winner);
    }
  }

  if (conflicts.length > 0) {
    return { status: "conflict", chats: local, conflictIds: conflicts.sort() };
  }
  const orderedIds = [...remote.map((chat) => chat.id), ...local.map((chat) => chat.id)];
  const seen = new Set<string>();
  const chats = orderedIds.flatMap((id) => {
    if (seen.has(id)) {
      return [];
    }
    seen.add(id);
    const chat = selected.get(id);
    return chat ? [chat] : [];
  });
  return { status: "merged", chats, conflictIds: [] };
}

/**
 * Resolve an explicitly confirmed conflict without discarding either payload.
 * The server variant keeps the original id, while every conflicting local
 * variant is cloned under a caller-generated id before the next CAS write.
 */
export function preserveConflictingChatCopies(
  baseline: Readonly<Record<string, string>>,
  local: ChatThread[],
  remote: ChatThread[],
  cloneLocal: (chat: ChatThread, index: number) => ChatThread,
  project: ChatFingerprintProjector = identityChatProjection,
): { chats: ChatThread[]; conflictIds: string[] } {
  const conflict = reconcileChatStorage(baseline, local, remote, project);
  if (conflict.status === "merged") {
    return { chats: conflict.chats, conflictIds: [] };
  }
  const conflictSet = new Set(conflict.conflictIds);
  const filteredBaseline = Object.fromEntries(
    Object.entries(baseline).filter(([id]) => !conflictSet.has(id)),
  );
  const nonConflicting = reconcileChatStorage(
    filteredBaseline,
    local.filter((chat) => !conflictSet.has(chat.id)),
    remote.filter((chat) => !conflictSet.has(chat.id)),
    project,
  );
  if (nonConflicting.status !== "merged") {
    throw new Error("non-conflicting chat reconciliation unexpectedly failed");
  }
  const selected = new Map(nonConflicting.chats.map((chat) => [chat.id, chat]));
  for (const chat of remote) {
    if (conflictSet.has(chat.id)) {
      selected.set(chat.id, chat);
    }
  }
  const orderedIds = [...remote.map((chat) => chat.id), ...local.map((chat) => chat.id)];
  const seen = new Set<string>();
  const merged = orderedIds.flatMap((id) => {
    if (seen.has(id)) {
      return [];
    }
    seen.add(id);
    const chat = selected.get(id);
    return chat ? [chat] : [];
  });
  const localById = new Map(local.map((chat) => [chat.id, chat]));
  const copies = conflict.conflictIds.flatMap((id, index) => {
    const chat = localById.get(id);
    return chat ? [cloneLocal(chat, index)] : [];
  });
  return { chats: [...merged, ...copies], conflictIds: conflict.conflictIds };
}

function stableFingerprint(value: unknown): string {
  return JSON.stringify(canonicalize(value));
}

function canonicalize(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(canonicalize);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .filter(([, item]) => item !== undefined)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalize(item)]),
    );
  }
  return value;
}
