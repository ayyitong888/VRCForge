import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";
import jsonPatch from "fast-json-patch";

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const bundle = await build({
  stdin: {
    resolveDir: process.cwd(), loader: "tsx",
    contents: `
import React from "react"; import {createRoot} from "react-dom/client";
import i18n from "i18next"; import {initReactI18next} from "react-i18next";
import {useChatSessions} from "./src/hooks/use-chat-sessions";
await i18n.use(initReactI18next).init({lng:"en",resources:{en:{translation:{chat:{deletePermanentlyPartialSuccess:"Chat record deleted, but runtime history cleanup is incomplete: {{error}}"}}}}});
const noop=()=>{};
function Harness(){ const sessions=useChatSessions({endpoint:location.origin,activeView:"chat",runtimeConnected:true,projectPrefsReady:true,projectPaths:[],customProjectPaths:[],activeProjectPath:"",setActiveProjectPath:noop,setActiveView:noop,setError:(error)=>window.__error=error,expandProjectGroup:noop,initialChatState:{chats:[],activeChatId:""}}); window.sessions=sessions; return <div/>; }
window.__storageTrace=[]; const originalDebug=console.debug; console.debug=(...args)=>{if(args[0]==="[vrcforge:chat-storage]")window.__storageTrace.push(args[1]);else originalDebug(...args)};
createRoot(document.getElementById("root")).render(<Harness/>);
`,
  }, bundle: true, write: false, format: "esm", jsx: "automatic",
  define: {"process.env.NODE_ENV": '"test"'},
});
const makeChat = (id, sessionId, title) => ({
  id, sessionId, title, projectPath: "", projectType: "general", revision: 0,
  createdAt: "2026-01-01T00:00:00Z", updatedAt: "2026-01-01T00:00:00Z",
  items: [{ id: "user-" + id, type: "user", text: title }],
});
const initialChats = [
  makeChat("chat-target", "session-target", "Delete target"),
  makeChat("chat-stale", "session-stale", "Deleted by another profile"),
  makeChat("chat-retained", "session-retained", "Retained conversation"),
];
let chats = [...initialChats];
const posts = [];
const runtimeDeletes = [];
const postStatuses = [];
let runtimeDeleteStatus = 200;
const digest = (value) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
const source = () => ({ storeId: "chat.app", scope: "app", exists: true, status: "ok", digest: digest(chats) });
const shapeOf = (value) => value && typeof value === "object" && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).filter(([key]) => key !== "items").map(([key, child]) => [key, shapeOf(child)]))
  : true;

function json(response, payload, status = 200) {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(payload));
}

const server = createServer(async (request, response) => {
  const url = new URL(request.url, "http://127.0.0.1");
  if (url.pathname === "/fixture.js") {
    response.setHeader("Content-Type", "text/javascript");
    response.end(bundle.outputFiles[0].text);
    return;
  }
  if (url.pathname === "/api/app/chats" && request.method === "GET") {
    if (url.searchParams.get("catalog") === "1") {
      json(response, {
        ok: true,
        entries: chats.map(({ items, ...chat }) => ({ ...chat, sourceProjectPath: "", sourceStoreId: source().storeId })),
        sources: [source()], pendingChatIds: [], recoveries: [], writeBlocked: false,
      });
      return;
    }
    const chat = chats.find((chat) => chat.id === url.searchParams.get("chatId"));
    if (!chat || url.searchParams.get("full") !== "1") {
      json(response, { detail: "Chat not found" }, 404);
      return;
    }
    json(response, {
      ok: true, chats: [chat], sourceRevision: source(), hasOlder: false, nextBefore: null,
      chatDigests: { [chat.id]: digest(chat) }, chatMetadataShapes: { [chat.id]: shapeOf(chat) },
    });
    return;
  }
  if (url.pathname.startsWith("/api/app/agent/chats/") && request.method === "DELETE") {
    runtimeDeletes.push({
      sessionId: decodeURIComponent(url.pathname.split("/").at(-1)),
      targetExists: chats.some((chat) => chat.id === "chat-target"),
    });
    json(response, runtimeDeleteStatus === 200 ? { ok: true, removed: true } : { detail: "runtime cleanup failed" }, runtimeDeleteStatus);
    return;
  }
  if (url.pathname === "/api/app/chats/single" && request.method === "POST") {
    let raw = "";
    for await (const chunk of request) raw += chunk;
    const body = JSON.parse(raw);
    const current = chats.find((chat) => chat.id === body.chatId);
    const revision = source();
    const staleSource = body.sourceRevisions?.find((entry) => entry.storeId === revision.storeId)?.digest !== revision.digest;
    const status = postStatuses.shift() ?? (staleSource ? 409 : !current ? 404 : 200);
    posts.push({ body, serverIds: chats.map((chat) => chat.id), status });
    if (status >= 400) {
      const detail = { 422: "chat patch invalid", 409: "chat source changed", 404: "Chat not found", 503: "temporary storage error" }[status];
      json(response, { detail }, status);
      return;
    }
    if (body.delete) {
      chats = chats.filter((chat) => chat.id !== body.chatId);
      json(response, { ok: true, sourceRevisions: [source()], chatDigests: {}, chatMetadataShapes: {} });
      return;
    }
    const updated = jsonPatch.applyPatch(current, body.chatPatch.metadataPatch, true, false).newDocument;
    chats = chats.map((chat) => chat.id === updated.id ? updated : chat);
    json(response, {
      ok: true, sourceRevisions: [source()],
      chatDigests: { [updated.id]: digest(updated) }, chatMetadataShapes: { [updated.id]: shapeOf(updated) },
    });
    return;
  }
  response.setHeader("Content-Type", "text/html");
  response.end('<div id="root"></div><script type="module" src="/fixture.js"></script>');
});

