import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

// Execute the actual hook's save/reconcile and enqueue functions with local ports.
// No renderer, provider, backend, or live chat storage is involved.
const source = fs.readFileSync("src/hooks/use-chat-sessions.ts", "utf8");
const saveErrorHelperStart = source.indexOf("function chatSaveErrorMessage(");
assert.notEqual(saveErrorHelperStart, -1, "chat save errors need an explicit transient classification");
const saveErrorHelperEnd = source.indexOf("\n}\n", saveErrorHelperStart) + 2;
const saveErrorHelper = ts.transpileModule(
  source.slice(saveErrorHelperStart, saveErrorHelperEnd),
  { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } },
).outputText;
class HelperApiError extends Error { constructor(status, message) { super(message); this.status = status; } }
const chatSaveErrorMessage = new Function("ApiError", `${saveErrorHelper}\nreturn chatSaveErrorMessage;`)(HelperApiError);
assert.equal(
  chatSaveErrorMessage(new HelperApiError(413, "Chat payload exceeds the 16 MiB save limit."), "blocked"),
  "Chat payload exceeds the 16 MiB save limit.",
  "413 must expose an accurate retryable save error",
);
assert.equal(
  chatSaveErrorMessage(new HelperApiError(500, "Runtime is temporarily unavailable."), "blocked"),
  "Runtime is temporarily unavailable.",
  "temporary failures must remain retryable and accurate",
);
assert.equal(
  chatSaveErrorMessage(new HelperApiError(409, "Concurrent chat update."), "blocked"),
  "blocked",
  "conflict fallback remains owned by the recovery path",
);
assert.equal(
  chatSaveErrorMessage(new Error("Local bridge temporarily unavailable."), "blocked"),
  "Local bridge temporarily unavailable.",
  "bridge failures must not be mislabeled as source recovery",
);
// Reproduce a passive effect from an earlier render arriving between deltas.
// Execute the real chats-only effects, if any, against that earlier snapshot.
const chatsOnlyEffects = [];
const hookAst = ts.createSourceFile("hook.ts", source, ts.ScriptTarget.Latest, true);
function collectEffects(node) {
  if (ts.isCallExpression(node) && node.expression.getText(hookAst) === "useEffect") {
    const [callback, dependencies] = node.arguments;
    if (dependencies && ts.isArrayLiteralExpression(dependencies)
      && dependencies.elements.length === 1 && dependencies.elements[0].getText(hookAst) === "chats") {
      chatsOnlyEffects.push(callback.getText(hookAst));
    }
  }
  ts.forEachChild(node, collectEffects);
}
collectEffects(hookAst);
const mutatorSource = source.slice(
  source.indexOf("  function updateChatIfRevision("),
  source.indexOf("\n  function appendToChat(", source.indexOf("  function updateChatIfRevision(")),
);
const mutatorJs = ts.transpileModule(mutatorSource, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
const mutatorPorts = {
  chatsRef: { current: [{ id: "stream-chat", items: [{ id: "stream", type: "streaming", clientTurnId: "turn-1", text: "" }] }] },
  applyRevisionedChatUpdate: (chat, _revision, updater) => ({ applied: true, chat: updater(chat) }),
  stripSupersededStreamingItems: items => items,
  cacheChatContextUsageFast: chat => chat,
  markChatsDirty: () => { mutatorPorts.dirty += 1; },
  setChats: chats => { mutatorPorts.savedSnapshot = chats; },
  dirty: 0,
  savedSnapshot: null,
};
const updateChatIfRevision = new Function(
  ...Object.keys(mutatorPorts),
  `${mutatorJs}\nreturn updateChatIfRevision;`,
)(...Object.values(mutatorPorts));
updateChatIfRevision("stream-chat", undefined, current => ({
  ...current,
  items: [{ ...current.items[0], text: `${current.items[0].text}first` }],
}));
const earlierRender = mutatorPorts.savedSnapshot;
updateChatIfRevision("stream-chat", undefined, current => ({
  ...current,
  items: [{ ...current.items[0], text: `${current.items[0].text}second` }],
}));
for (const effect of chatsOnlyEffects) {
  new Function("chatsRef", "chats", `(${effect})();`)(mutatorPorts.chatsRef, earlierRender);
}
assert.equal(mutatorPorts.chatsRef.current[0].items[0].text, "firstsecond", "rapid deltas must retain both mutations in the ref");
assert.equal(mutatorPorts.savedSnapshot[0].items[0].text, "firstsecond", "the persisted snapshot must read the mutation-owned ref");
assert.equal(mutatorPorts.dirty, 2);
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
