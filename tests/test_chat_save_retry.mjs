import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

// Execute the actual hook's save/reconcile and enqueue functions with local ports.
// No renderer, provider, backend, or live chat storage is involved.
const source = fs.readFileSync("src/hooks/use-chat-sessions.ts", "utf8");
const functions = [
  source.slice(source.indexOf("  async function saveWithOneReconcileRetryWithinStorageOperation("), source.indexOf("\n  useEffect(() => {", source.indexOf("  async function saveWithOneReconcileRetryWithinStorageOperation("))),
  source.slice(source.indexOf("  function enqueueChatSave("), source.indexOf("\n  function touchChat(", source.indexOf("  function enqueueChatSave("))),
].join("\n");
const js = ts.transpileModule(functions, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
class ApiError extends Error { constructor(status) { super(`HTTP ${status}`); this.status = status; } }

function fixture(status, failures = 1) {
  const calls = { save: 0, fetch: 0, reconcile: 0 };
  const chatsRef = { current: [{ id: "unsaved-new-chat", items: [{ id: "user", type: "user", text: "Keep this" }] }] };
  const chatsDirtyRef = { current: true };
  const chatsSaveVersionRef = { current: 1 };
  const error = new ApiError(status);
  const ports = {
    ApiError, chatsRef, chatsDirtyRef, chatsSaveVersionRef, endpoint: "fixture",
    filterPersistableChats: value => value,
    collectChatStorageProjectPaths: () => [],
    enqueueChatStorageOperation: fn => fn(),
    saveChatSnapshotWithinStorageOperation: async () => { if (++calls.save <= failures) throw error; chatsDirtyRef.current = false; },
    fetchChats: async () => { calls.fetch++; return {}; },
    reconcileFetchedChatStorage: async () => { calls.reconcile++; return { status: "ready" }; },
  };
  const save = new Function(...Object.keys(ports), js + "\nreturn persistChatsNow;")(...Object.values(ports));
  return { save, calls, chatsRef, chatsDirtyRef, error };
}
for (const status of [422, 413, 500, 0]) {
  const f = fixture(status);
  await assert.rejects(f.save(), error => error === f.error);
  assert.deepEqual(f.calls, { save: 1, fetch: 0, reconcile: 0 }, "non-conflict errors must not setChats via reconcile and restart debounce");
  assert.equal(f.chatsDirtyRef.current, true);
  assert.equal(f.chatsRef.current[0].id, "unsaved-new-chat");
  await f.save(); // A later explicit retry/new mutation can still save the same data.
  assert.equal(f.calls.save, 2);
  assert.equal(f.chatsDirtyRef.current, false);
}
const conflict = fixture(409);
await conflict.save();
assert.deepEqual(conflict.calls, { save: 2, fetch: 1, reconcile: 1 });
const repeated = fixture(409, 2);
await assert.rejects(repeated.save());
assert.deepEqual(repeated.calls, { save: 2, fetch: 1, reconcile: 1 });
assert.equal(repeated.chatsDirtyRef.current, true);
console.log("chat save retry: ok");
