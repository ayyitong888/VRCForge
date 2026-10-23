import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "ts",
    contents: `import * as cache from "./src/lib/chat-transcript-cache"; window.chatTranscriptCache = cache;`,
  },
  bundle: true,
  write: false,
  format: "esm",
  define: { "process.env.NODE_ENV": '"test"' },
});

const server = createServer((request, response) => {
  if (request.url === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  response.setHeader("Content-Type", "text/html; charset=utf-8");
  response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));

let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  await page.goto(origin, { waitUntil: "domcontentloaded" });

  const savedText = "complete transcript ".repeat(12_000);
  const persisted = {
    id: "persisted",
    sessionId: "session-persisted",
    title: "Persisted transcript",
    projectPath: "C:/Workspace/One",
    projectType: "general",
    items: [{ id: "user-1", type: "user", text: savedText }, { id: "live-1", type: "streaming", clientTurnId: "turn-1", text: "partial" }],
  };
  await page.evaluate(async ({ endpoint, chat }) => window.chatTranscriptCache.writeChatTranscriptCache(endpoint, chat), { endpoint: `${origin}/api`, chat: persisted });
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "persisted", "c:\\workspace\\one"), { endpoint: `${origin}/api` }), null, "running sessions bypass the transcript cache");

  persisted.items.pop();
  await page.evaluate(async ({ endpoint, chat }) => window.chatTranscriptCache.writeChatTranscriptCache(endpoint, chat), { endpoint: `${origin}/api`, chat: persisted });
  await page.reload();
  assert.equal(await page.evaluate(async ({ endpoint, expectedLength }) => {
    const restored = await window.chatTranscriptCache.readChatTranscriptCache(endpoint, "persisted", "c:\\workspace\\one");
    return restored?.items[0].text.length === expectedLength;
  }, { endpoint: `${origin}/api`, expectedLength: savedText.length }), true, "complete persisted transcript survives page reload with normalized project path");
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "persisted", "c:\\workspace\\two"), { endpoint: `${origin}/api` }), null, "project path scopes cache entries");
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "other-id", "C:/Workspace/One"), { endpoint: `${origin}/api` }), null, "chat ID scopes cache entries");
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "persisted", "C:/Workspace/One"), { endpoint: `${origin}/other-api` }), null, "endpoint scopes cache entries");
  await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.removeChatTranscriptCache(endpoint, "persisted", "C:/Workspace/One"), { endpoint: `${origin}/api` });
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "persisted", "C:/Workspace/One"), { endpoint: `${origin}/api` }), null, "removal targets the exact transcript key");

  const corruptEndpoint = `${origin}/corrupt`;
  const corruptChat = { id: "corrupt", sessionId: "session-corrupt", title: "Corrupt", projectPath: "C:/Corrupt", items: [{ id: "message", type: "user", text: "valid" }] };
  await page.evaluate(async ({ endpoint, chat }) => window.chatTranscriptCache.writeChatTranscriptCache(endpoint, chat), { endpoint: corruptEndpoint, chat: corruptChat });
  await page.evaluate(async (chatId) => new Promise((resolve, reject) => {
    const request = indexedDB.open("vrcforge-chat-transcripts", 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const database = request.result;
      const transaction = database.transaction("transcripts", "readwrite");
      const store = transaction.objectStore("transcripts");
      transaction.oncomplete = () => { database.close(); resolve(); };
      transaction.onerror = () => { database.close(); reject(transaction.error); };
      const entries = store.getAll();
      entries.onerror = () => reject(entries.error);
      entries.onsuccess = () => {
        const entry = entries.result.find((candidate) => candidate.chatId === chatId);
        entry.chat.items = "invalid";
        store.put(entry);
      };
    };
  }), "corrupt");
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "corrupt", "C:/Corrupt"), { endpoint: corruptEndpoint }), null, "malformed cached transcript shape is rejected");

  const lruEndpoint = `${origin}/lru`;
  const smallChat = (id) => ({ id, sessionId: `session-${id}`, title: id, projectPath: "C:/LRU", items: [{ id: "message", type: "user", text: id }] });
  for (let index = 0; index < 6; index += 1) {
    await page.evaluate(async ({ endpoint, chat }) => window.chatTranscriptCache.writeChatTranscriptCache(endpoint, chat), { endpoint: lruEndpoint, chat: smallChat(`lru-${index}`) });
  }
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "lru-0", "C:/LRU"), { endpoint: lruEndpoint }), null, "cache keeps at most five entries");
  await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "lru-1", "C:/LRU"), { endpoint: lruEndpoint });
  const keyedRead = await page.evaluate(async (endpoint) => {
    const originalGetAll = IDBObjectStore.prototype.getAll;
    let getAllCalls = 0;
    IDBObjectStore.prototype.getAll = function (...args) {
      getAllCalls += 1;
      throw new Error("cache reads must not scan or materialize unrelated transcripts");
    };
    try {
      const chat = await window.chatTranscriptCache.readChatTranscriptCache(endpoint, "lru-1", "C:/LRU");
      return { found: Boolean(chat), getAllCalls };
    } finally {
      IDBObjectStore.prototype.getAll = originalGetAll;
    }
  }, lruEndpoint);
  assert.deepEqual(keyedRead, { found: true, getAllCalls: 0 }, "cache reads use a keyed lookup without materializing unrelated entries");
  await page.evaluate(async ({ endpoint, chat }) => window.chatTranscriptCache.writeChatTranscriptCache(endpoint, chat), { endpoint: lruEndpoint, chat: smallChat("lru-6") });
  assert.ok(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "lru-1", "C:/LRU"), { endpoint: lruEndpoint }), "reading an entry refreshes its LRU position");
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "lru-2", "C:/LRU"), { endpoint: lruEndpoint }), null, "least-recently-used entry is evicted first");

  const sizeEndpoint = `${origin}/size`;
  const largeSize = 16 * 1024 * 1024;
  for (const id of ["large-0", "large-1"]) {
    await page.evaluate(async ({ endpoint, id, size }) => {
      const chat = { id, sessionId: id, title: id, projectPath: "C:/Size", items: [{ id: "message", type: "user", text: "x".repeat(size) }] };
      await window.chatTranscriptCache.writeChatTranscriptCache(endpoint, chat);
    }, { endpoint: sizeEndpoint, id, size: largeSize });
  }
  assert.equal(await page.evaluate(async ({ endpoint }) => window.chatTranscriptCache.readChatTranscriptCache(endpoint, "large-0", "C:/Size"), { endpoint: sizeEndpoint }), null, "aggregate byte budget evicts older large transcripts");
  assert.equal(await page.evaluate(async ({ endpoint, size }) => (await window.chatTranscriptCache.readChatTranscriptCache(endpoint, "large-1", "C:/Size"))?.items[0].text.length === size, { endpoint: sizeEndpoint, size: largeSize }), true, "large transcript remains complete after budget enforcement");

  const unavailable = await page.evaluate(async ({ endpoint }) => {
    const descriptor = Object.getOwnPropertyDescriptor(window, "indexedDB");
    Object.defineProperty(window, "indexedDB", { configurable: true, value: undefined });
    try {
      const read = await window.chatTranscriptCache.readChatTranscriptCache(endpoint, "missing", "C:/Missing");
      await window.chatTranscriptCache.writeChatTranscriptCache(endpoint, { id: "missing", sessionId: "s", title: "Missing", projectPath: "C:/Missing", items: [] });
      await window.chatTranscriptCache.removeChatTranscriptCache(endpoint, "missing", "C:/Missing");
      return read === null;
    } finally {
      Object.defineProperty(window, "indexedDB", descriptor);
    }
  }, { endpoint: `${origin}/unavailable` });
  assert.equal(unavailable, true, "cache failures degrade to null/no-op");

  console.log("PASS IndexedDB transcript persistence, scoping, LRU/size bounds, and graceful failure");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
