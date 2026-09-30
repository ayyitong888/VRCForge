import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

// Execute the real deleteChatPermanently with local ports. No renderer,
// provider, backend, or live chat storage is involved.
const hookSource = fs.readFileSync("src/hooks/use-chat-sessions.ts", "utf8");
const appSource = fs.readFileSync("src/lib/api/app.ts", "utf8");

// API layer must expose the DELETE endpoint through both transport channels.
assert.ok(
  appSource.includes("/api/app/agent/chats/"),
  "app.ts must target DELETE /api/app/agent/chats/{id}",
);
assert.ok(appSource.includes('"delete_chat_permanently"'), "app.ts must invoke the typed desktop command");
assert.ok(appSource.includes('method: "DELETE"'), "the permanent delete must use HTTP DELETE");
assert.ok(appSource.includes("encodeURIComponent(chatId)"), "path ids must be URL-encoded");

// The hook must pass the runtime session id (chat.sessionId), never chat.id.
assert.ok(
  hookSource.includes("deleteChatPermanentlyApi(endpoint, runtimeSessionId)"),
  "permanent delete must use the runtime session id",
);
assert.ok(
  hookSource.includes('String(removedSnapshot?.sessionId || "").trim()'),
  "the runtime session id must be read from the removed chat snapshot",
);

