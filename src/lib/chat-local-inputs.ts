import { chatStorageValuesEqual } from "./chat-storage-reconcile";
import type { ChatThread, ConversationItem } from "./chat-types";
import { normalizeProjectPathKey } from "./project-path";

const STORE_NAME = "inputs";
type UserItem = Extract<ConversationItem, { type: "user" }>;
type ChatOwner = Pick<ChatThread, "id" | "sessionId" | "title" | "titleSource" | "projectPath" | "projectType" | "planMode" | "agentName" | "createdAt" | "updatedAt">;
type LocalInput = {
  key: [endpoint: string, chatId: string, sessionId: string, clientTurnId: string];
  endpoint: string;
  owner: ChatOwner;
  item: UserItem;
  attachmentPayloads?: ChatThread["attachmentPayloads"];
  newChat: boolean;
};

function inputTransaction<T>(
  mode: "readonly" | "readwrite",
  work: (store: IDBObjectStore, result: (value: T) => void) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("vrcforge-chat-local-inputs", 1);
    let blocked = false;
    request.onupgradeneeded = () => {
      request.result.createObjectStore(STORE_NAME, { keyPath: "key" }).createIndex("endpoint", "endpoint");
    };
    request.onblocked = () => {
      blocked = true;
      reject(new Error("Local input storage is blocked"));
    };
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const database = request.result;
      if (blocked) { database.close(); return; }
      database.onversionchange = () => database.close();
      try {
        const transaction = database.transaction(STORE_NAME, mode, mode === "readwrite" ? { durability: "strict" } : undefined);
        let value: T;
        transaction.oncomplete = () => { database.close(); resolve(value); };
        transaction.onabort = () => { database.close(); reject(transaction.error || new Error("Local input storage was interrupted")); };
        try {
          work(transaction.objectStore(STORE_NAME), (result) => { value = result; });
        } catch (cause) {
          transaction.abort();
          throw cause;
        }
      } catch (cause) {
        database.close();
        reject(cause);
      }
    };
  });
}

/** Only explicit submissions enter recovery. There is no model dispatch on read. */
export function recordLocalChatInput(endpoint: string, chat: ChatThread, clientTurnId: string): Promise<void> {
  const item = chat.items.find((candidate): candidate is UserItem => candidate.type === "user" && candidate.clientTurnId === clientTurnId);
  if (!item || !chat.sessionId) return Promise.resolve();
  const { id, sessionId, title, titleSource, projectPath, projectType, planMode, agentName, createdAt, updatedAt } = chat;
  const owner: ChatOwner = { id, sessionId, title, titleSource, projectPath, projectType, planMode, agentName, createdAt, updatedAt };
  const attachmentPayloads = Object.fromEntries((item.attachments || []).flatMap(({ payloadHash }) => {
    const payload = payloadHash && chat.attachmentPayloads?.[payloadHash];
    return payload ? [[payload.payloadHash, payload]] : [];
  }));
  const input: LocalInput = {
    key: [endpoint, id, sessionId, clientTurnId], endpoint, owner, item,
    newChat: chat.items.every((candidate) => candidate.id === item.id || candidate.type === "streaming"),
    attachmentPayloads: Object.keys(attachmentPayloads).length ? attachmentPayloads : undefined,
  };
  return inputTransaction("readwrite", (store) => {
    const previous = store.get(input.key);
    previous.onsuccess = () => {
      // A second observation of the same submission can carry a cancellation.
      // Preserve the initial birth marker when its conversation was never saved.
      const original = previous.result as LocalInput | undefined;
      store.put({ ...input, newChat: original?.newChat || input.newChat });
    };
  });
}

function ownsInput(chat: ChatThread, input: LocalInput, allowUnstarted = false): boolean {
  const sameSession = chat.sessionId === input.owner.sessionId;
  const unstarted = allowUnstarted && !chat.sessionId && chat.items.length === 0;
  return chat.id === input.owner.id && (sameSession || unstarted)
    && normalizeProjectPathKey(chat.projectPath) === normalizeProjectPathKey(input.owner.projectPath)
    && (chat.projectType || (chat.projectPath ? "unity" : "general")) === (input.owner.projectType || (input.owner.projectPath ? "unity" : "general"));
}

