import { compare, type Operation } from "fast-json-patch";
import type { ChatThread, ConversationItem } from "./chat-types";

export type ChatStoragePatch = {
  baseDigest: string;
  metadataPatch: Operation[];
  itemOrder: string[];
  itemPatches: Record<string, Operation[]>;
  newItems: Record<string, ConversationItem>;
};

export type ChatStoragePatches = Record<string, ChatStoragePatch>;
export type ChatMetadataShape = Record<string, unknown>;

const SHA256 = /^[a-f0-9]{64}$/;

/**
 * Build raw-preserving deltas from the last accepted client projection.
 * Top-level item identity/order is carried separately so an array index patch
 * can never retarget another conversation item in the raw server record.
 */
export function buildChatStoragePatches(
  chats: ReadonlyArray<ChatThread>,
  baselineFingerprints: Readonly<Record<string, string>>,
  serverDigests: Readonly<Record<string, string>> | undefined,
  serverShapes: Readonly<Record<string, ChatMetadataShape>> | undefined,
): ChatStoragePatches | undefined {
  if (serverDigests === undefined) return undefined;
  if (serverShapes === undefined) {
    throw new Error("Chat storage returned no acknowledged metadata shape.");
  }
  const patches: ChatStoragePatches = {};
  for (const chat of chats) {
    const baseDigest = serverDigests[chat.id];
    if (baseDigest === undefined) continue;
    if (!SHA256.test(baseDigest)) {
      throw new Error("Chat storage returned an invalid acknowledged chat digest.");
    }
    const fingerprint = baselineFingerprints[chat.id];
    if (fingerprint === undefined) {
      throw new Error("Chat storage delta baseline is unavailable for an acknowledged chat.");
    }
    const baseline = parseBaseline(fingerprint);
    if (baseline.id !== chat.id) {
      throw new Error("Chat storage delta baseline does not belong to the current chat.");
    }
    const shape = serverShapes[chat.id];
    if (!shape || typeof shape !== "object" || Array.isArray(shape)) {
      throw new Error("Chat storage delta shape is unavailable for an acknowledged chat.");
    }
    patches[chat.id] = buildChatStoragePatch(baseline, chat, baseDigest, shape);
  }
  return patches;
}

function buildChatStoragePatch(
  baseline: ChatThread,
  current: ChatThread,
  baseDigest: string,
  serverShape: ChatMetadataShape,
): ChatStoragePatch {
  const baselineItems = requireItems(baseline.items);
  const currentItems = requireItems(current.items);
  const baselineById = uniqueItems(baselineItems);
  uniqueItems(currentItems);
  const baselineMetadata = projectMetadataToShape(withoutItems(baseline), serverShape);
  const currentMetadata = withoutItems(current);
  const metadataPatch = compare(baselineMetadata, currentMetadata);
  assertAllowedOperations(metadataPatch, "metadata");

  const itemPatches: Record<string, Operation[]> = {};
  for (const item of currentItems) {
    const before = baselineById.get(item.id);
    if (!before) continue;
    const operations = compare(before, item);
    assertAllowedOperations(operations, "item");
    if (operations.length > 0) itemPatches[item.id] = operations;
  }

  return {
    baseDigest,
    metadataPatch,
    itemOrder: currentItems.map((item) => item.id),
    itemPatches,
    newItems: Object.fromEntries(
      currentItems
        .filter((item) => !baselineById.has(item.id))
        .map((item) => [item.id, structuredClone(item)]),
    ),
  };
}

/** Keep only fields known to exist in the server's value-free raw shape. */
function projectMetadataToShape(value: Record<string, unknown>, shape: ChatMetadataShape): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, childShape] of Object.entries(shape)) {
    if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
    const child = value[key];
    if (child && typeof child === "object" && !Array.isArray(child)
      && childShape && typeof childShape === "object" && !Array.isArray(childShape)) {
      result[key] = projectMetadataToShape(child as Record<string, unknown>, childShape as ChatMetadataShape);
    } else {
      result[key] = structuredClone(child);
    }
  }
  return result;
}

function parseBaseline(fingerprint: string): ChatThread {
  try {
    const value: unknown = JSON.parse(fingerprint);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
    return value as ChatThread;
  } catch {
    throw new Error("Chat storage delta baseline is not valid JSON.");
  }
}

function withoutItems(chat: ChatThread): Record<string, unknown> {
  const copy = structuredClone(chat) as unknown as Record<string, unknown>;
  delete copy.items;
  return copy;
}

function requireItems(value: unknown): ConversationItem[] {
  if (!Array.isArray(value)) throw new Error("Chat storage delta requires an items array.");
  return value as ConversationItem[];
}

function uniqueItems(items: ConversationItem[]): Map<string, ConversationItem> {
  const result = new Map<string, ConversationItem>();
  for (const item of items) {
    if (!item || typeof item.id !== "string" || !item.id.trim() || result.has(item.id)) {
      throw new Error("Chat storage delta requires unique stable item IDs.");
    }
    result.set(item.id, item);
  }
  return result;
}

function assertAllowedOperations(operations: Operation[], kind: "metadata" | "item"): void {
  for (const operation of operations) {
    const path = operation.path || "";
    const first = path.split("/")[1] || "";
    if (!path || first === "id" || (kind === "metadata" && first === "items")) {
      throw new Error(`Chat storage ${kind} patch contains a forbidden path.`);
    }
    if ("from" in operation) {
      const from = operation.from || "";
      const fromFirst = from.split("/")[1] || "";
      if (!from || fromFirst === "id" || (kind === "metadata" && fromFirst === "items")) {
        throw new Error(`Chat storage ${kind} patch contains a forbidden source path.`);
      }
    }
  }
}