const deleteStart = hookSource.indexOf("  function deleteChatPermanently(");
assert.notEqual(deleteStart, -1, "deleteChatPermanently must exist");
const deleteEnd = hookSource.indexOf("\n  function bindProject(", deleteStart);
const deleteJs = ts.transpileModule(hookSource.slice(deleteStart, deleteEnd), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;

class ApiError extends Error {
  constructor(status) {
    super(`HTTP ${status}`);
    this.status = status;
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

async function runScenario({ apiResult, sessionId = "runtime-session-1" }) {
  const chatsRef = {
    current: [
      { id: "chat-older", sessionId: "runtime-session-old", title: "older" },
      { id: "chat-target", sessionId, title: "target" },
      { id: "chat-newer", sessionId: "runtime-session-new", title: "newer" },
    ],
  };
  const chatDeletedIdsRef = { current: new Set() };
  const calls = {
    deleteApi: [],
    setError: [],
    setChats: [],
    setActiveChatId: [],
    setDeleteTargetId: [],
    setChatMenu: [],
    dirty: 0,
  };
  let pendingApi = null;
  const ports = {
    chatsRef,
    chatDeletedIdsRef,
    markChatsDirty: () => {
      calls.dirty += 1;
    },
    setChats: (value) => {
      calls.setChats.push(value);
    },
    activeChatId: "chat-target",
    setActiveChatId: (value) => {
      calls.setActiveChatId.push(value);
    },
    setDeleteTargetId: (value) => {
      calls.setDeleteTargetId.push(value);
    },
    setChatMenu: (value) => {
      calls.setChatMenu.push(value);
    },
    endpoint: "fixture-endpoint",
    deleteChatPermanentlyApi: (endpoint, chatId) => {
      calls.deleteApi.push({ endpoint, chatId });
      pendingApi = apiResult instanceof Error ? Promise.reject(apiResult) : Promise.resolve(apiResult);
      return pendingApi;
    },
    ApiError,
    t: (key) => key,
    setError: (message) => {
      calls.setError.push(message);
    },
    chatSaveErrorMessage: (_cause, fallback) => fallback,
  };
  const deleteChatPermanently = new Function(
    ...Object.keys(ports),
    `${deleteJs}\nreturn deleteChatPermanently;`,
  )(...Object.values(ports));
  deleteChatPermanently("chat-target");
  // The optimistic removal must stay synchronous.
  assert.deepEqual(chatsRef.current.map((chat) => chat.id), ["chat-older", "chat-newer"]);
  assert.equal(calls.dirty, 1);
  assert.deepEqual(calls.setActiveChatId, [""]);
  assert.deepEqual(calls.setDeleteTargetId, [""]);
  assert.deepEqual(calls.setChatMenu, [null]);
  assert.equal(chatDeletedIdsRef.current.has("chat-target"), true);
  if (pendingApi) {
    await pendingApi.catch(() => undefined);
  }
  await tick();
  await tick();
  return { chatsRef, chatDeletedIdsRef, calls };
}

// The endpoint must receive the runtime session id, not the client chat id.
const mapping = await runScenario({ apiResult: { ok: true, removed: true } });
assert.deepEqual(
  mapping.calls.deleteApi,
  [{ endpoint: "fixture-endpoint", chatId: "runtime-session-1" }],
  "DELETE must target the runtime session id",
);

// API success keeps the optimistic removal and shows nothing.
assert.deepEqual(mapping.chatsRef.current.map((chat) => chat.id), ["chat-older", "chat-newer"]);
assert.equal(mapping.chatDeletedIdsRef.current.has("chat-target"), true);
assert.equal(mapping.calls.setError.length, 0);
assert.equal(mapping.calls.dirty, 1);

// API failure restores the chat at its original position and surfaces an error.
const failed = await runScenario({ apiResult: new ApiError(500) });
assert.deepEqual(
  failed.chatsRef.current.map((chat) => chat.id),
  ["chat-older", "chat-target", "chat-newer"],
  "a failed backend delete must restore the chat at its original position",
);
assert.equal(failed.chatDeletedIdsRef.current.has("chat-target"), false, "the deleted-id marker must be undone on restore");
assert.ok(failed.calls.setError.length >= 1, "the failure must surface a user-visible error");
assert.ok(failed.calls.dirty >= 2, "the restore must re-mark chat storage dirty so autosave persists it");

// An explicit ok:false body is treated as a failure too.
const declined = await runScenario({ apiResult: { ok: false, removed: false } });
assert.deepEqual(declined.chatsRef.current.map((chat) => chat.id), ["chat-older", "chat-target", "chat-newer"]);
assert.ok(declined.calls.setError.length >= 1);

// A chat that never ran has no runtime session: no backend call, removal stands.
const neverRan = await runScenario({ sessionId: "" });
assert.equal(neverRan.calls.deleteApi.length, 0, "chats without a runtime session must not call the endpoint");
assert.deepEqual(neverRan.chatsRef.current.map((chat) => chat.id), ["chat-older", "chat-newer"]);
assert.equal(neverRan.calls.setError.length, 0);

// If the chat id reappeared before the failure landed, never duplicate it.
{
  const chatsRef = {
    current: [
      { id: "chat-older", sessionId: "runtime-session-old", title: "older" },
      { id: "chat-target", sessionId: "runtime-session-1", title: "target" },
      { id: "chat-newer", sessionId: "runtime-session-new", title: "newer" },
    ],
  };
  const chatDeletedIdsRef = { current: new Set() };
  const calls = { setError: [], dirty: 0 };
  let pendingApi = null;
  const ports = {
    chatsRef,
    chatDeletedIdsRef,
    markChatsDirty: () => {
      calls.dirty += 1;
    },
    setChats: () => {},
    activeChatId: "",
    setActiveChatId: () => {},
    setDeleteTargetId: () => {},
    setChatMenu: () => {},
    endpoint: "fixture-endpoint",
    deleteChatPermanentlyApi: () => {
      pendingApi = Promise.reject(new ApiError(500));
      return pendingApi;
    },
    ApiError,
    t: (key) => key,
    setError: (message) => {
      calls.setError.push(message);
    },
    chatSaveErrorMessage: (_cause, fallback) => fallback,
  };
  const deleteChatPermanently = new Function(
    ...Object.keys(ports),
    `${deleteJs}\nreturn deleteChatPermanently;`,
  )(...Object.values(ports));
  deleteChatPermanently("chat-target");
  chatsRef.current.splice(1, 0, { id: "chat-target", sessionId: "runtime-session-1", title: "re-added" });
  await pendingApi.catch(() => undefined);
  await tick();
  await tick();
  assert.equal(
    chatsRef.current.filter((chat) => chat.id === "chat-target").length,
    1,
    "restore must not duplicate a re-added chat",
  );
  assert.ok(calls.setError.length >= 1);
}

console.log("chat permanent delete + native history UI wiring: ok");
