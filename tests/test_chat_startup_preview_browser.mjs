import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(),
    loader: "tsx",
    contents: `
      import React from "react";
      import { createRoot } from "react-dom/client";
      import i18n from "i18next";
      import { initReactI18next } from "react-i18next";
      import { useChatSessions } from "./src/hooks/use-chat-sessions";

      await i18n.use(initReactI18next).init({ lng: "en", resources: { en: { translation: {} } } });
      const noop = () => {};
      const paths = [];
      const initialChatState = { chats: [], activeChatId: "" };

      function Fixture() {
        const sessions = useChatSessions({
          endpoint: location.origin, activeView: "chat", runtimeConnected: true, projectPrefsReady: true,
          projectPaths: paths, customProjectPaths: paths, activeProjectPath: "", setActiveProjectPath: noop,
          setActiveView: noop, setError: noop, expandProjectGroup: noop, initialChatState,
        });
        const sidebar = sessions.chatSidebar.temporaryChats;
        const displayed = (sessions.displayChat ?? sessions.previewChat)?.items ?? [];
        const authoritative = sessions.getChatById(sessions.activeChatId)?.items ?? null;
        window.appendAndSave = async () => {
          const owner = await sessions.loadChat("a");
          if (!owner) throw new Error("Expected a loaded owner for the new message");
          sessions.appendToChat(owner.id, { id: "a-new", type: "user", text: "Appended message in A" });
          await sessions.persistChatsNow();
        };
        return <>
          <nav>{sidebar.map((chat) => <button key={chat.id} onClick={() => sessions.openChat(chat)}>{chat.title}</button>)}</nav>
          {sessions.hasOlderChatMessages && <button onClick={() => void sessions.loadOlderChatMessages()}>Load older messages</button>}
          <div id="messages">{displayed.map((item) => <p key={item.id}>{item.text}</p>)}</div>
          <pre id="state">{JSON.stringify({
            activeId: sessions.activeChatId,
            pending: sessions.activeChatPending,
            ready: sessions.chatStorageReady,
            preview: Boolean(sessions.previewChat),
            authoritative: authoritative?.map((item) => item.text) ?? null,
            loadedIds: sessions.chats.map((chat) => chat.id),
          })}</pre>
        </>;
      }
      createRoot(document.getElementById("root")).render(<Fixture />);
    `,
  },
  bundle: true,
  write: false,
  format: "esm",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const entry = (id, title) => ({
  id, title, sessionId: `session-${id}`, projectPath: "", projectType: "general", revision: 0,
  createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
});
const entries = [entry("a", "Chat A"), entry("b", "Chat B")];
const message = (id, text) => ({ id, type: "user", text });
const aOlder = { ...entries[0], items: [message("a-old", "Older message in A")] };
const aRecent = { ...entries[0], items: [message("a-recent", "Recent message in A")] };
const bRecord = { ...entries[1], items: [message("b-recent", "Recent message in B")] };
const aFull = { ...aRecent, items: [...aOlder.items, ...aRecent.items] };
const staleRecord = Promise.withResolvers();
const heldCatalog = Promise.withResolvers();
const movedCatalog = Promise.withResolvers();
const retryCatalog = Promise.withResolvers();
const removedCatalog = Promise.withResolvers();
const movedSourcePath = "D:/MovedProject";
let retryReads = 0;
const source = { storeId: "chat.app", scope: "app", exists: true, digest: "1".repeat(64), status: "ok" };
const savedSource = { ...source, digest: "2".repeat(64) };
const aDigest = "a".repeat(64);
const aShape = Object.fromEntries(Object.keys(entries[0]).map((key) => [key, true]));
const firstCursor = `${source.digest}:1`;
const requests = [];
const writes = [];

function json(response, payload, status = 200) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(payload));
}