let browser;
try {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const origin = "http://127.0.0.1:" + server.address().port;
  await page.addInitScript(() => localStorage.setItem("vrcforge_developer_options_enabled", "true"));
  await page.route("**/*", (route) => route.request().url().startsWith(origin) ? route.continue() : route.abort());

  async function loadAll() {
    await page.waitForFunction(() => window.sessions?.chatStorageReady === true);
    await page.evaluate((ids) => Promise.all(ids.map((id) => window.sessions.loadChat(id))), initialChats.map((chat) => chat.id));
    await page.waitForFunction(() => window.sessions.chats.length === 3);
  }
  const state = () => page.evaluate(() => ({
    ids: window.sessions.sidebarChats.map((chat) => chat.id),
    error: window.__error, chatStorageError: window.sessions.chatStorageError,
  }));
  const failedSaves = (count) => page.waitForFunction((count) => window.__storageTrace
    .filter((event) => event.kind === "save" && event.stage === "failed").length >= count, count);

  await page.goto(origin);
  await loadAll();
  postStatuses.push(422);
  await page.evaluate(() => window.sessions.deleteChatPermanently("chat-target"));
  await failedSaves(1);
  await page.waitForTimeout(1900);
  const rejectedDelete = await state();
  assert.equal(posts.length, 1, "a stable rejection does not resend deletion on each autosave tick");
  assert.equal(posts[0].body.chatId, "chat-target");
  assert.equal(posts[0].body.delete, true);
  assert.equal(rejectedDelete.ids.includes("chat-target"), true, "failed durable deletion restores the chat row");
  assert.equal(rejectedDelete.error, undefined, "precommit rejection does not occupy the common App error slot");
  assert.equal(rejectedDelete.chatStorageError?.kind, "save", "chat storage owns the precommit failure");
  assert.equal(runtimeDeletes.length, 0, "runtime history remains when durable deletion is rejected");

  postStatuses.push(422, 422);
  await page.evaluate(() => window.sessions.updateChat("chat-retained", (chat) => ({ ...chat, title: "edited before stable rejection" })));
  await failedSaves(2);
  await page.waitForTimeout(1000);
  assert.equal(posts.length, 2, "a stable 422 does not retry an unchanged dirty edit");
  const explicitRetry = await page.evaluate(async () => {
    try { await window.sessions.persistChatsNow(); return false; }
    catch { return true; }
  });
  assert.equal(explicitRetry, true, "an explicit save remains available after an unchanged stable rejection");
  assert.equal(posts.length, 3, "the explicit save makes one new attempt");

  postStatuses.push(503, 422);
  await page.evaluate(() => window.sessions.updateChat("chat-retained", (chat) => ({ ...chat, title: "edited after rejection" })));
  await failedSaves(5);
  await page.waitForTimeout(1100);
  assert.equal(posts.length, 5, "a new edit retries and a temporary 5xx retries before a stable 422 stops the same generation");
  assert.deepEqual(posts.slice(3).map((post) => post.status), [503, 422]);

  await page.reload();
  await loadAll();
  chats = chats.filter((chat) => chat.id !== "chat-stale");
  runtimeDeleteStatus = 500;
  await page.evaluate(() => window.sessions.deleteChatPermanently("chat-target"));
  await page.waitForFunction(() => window.__error?.includes("runtime cleanup failed"));
  const cleanupFailure = await state();
  const staleDeleteAttempts = posts.slice(5);
  assert.deepEqual(staleDeleteAttempts.map(({ body, status }) => [body.chatId, body.delete, status]), [
    ["chat-target", true, 409], ["chat-target", true, 200],
  ], "a stale source reconciles and retries only the intended chat deletion");
  assert.deepEqual(staleDeleteAttempts[0].serverIds, ["chat-target", "chat-retained"]);
  assert.deepEqual(runtimeDeletes, [{ sessionId: "session-target", targetExists: false }], "runtime cleanup follows transcript deletion");
  assert.deepEqual(cleanupFailure.ids, ["chat-retained"], "runtime cleanup failure cannot resurrect a durably deleted chat or the stale row");
  assert.equal(cleanupFailure.error, "Chat record deleted, but runtime history cleanup is incomplete: runtime cleanup failed");

  await page.evaluate(async () => {
    window.sessions.updateChat("chat-retained", (chat) => ({ ...chat, title: "saved after cleanup error" }));
    await window.sessions.persistChatsNow();
  });
  assert.equal(posts.at(-1).body.chatId, "chat-retained");
  assert.equal(chats[0].title, "saved after cleanup error");
  assert.equal(await page.evaluate(() => window.__error), cleanupFailure.error, "a later successful chat save does not clear a runtime cleanup failure");
  chats = [makeChat("alias-deleted", "shared-session", "Delete one alias"), makeChat("alias-survivor", "shared-session", "Keep this alias")];
  runtimeDeleteStatus = 200;
  await page.reload();
  await page.waitForFunction(() => window.sessions?.chatStorageReady === true);
  await page.evaluate(() => window.sessions.deleteChatPermanently("alias-deleted"));
  assert.deepEqual(chats.map(chat => chat.id), ["alias-survivor"], "only the selected alias is deleted");
  assert.deepEqual(runtimeDeletes.map(item => item.sessionId), ["session-target"], "an unloaded surviving alias keeps its runtime session");
  await page.evaluate(() => window.sessions.deleteChatPermanently("alias-survivor"));
  assert.deepEqual(chats, []);
  assert.deepEqual(runtimeDeletes.map(item => item.sessionId), ["session-target", "shared-session"], "removing the last alias cleans up runtime history");
  console.log("PASS stable rejection, explicit retry, scoped stale-delete reconciliation, and runtime cleanup ownership");
} finally {
  await browser?.close();
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}
