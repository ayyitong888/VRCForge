import type { StoredChats } from "./api/notes-chats";
import type { ChatStorageBaseline } from "./chat-storage-reconcile";

/** Omitted bodies are reusable only with a complete manifest and the acknowledged version. */
export function expandChatRefresh(
  payload: StoredChats<unknown>,
  baseline: Readonly<ChatStorageBaseline>,
  knownDigests: Readonly<Record<string, string>> | undefined,
): unknown[] {
  if (payload.incremental !== true) return payload.chats || [];
  const manifest = payload.chatManifest;
  const fail = (): never => { throw new Error("Incomplete incremental chat snapshot; retaining local chats."); };
  if (!manifest || manifest.schema !== "vrcforge.chat_manifest.v1" || manifest.complete !== true
    || typeof manifest.scopeDigest !== "string" || !/^[a-f0-9]{64}$/.test(manifest.scopeDigest)
    || !Array.isArray(manifest.ids) || manifest.ids.some(id => typeof id !== "string" || !id)
    || new Set(manifest.ids).size !== manifest.ids.length || manifest.ids.length !== payload.count
    || !payload.chatDigests || !payload.chatMetadataShapes || !Array.isArray(payload.chats)) return fail();
  const ids = new Set(manifest.ids);
  const changed = new Map<string, unknown>();
  for (const value of payload.chats) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return fail();
    const id = (value as { id?: unknown }).id;
    if (typeof id !== "string" || !ids.has(id) || changed.has(id)) return fail();
    changed.set(id, value);
  }
  if (Object.keys(payload.chatDigests).length !== ids.size || Object.keys(payload.chatMetadataShapes).length !== ids.size) return fail();
  return manifest.ids.map(id => {
    const digest = payload.chatDigests![id];
    if (!/^[a-f0-9]{64}$/.test(digest || "") || !payload.chatMetadataShapes![id]) return fail();
    if (changed.has(id)) return changed.get(id);
    const acknowledged = baseline[id];
    if (!acknowledged || acknowledged.id !== id || knownDigests?.[id] !== digest) return fail();
    return acknowledged;
  });
}