function chatPage(chat, nextBefore = null) {
  return { ok: true, chats: [chat], sourceRevision: source, hasOlder: nextBefore !== null, nextBefore };
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (url.pathname === "/api/app/chats/single" && request.method === "POST") {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    writes.push(body);
    if (body.chatId !== "a") {
      json(response, { detail: "Unexpected write target" }, 422);
      return;
    }
    json(response, {
      ok: true, path: "", count: 1, sourceRevisions: [savedSource],
      chatDigests: { a: "c".repeat(64) }, chatMetadataShapes: { a: aShape },
    });
    return;
  }
  if (url.pathname === "/api/app/chats") {
    const query = Object.fromEntries(url.searchParams);
    const referer = request.headers.referer ?? "";
    const retry = referer.includes("retryCatalog=1");
    const removed = referer.includes("removedCatalog=1");
    const moved = retry || removed || referer.includes("movedCatalog=1");
    requests.push({ method: request.method, ...query });
    if (query.catalog === "1" && request.method === "GET") {
      if (referer.includes("holdCatalog=1")) await heldCatalog.promise;
      if (retry) await retryCatalog.promise;
      else if (removed) await removedCatalog.promise;
      else if (moved) await movedCatalog.promise;
      json(response, {
        ok: true,
        entries: entries.filter((row) => !removed || row.id !== "a")
          .map((row) => ({ ...row, sourceProjectPath: moved && row.id === "a" ? movedSourcePath : "", sourceStoreId: source.storeId })),
        sources: [source], pendingChatIds: [], recoveries: [], writeBlocked: false,
      });
    } else if (query.chatId === "a" && query.full === "1" && request.method === "GET") {
      json(response, { ...chatPage(aFull), chatDigests: { a: aDigest }, chatMetadataShapes: { a: aShape } });
    } else if (query.chatId === "a" && request.method === "GET") {
      if (retry && ++retryReads === 1) json(response, { detail: "Unavailable during preview" }, 409);
      else if (query.chatProjectPath === movedSourcePath) json(response, chatPage({ ...aRecent, items: [message("a-moved", "Message from the confirmed source")] }));
      else if (query.before === firstCursor) json(response, chatPage(aOlder));
      else if (query.before === undefined) json(response, chatPage(aRecent, firstCursor));
      else json(response, { detail: "Unexpected history cursor" }, 422);
    } else if (query.chatId === "b" && !query.full && request.method === "GET") {
      await staleRecord.promise;
      json(response, chatPage(bRecord));
    } else {
      json(response, { detail: "Expected catalog or scoped chat read" }, 422);
    }
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
  await page.addInitScript(() => {
    const fetch = window.fetch.bind(window);
    window.fetch = async (url, options) => {
      const params = new URL(url, location.origin).searchParams;
      if (params.get("chatId") !== "b") return fetch(url, options);
      // Model a transport that has already received B despite cancellation.
      const response = await fetch(url, { ...options, signal: undefined });
      const text = await response.text();
      setTimeout(() => { window.staleRecordDelivered = true; }, 0);
      return new Response(text, { status: response.status });
    };
  });

  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const chatA = page.getByRole("button", { name: "Chat A", exact: true });
  const chatB = page.getByRole("button", { name: "Chat B", exact: true });
  const loadOlder = page.getByRole("button", { name: "Load older messages", exact: true });
  const messages = page.locator("#messages p");
  const state = async () => JSON.parse(await page.locator("#state").textContent());

  await chatA.waitFor({ timeout: 8000 });
  await chatB.waitFor();
  await page.waitForFunction(() => JSON.parse(document.querySelector("#state").textContent).ready);
  assert.deepEqual(requests, [{ method: "GET", catalog: "1" }], "cold sidebar reads only the catalog");

  await chatA.click();
  await messages.getByText("Recent message in A").waitFor();
  assert.deepEqual(await messages.allTextContents(), ["Recent message in A"]);
  assert.deepEqual(await state(), { activeId: "a", pending: false, ready: true, preview: false, authoritative: null, loadedIds: [] },
    "the first page is visible before any complete transcript is loaded");
  await loadOlder.click();
  await messages.getByText("Older message in A").waitFor();
  assert.deepEqual(await messages.allTextContents(), ["Older message in A", "Recent message in A"],
    "older messages prepend without duplicating or replacing the visible page");
  assert.equal(await loadOlder.count(), 0, "oldest page removes the continuation affordance");
  assert.deepEqual(requests.slice(1), [
    { method: "GET", chatId: "a", chatProjectPath: "" },
    { method: "GET", chatId: "a", chatProjectPath: "", before: firstCursor },
  ], "older request carries the server cursor and B remains unread");

  const bRequested = page.waitForRequest((request) => new URL(request.url()).searchParams.get("chatId") === "b");
  await chatB.click();
  await bRequested;
  await chatA.click();
  await messages.getByText("Recent message in A").waitFor();
  await loadOlder.waitFor();
  staleRecord.resolve();
  await page.waitForFunction(() => window.staleRecordDelivered === true);
  assert.deepEqual(await messages.allTextContents(), ["Recent message in A"], "late B response cannot replace A's visible page");
  assert.equal((await state()).authoritative, null, "partial pages never enter getChatById");

  await page.evaluate(() => window.appendAndSave());
  await messages.getByText("Appended message in A").waitFor();
  assert.deepEqual(await state(), {
    activeId: "a", pending: false, ready: true, preview: false,
    authoritative: ["Older message in A", "Recent message in A", "Appended message in A"], loadedIds: ["a"],
  }, "explicit load materializes only A's full history before append and persistence");
  assert.deepEqual(requests.filter((request) => request.full === "1"), [
    { method: "GET", chatId: "a", chatProjectPath: "", full: "1" },
  ], "the only complete transcript read is the edited owner");
  assert.equal(writes.length, 1, "the append issues exactly one single-chat write");
  const [write] = writes;
  assert.equal(write.chatId, "a");
  assert.equal(write.projectPath, "");
  assert.deepEqual(write.sourceRevisions, [source]);
  assert.equal(write.chatPatch.baseDigest, aDigest);
  assert.deepEqual(write.chatPatch.itemOrder, ["a-old", "a-recent", "a-new"]);
  assert.deepEqual(write.chatPatch.newItems, { "a-new": message("a-new", "Appended message in A") });
  assert.equal(await chatB.count(), 1, "B stays in the catalog without its full history entering the write");

  await page.goto(`http://127.0.0.1:${server.address().port}/?holdCatalog=1`);
  const warmClickedAt = performance.now();
  await chatA.click();
  await messages.getByText("Recent message in A").waitFor({ timeout: 2500 });
  const warmPreviewMs = Math.round(performance.now() - warmClickedAt);
  assert.deepEqual(await state(), { activeId: "a", pending: true, ready: false, preview: true, authoritative: null, loadedIds: [] },
    "warm sidebar selection shows a read-only page while the authoritative catalog is pending");
  assert.equal(await loadOlder.count(), 0);
  const warmReads = requests.filter((request) => request.chatId === "a" && !request.before && !request.full).length;
  heldCatalog.resolve();
  await page.waitForFunction(() => JSON.parse(document.querySelector("#state").textContent).ready);
  assert.equal((await state()).pending, false);
  assert.equal(requests.filter((request) => request.chatId === "a" && !request.before && !request.full).length, warmReads,
    "the confirmed catalog source reuses the page already received");

  await page.goto(`http://127.0.0.1:${server.address().port}/?movedCatalog=1`);
  await chatA.click();
  await messages.getByText("Recent message in A").waitFor();
  assert.equal((await state()).preview, true);
  movedCatalog.resolve();
  await messages.getByText("Message from the confirmed source").waitFor();
  assert.deepEqual(await messages.allTextContents(), ["Message from the confirmed source"]);
  assert.deepEqual(await state(), { activeId: "a", pending: false, ready: true, preview: false, authoritative: null, loadedIds: [] });

  await page.goto(`http://127.0.0.1:${server.address().port}/?retryCatalog=1`);
  const speculativeFailure = page.waitForResponse((response) =>
    new URL(response.url()).searchParams.get("chatId") === "a" && response.status() === 409);
  await chatA.click();
  await (await speculativeFailure).finished();
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await messages.count(), 0);
  assert.equal((await state()).pending, true);
  retryCatalog.resolve();
  await messages.getByText("Message from the confirmed source").waitFor();
  assert.equal(retryReads, 2, "a failed warm read retries once at the confirmed source");

  await page.goto(`http://127.0.0.1:${server.address().port}/?removedCatalog=1`);
  await chatA.click();
  await messages.getByText("Message from the confirmed source").waitFor();
  removedCatalog.resolve();
  await page.waitForFunction(() => JSON.parse(document.querySelector("#state").textContent).ready);
  assert.equal(await messages.count(), 0, "catalog removal clears an already-rendered warm preview");
  assert.equal((await state()).preview, false);
  console.log(`PASS warm preview in ${warmPreviewMs}ms with the catalog blocked, stale source handling, paging and scoped persistence`);
} finally {
  staleRecord.resolve();
  heldCatalog.resolve();
  movedCatalog.resolve();
  retryCatalog.resolve();
  removedCatalog.resolve();
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