function matchesInput(item: ConversationItem, input: LocalInput): item is UserItem {
  return item.type === "user" && item.id === input.item.id && item.clientTurnId === input.item.clientTurnId
    && item.text === input.item.text && item.planMode === input.item.planMode
    && chatStorageValuesEqual(item.attachments || [], input.item.attachments || []);
}

export function acknowledgeLocalChatInputs(endpoint: string, chats: ChatThread[]): Promise<void> {
  const byId = new Map(chats.map((chat) => [chat.id, chat]));
  return deleteMatchingLocalInputs(endpoint, (input) => {
    const chat = byId.get(input.owner.id);
    return Boolean(chat && ownsInput(chat, input) && chat.items.some((item) => matchesInput(item, input)
      && (input.item.queueStatus !== "cancelled" || item.queueStatus === "cancelled"))
      && Object.entries(input.attachmentPayloads || {}).every(([key, payload]) => chatStorageValuesEqual(chat.attachmentPayloads?.[key], payload)));
  });
}

export function removeLocalChatInputs(endpoint: string, chatId: string): Promise<void> {
  return deleteMatchingLocalInputs(endpoint, (input) => input.owner.id === chatId);
}

function deleteMatchingLocalInputs(endpoint: string, matches: (input: LocalInput) => boolean): Promise<void> {
  return inputTransaction("readwrite", (store) => {
    const request = store.index("endpoint").openCursor(endpoint);
    request.onsuccess = () => {
      const cursor = request.result;
      if (!cursor) return;
      if (matches(cursor.value as LocalInput)) cursor.delete();
      cursor.continue();
    };
  });
}

export function readLocalChatInputs(endpoint: string): Promise<LocalInput[]> {
  return inputTransaction("readonly", (store, result) => {
    const request = store.index("endpoint").getAll(endpoint);
    request.onsuccess = () => result(request.result as LocalInput[]);
  });
}

export function restoreLocalChatInputs(
  chats: ChatThread[], inputs: LocalInput[], deletedChatIds: ReadonlySet<string>,
): ChatThread[] {
  if (!inputs.length) return chats;
  const byId = new Map(chats.map((chat) => [chat.id, chat]));
  const sessions = new Set(chats.map((chat) => chat.sessionId));
  const ordered = [...inputs].sort((a, b) => (a.item.createdAt || "").localeCompare(b.item.createdAt || ""));
  for (const input of ordered) {
    const { owner, item, attachmentPayloads } = input;
    if (deletedChatIds.has(owner.id)) continue;
    const current = byId.get(owner.id);
    const canStartSession = input.newChat && !sessions.has(owner.sessionId);
    if (current ? !ownsInput(current, input, canStartSession) : !canStartSession) continue;
    const chat = current || { ...owner, items: [] };
    const existing = chat.items.find((candidate) => candidate.id === item.id
      || (candidate.type === "user" && candidate.clientTurnId === item.clientTurnId));
    if (existing) {
      if (item.queueStatus === "cancelled" && existing.type === "user" && existing.id === item.id
        && existing.clientTurnId === item.clientTurnId && existing.queueStatus !== "cancelled") {
        byId.set(owner.id, { ...chat, items: chat.items.map((candidate) => candidate === existing
          ? { ...existing, queueStatus: "cancelled" }
          : candidate) });
      }
      continue;
    }
    const restored: UserItem = item.queueEnvelope && item.queueStatus !== "cancelled"
      ? { ...item, composerQueued: undefined, queueStatus: "delivery_unverified",
          queueEnvelope: { ...item.queueEnvelope, queueId: undefined, sequence: undefined } }
      : item;
    const items = [...chat.items];
    const response = items.findIndex((candidate) => candidate.type === "agent"
      && candidate.response.clientTurnId === item.clientTurnId);
    items.splice(response < 0 ? items.length : response, 0, restored);
    byId.set(owner.id, {
      ...chat,
      sessionId: owner.sessionId,
      items,
      ...(attachmentPayloads ? { attachmentPayloads: { ...attachmentPayloads, ...chat.attachmentPayloads } } : {}),
    });
    sessions.add(owner.sessionId);
  }
  const restored = [...byId.values()];
  return restored.length === chats.length && restored.every((chat, index) => chat === chats[index]) ? chats : restored;
}
