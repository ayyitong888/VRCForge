import type { ChatThread } from "./chat-types";
import { filterPersistableChats, isStoredChat } from "./chat-thread";
import { normalizeProjectPathKey } from "./project-path";

const DATABASE_NAME = "vrcforge-chat-transcripts";
const STORE_NAME = "transcripts";
const ACCESS_INDEX = "accessedAt";
const DATABASE_VERSION = 1;
const MAX_ENTRIES = 5;
const MAX_BYTES = 30 * 1024 * 1024;

type TranscriptCacheEntry = {
  key: string;
  endpoint: string;
  projectPath: string;
  chatId: string;
  accessedAt: number;
  bytes: number;
  chat: ChatThread;
};

export async function readChatTranscriptCache(
  endpoint: string,
  chatId: string,
  projectPath: string,
): Promise<ChatThread | null> {
  let database: IDBDatabase | undefined;
  try {
    const normalizedPath = normalizeProjectPathKey(projectPath);
    const key = cacheKey(endpoint, chatId, normalizedPath);
    database = await openDatabase();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const done = transactionDone(transaction);
    void done.catch(() => {});
    const store = transaction.objectStore(STORE_NAME);
    const entryRequest = requestValue<TranscriptCacheEntry | undefined>(store.get(key));
    const latestAccessRequest = latestAccessTime(store.index(ACCESS_INDEX));
    const [entry, latestAccess] = await Promise.all([entryRequest, latestAccessRequest]);
    if (!entry || !isMatchingEntry(entry, endpoint, chatId, normalizedPath, key)) {
      if (entry) store.delete(key);
      await done;
      return null;
    }
    store.put({ ...entry, accessedAt: Math.max(Date.now(), latestAccess + 1) });
    await done;
    return entry.chat;
  } catch {
    return null;
  } finally {
    database?.close();
  }
}

export async function writeChatTranscriptCache(endpoint: string, chat: ChatThread): Promise<void> {
  let database: IDBDatabase | undefined;
  try {
    if (chat.items.some((item) => item.type === "streaming")) return;
    const persistable = filterPersistableChats([chat])[0];
    if (!persistable) return;

    const normalizedPath = normalizeProjectPathKey(persistable.projectPath);
    const key = cacheKey(endpoint, persistable.id, normalizedPath);
    const accessedAt = Date.now();
    const serialized = JSON.stringify({ endpoint, projectPath: normalizedPath, chatId: persistable.id, chat: persistable });
    const bytes = new TextEncoder().encode(serialized).byteLength;
    if (bytes > MAX_BYTES) {
      await removeChatTranscriptCache(endpoint, persistable.id, persistable.projectPath);
      return;
    }

    database = await openDatabase();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const done = transactionDone(transaction);
    void done.catch(() => {});
    const store = transaction.objectStore(STORE_NAME);
    const entries = await requestValue<TranscriptCacheEntry[]>(store.getAll());
    const entry: TranscriptCacheEntry = {
      key,
      endpoint,
      projectPath: normalizedPath,
      chatId: persistable.id,
      accessedAt: nextAccessTime(entries, accessedAt),
      bytes,
      chat: persistable,
    };
    const retained = entries.filter((candidate) => candidate.key !== key);
    retained.push(entry);
    retained.sort((left, right) => right.accessedAt - left.accessedAt);
    let totalBytes = retained.reduce((total, candidate) => total + candidate.bytes, 0);
    while (retained.length > MAX_ENTRIES || totalBytes > MAX_BYTES) {
      const evicted = retained.pop();
      if (!evicted) break;
      totalBytes -= evicted.bytes;
      store.delete(evicted.key);
    }
    store.put(entry);
    await done;
  } catch {
    // This cache only improves display startup. Storage failures must not affect chat authority.
  } finally {
    database?.close();
  }
}

export async function removeChatTranscriptCache(
  endpoint: string,
  chatId: string,
  projectPath: string,
): Promise<void> {
  let database: IDBDatabase | undefined;
  try {
    const normalizedPath = normalizeProjectPathKey(projectPath);
    database = await openDatabase();
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const done = transactionDone(transaction);
    void done.catch(() => {});
    transaction.objectStore(STORE_NAME).delete(cacheKey(endpoint, chatId, normalizedPath));
    await done;
  } catch {
    // Cache removal is best-effort and never changes authoritative chat data.
  } finally {
    database?.close();
  }
}

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB is unavailable"));
      return;
    }
    let wasBlocked = false;
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const store = request.result.objectStoreNames.contains(STORE_NAME)
        ? request.transaction!.objectStore(STORE_NAME)
        : request.result.createObjectStore(STORE_NAME, { keyPath: "key" });
      if (!store.indexNames.contains(ACCESS_INDEX)) store.createIndex(ACCESS_INDEX, ACCESS_INDEX);
    };
    request.onblocked = () => {
      wasBlocked = true;
      reject(new Error("IndexedDB upgrade is blocked"));
    };
    request.onerror = () => reject(request.error || new Error("IndexedDB open failed"));
    request.onsuccess = () => {
      if (wasBlocked) {
        request.result.close();
        return;
      }
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
  });
}

function requestValue<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error || new Error("IndexedDB transaction aborted"));
    transaction.onerror = () => reject(transaction.error || new Error("IndexedDB transaction failed"));
  });
}

function latestAccessTime(index: IDBIndex): Promise<number> {
  return new Promise((resolve, reject) => {
    const request = index.openKeyCursor(null, "prev");
    request.onsuccess = () => {
      const key = request.result?.key;
      resolve(typeof key === "number" ? key : 0);
    };
    request.onerror = () => reject(request.error || new Error("IndexedDB access index read failed"));
  });
}

function cacheKey(endpoint: string, chatId: string, normalizedProjectPath: string): string {
  return JSON.stringify([endpoint, normalizedProjectPath, chatId]);
}

function nextAccessTime(entries: TranscriptCacheEntry[], minimum = Date.now()): number {
  return entries.reduce((latest, entry) => Math.max(latest, entry.accessedAt + 1), minimum);
}

function isMatchingEntry(
  entry: TranscriptCacheEntry,
  endpoint: string,
  chatId: string,
  projectPath: string,
  key: string,
): boolean {
  if (entry.endpoint !== endpoint || entry.chatId !== chatId || entry.projectPath !== projectPath || entry.key !== key) {
    return false;
  }
  const chat: unknown = entry.chat;
  if (!isStoredChat(chat) || chat.id !== chatId || chat.projectPath !== entry.chat.projectPath) {
    return false;
  }
  if (typeof chat.projectPath !== "string"
    || normalizeProjectPathKey(chat.projectPath) !== projectPath
    || typeof chat.sessionId !== "string"
    || typeof chat.title !== "string") {
    return false;
  }
  return chat.items.every((item) => Boolean(item)
    && typeof item === "object"
    && typeof item.id === "string"
    && typeof item.type === "string"
    && item.type !== "streaming");
}
