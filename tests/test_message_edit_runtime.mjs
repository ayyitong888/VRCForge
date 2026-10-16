import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// Execute the production App handlers against controlled runtime receipts.
const source = readFileSync("src/App.tsx", "utf8");
const handlers = source.slice(source.indexOf("  function editConversationMessage("), source.indexOf("  function retryConversationItem("));
const compiled = ts.transpileModule(handlers, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
function fixture() {
  const chat = { id: "chat", sessionId: "session", title: "title", items: [
    { id: "old", type: "user", text: "old" },
    { id: "user", type: "user", text: "有没有简短向的命中", clientTurnId: "turn" },
    { id: "tool", type: "timeline_event", event: { kind: "tool", status: "done" } },
  ] };
  const scope = {
    chat, activeChatId: "chat", editingMessage: null, input: "unsent draft", attachments: [],
    running: false, compacting: false, visibleQueued: [], unresolved: false,
    messageEditSaveInFlightRef: { current: false }, messageEditSnapshotRef: { current: null },
    isChatRunActive: () => scope.running,
    hasUnresolvedRuntimeState: () => scope.unresolved,
    getChatById: (id) => id === chat.id ? chat : null,
    latestConversationItemId: (items, predicate) => [...items].reverse().find(predicate)?.id,
    cloneChatAttachments: (items) => structuredClone(items),
    setEditingMessage: (value) => { scope.editingMessage = typeof value === "function" ? value(scope.editingMessage) : value; sync(); },
    setInput: (value) => { scope.input = value; }, setAttachments: (value) => { scope.attachments = value; },
    setRuntimeNotice: () => {}, setError: (value) => scope.errors.push(value),
    setSavingMessageEdit: () => {}, t: (key) => key,
    resolveContextLimit: () => ({ known: false }), providerSnapshot: {}, currentModelInfo: null, apiConfig: null,
    endpoint: "http://fixture", fetchAgentRuns: async () => ({ ok: true, runs: [], count: 0 }),
    runTurnNow: async (...args) => { scope.sent.push(args); }, errors: [], sent: [],
  };
  function sync() { scope.messageEditSnapshotRef.current = { draft: scope.editingMessage, chatId: scope.activeChatId, busy: scope.running || scope.compacting || scope.visibleQueued.length > 0 || scope.unresolved }; }
  sync();
  const handlers = new Function("scope", `with(scope) { ${compiled}; return {editConversationMessage, saveMessageEdit, cancelMessageEdit}; }`)(scope);
  return { scope, handlers, sync };
}

test("running latest message opens only a local draft and keeps executed history", () => {
  const { scope, handlers, sync } = fixture(); scope.running = true; sync();
  const original = structuredClone(scope.chat);
  handlers.editConversationMessage("user");
  assert.equal(scope.editingMessage?.draftText, "有没有简短向的命中");
  assert.deepEqual(scope.chat, original);
  assert.equal(scope.sent.length, 0);
  handlers.cancelMessageEdit();
  assert.equal(scope.input, "unsent draft");
  assert.deepEqual(scope.chat, original);
});

test("save waits for local work, compaction, queue and unresolved runtime state", async () => {
  for (const condition of ["running", "compacting", "visibleQueued", "unresolved"]) {
    const { scope, handlers, sync } = fixture(); handlers.editConversationMessage("user");
    scope[condition] = condition === "visibleQueued" ? [{}] : true; sync();
    await handlers.saveMessageEdit();
    assert.equal(scope.sent.length, 0, condition);
    assert.ok(scope.editingMessage, condition);
  }
});

test("fresh cancelling receipt prevents resend even after local sending is false", async () => {
  const { scope, handlers } = fixture(); handlers.editConversationMessage("user");
  scope.fetchAgentRuns = async () => ({ ok: true, runs: [{ status: "cancelling", sessionId: "session" }], count: 1 });
  await handlers.saveMessageEdit();
  assert.equal(scope.sent.length, 0);
  assert.ok(scope.editingMessage);
});

test("one explicit save starts one rerun; changed chat or cancelled draft cannot resume it", async () => {
  for (const change of ["none", "chat", "cancel", "latest", "runtime"]) {
    const { scope, handlers, sync } = fixture(); handlers.editConversationMessage("user");
    let finish; scope.fetchAgentRuns = () => new Promise(resolve => { finish = resolve; });
    const first = handlers.saveMessageEdit();
    const second = handlers.saveMessageEdit();
    if (change === "chat") scope.activeChatId = "other";
    if (change === "cancel") handlers.cancelMessageEdit();
    if (change === "latest") scope.chat.items.push({ type: "user", id: "new", text: "new" });
    if (change === "runtime") scope.running = true;
    sync();
    assert.equal(typeof finish, "function", "save checks backend before changing history");
    finish({ ok: true, runs: [{ status: "completed", sessionId: "session" }], count: 1 });
    await Promise.all([first, second]);
    assert.equal(scope.sent.length, change === "none" ? 1 : 0, change);
    assert.equal(scope.messageEditSaveInFlightRef.current, false);
  }
});

test("failed runtime read preserves the editable draft without sending", async () => {
  const { scope, handlers } = fixture(); handlers.editConversationMessage("user");
  scope.fetchAgentRuns = async () => { throw new Error("offline"); };
  await handlers.saveMessageEdit();
  assert.equal(scope.sent.length, 0);
  assert.ok(scope.editingMessage);
});
