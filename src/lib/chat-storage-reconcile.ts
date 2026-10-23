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
 * on only one side wins; independent append-only item additions can merge.
 * Divergent edits to existing content fail closed so a
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
      const appended = mergeChatAppends(baseFingerprint, localChat, remoteChat, project);
      if (appended) {
        selected.set(id, appended);
        continue;
      }
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

/** Merge proven appends, never guess whether existing content was edited. */
function mergeChatAppends(
  baseline: string | undefined,
  local: ChatThread | undefined,
  remote: ChatThread | undefined,
  project: ChatFingerprintProjector,
): ChatThread | null {
  if (!baseline || !local || !remote) return null;
  let base: ChatThread;
  try {
    base = JSON.parse(baseline) as ChatThread;
  } catch {
    return null;
  }
  const left = project(local) as ChatThread | null;
  const right = project(remote) as ChatThread | null;
  if (!base || !left || !right || base.id !== local.id || base.id !== remote.id
    || !Array.isArray(base.items) || !Array.isArray(left.items) || !Array.isArray(right.items)) return null;
  // Revision and update time describe mutations; neither is user content.
  const contentMetadata = (chat: ChatThread) => {
    const { items: _items, revision: _revision, updatedAt: _updatedAt, ...metadata } = chat;
    return metadata;
  };
  const baseMetadata = contentMetadata(base);
  const leftMetadata = contentMetadata(left);
  const rightMetadata = contentMetadata(right);
  const mergedMetadata: Record<string, unknown> = {};
  for (const key of new Set([...Object.keys(baseMetadata), ...Object.keys(leftMetadata), ...Object.keys(rightMetadata)])) {
    const before = baseMetadata[key as keyof typeof baseMetadata];
    const localValue = leftMetadata[key as keyof typeof leftMetadata];
    const remoteValue = rightMetadata[key as keyof typeof rightMetadata];
    const localChanged = stableFingerprint(localValue) !== stableFingerprint(before);
    const remoteChanged = stableFingerprint(remoteValue) !== stableFingerprint(before);
    if (localChanged && remoteChanged && stableFingerprint(localValue) !== stableFingerprint(remoteValue)) return null;
    const source = localChanged ? local : remote;
    if (Object.prototype.hasOwnProperty.call(source, key)) mergedMetadata[key] = source[key as keyof ChatThread];
  }
  const prefix = (chat: ChatThread) => chat.items.length >= base.items.length
    && base.items.every((item, index) => stableFingerprint(chat.items[index]) === stableFingerprint(item));
  if (!prefix(left) || !prefix(right)) return null;
  // A metadata display difference can be redaction rather than the mutation
  // behind an opaque server version. Require the proven remote append before
  // merging concurrent edits; metadata alone cannot establish that evidence.
  if (right.items.length === base.items.length) return null;
  const all = new Map<string, ChatThread["items"][number]>();
  const edges = new Map<string, Set<string>>();
  const incoming = new Map<string, number>();
  for (const chat of [remote, local]) {
    const seen = new Set<string>();
    let previous: string | undefined;
    for (const item of chat.items) {
      if (!item.id || seen.has(item.id)) return null;
      seen.add(item.id);
      const other = all.get(item.id);
      if (other && stableFingerprint(other) !== stableFingerprint(item)) return null;
      if (!other) {
        all.set(item.id, item);
        incoming.set(item.id, 0);
      }
      if (previous) {
        const next = edges.get(previous) || new Set<string>();
        if (!next.has(item.id)) {
          next.add(item.id);
          incoming.set(item.id, (incoming.get(item.id) || 0) + 1);
        }
        edges.set(previous, next);
      }
      previous = item.id;
    }
  }
  const ready = [...all.keys()].filter((id) => incoming.get(id) === 0);
  const items: ChatThread["items"] = [];
  while (ready.length) {
    const id = ready.shift()!;
    items.push(all.get(id)!);
    for (const next of edges.get(id) || []) {
      const count = incoming.get(next)! - 1;
      incoming.set(next, count);
      if (count === 0) ready.push(next);
    }
  }
  if (items.length !== all.size) return null;
  const revisions = [local.revision ?? 0, remote.revision ?? 0];
  if (!revisions.every((value) => Number.isSafeInteger(value) && value >= 0 && value < Number.MAX_SAFE_INTEGER)) return null;
  const times = [local.updatedAt, remote.updatedAt].filter((value): value is string => value !== undefined);
  if (!times.every((value) => Number.isFinite(Date.parse(value)))) return null;
  const updatedAt = times.sort((a, b) => Date.parse(b) - Date.parse(a))[0];
  return { ...mergedMetadata, items, revision: Math.max(...revisions) + 1, ...(updatedAt ? { updatedAt } : {}) } as ChatThread;
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
