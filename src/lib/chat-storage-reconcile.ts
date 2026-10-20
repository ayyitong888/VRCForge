import type { ChatThread } from "./chat-types";
import type { ChatSourceRevision } from "./api/notes-chats";

export type ChatStorageReconcileResult =
  | { status: "merged"; chats: ChatThread[]; conflictIds: []; baselineFingerprints: Record<string, string> }
  | { status: "conflict"; chats: ChatThread[]; conflictIds: string[] };

export type ChatFingerprintProjector = (chat: ChatThread) => unknown;

export type ChatStorageVersions = {
  baseline: Readonly<Record<string, string>>;
  remote: Readonly<Record<string, string>>;
};

const identityChatProjection: ChatFingerprintProjector = (chat) => chat;

export function chatDigestsFromPayload(
  value: Record<string, string> | undefined,
  chats: ReadonlyArray<{ id: string }>,
): Record<string, string> | undefined {
  if (value === undefined) return undefined;
  if (!value || typeof value !== "object" || Array.isArray(value)
    || !chats.every((chat) => Object.prototype.hasOwnProperty.call(value, chat.id)
      && typeof value[chat.id] === "string" && /^[a-f0-9]{64}$/.test(value[chat.id]))) {
    throw new Error("Chat storage returned invalid chat version metadata.");
  }
  return Object.fromEntries(chats.map((chat) => [chat.id, value[chat.id]]));
}

export function chatMetadataShapesFromPayload(
  value: Record<string, Record<string, unknown>> | undefined,
  chats: ReadonlyArray<{ id: string }>,
): Record<string, Record<string, unknown>> | undefined {
  if (value === undefined) return undefined;
  const isShape = (shape: unknown): shape is Record<string, unknown> => (
    !!shape && typeof shape === "object" && !Array.isArray(shape)
      && Object.values(shape).every((child) => child === true || isShape(child))
  );
  if (!value || typeof value !== "object" || Array.isArray(value)
    || !chats.every((chat) => Object.prototype.hasOwnProperty.call(value, chat.id) && isShape(value[chat.id]))) {
    throw new Error("Chat storage returned invalid chat metadata shape.");
  }
  return Object.fromEntries(chats.map((chat) => [chat.id, value[chat.id]]));
}

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
  versions?: ChatStorageVersions,
): ChatStorageReconcileResult {
  const localById = new Map(local.map((chat) => [chat.id, chat]));
  const remoteById = new Map(remote.map((chat) => [chat.id, chat]));
  const allIds = new Set([...Object.keys(baseline), ...localById.keys(), ...remoteById.keys()]);
  const selected = new Map<string, ChatThread>();
  const conflicts: string[] = [];
  const nextBaseline = new Map<string, string>();

  for (const id of allIds) {
    const baseFingerprint = baseline[id];
    const localChat = localById.get(id);
    const remoteChat = remoteById.get(id);
    const localFingerprint = localChat ? stableFingerprint(project(localChat)) : undefined;
    const remoteFingerprint = remoteChat ? stableFingerprint(project(remoteChat)) : undefined;
    const localChanged = localFingerprint !== baseFingerprint;
    // IPC display redaction is not a storage edit. Compare the server-owned
    // version with its acknowledged version, independently of local content.
    const remoteChanged = versions
      ? versions.remote[id] !== versions.baseline[id]
      : remoteFingerprint !== baseFingerprint;

    const nextFingerprint = remoteChanged ? remoteFingerprint : baseFingerprint;
    if (nextFingerprint !== undefined) nextBaseline.set(id, nextFingerprint);

    if (localChanged && remoteChanged && (versions || localFingerprint !== remoteFingerprint)) {
      conflicts.push(id);
      continue;
    }
    const winner = localChanged || !remoteChanged ? localChat : remoteChat;
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
  return { status: "merged", chats, conflictIds: [], baselineFingerprints: Object.fromEntries(nextBaseline) };
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
  versions?: ChatStorageVersions,
): { chats: ChatThread[]; conflictIds: string[] } {
  const conflict = reconcileChatStorage(
    baseline,
    local,
    remote,
    project,
    restrictVersionsToChats(versions, baseline, local, remote),
  );
  if (conflict.status === "merged") {
    return { chats: conflict.chats, conflictIds: [] };
  }
  const conflictSet = new Set(conflict.conflictIds);
  const filteredBaseline = Object.fromEntries(
    Object.entries(baseline).filter(([id]) => !conflictSet.has(id)),
  );
  const filteredLocal = local.filter((chat) => !conflictSet.has(chat.id));
  const filteredRemote = remote.filter((chat) => !conflictSet.has(chat.id));
  const nonConflicting = reconcileChatStorage(
    filteredBaseline,
    filteredLocal,
    filteredRemote,
    project,
    restrictVersionsToChats(versions, filteredBaseline, filteredLocal, filteredRemote),
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

function restrictVersionsToChats(
  versions: ChatStorageVersions | undefined,
  baseline: Readonly<Record<string, string>>,
  local: ReadonlyArray<{ id: string }>,
  remote: ReadonlyArray<{ id: string }>,
): ChatStorageVersions | undefined {
  if (!versions) return undefined;
  const ids = new Set([
    ...Object.keys(baseline),
    ...local.map((chat) => chat.id),
    ...remote.map((chat) => chat.id),
  ]);
  return {
    baseline: Object.fromEntries(
      Object.entries(versions.baseline).filter(([id]) => ids.has(id)),
    ),
    remote: Object.fromEntries(
      Object.entries(versions.remote).filter(([id]) => ids.has(id)),
    ),
  };
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
