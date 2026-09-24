import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = await readFile(path.join(root, "src/hooks/use-context-compaction-controller.ts"), "utf8");
const implementation = source.slice(source.indexOf("type CompactTrigger"));
const harness = `
const useRef = (...args) => globalThis.__compactionHarness.hooks.useRef(...args);
const useState = (...args) => globalThis.__compactionHarness.hooks.useState(...args);
const useTranslation = () => ({ t: (key) => key, i18n: { language: "en", resolvedLanguage: "en" } });
const compactAgentHistory = (...args) => globalThis.__compactionHarness.compactAgentHistory(...args);
const stripTransientConversationItems = (items) => items;
const collectCompactedAttachmentReferences = () => [];
const mergeCompactedAttachmentReferences = (current) => current;
const estimateIncomingContextTokens = () => 0;
const estimateTextTokens = (text) => String(text || "").length;
const contextUsageMatchesModel = () => true;
const buildDurableCompactionStateEntries = () => [];
const resolveContextLimit = () => ({ limit: 1000, known: true, source: "test" });
const fingerprintCompactionSource = (entries) => entries.map((entry) => entry.text).join("|");
const invalidateCompactedWindowUsage = (items) => items;
const buildChatHistory = (items) => items.map((item) => ({ role: item.type === "user" ? "user" : "assistant", text: item.text || item.response || "" }));
const latestAgentContextUsage = () => undefined;
const boundedCompactionAttempts = (value) => value || 1;
const boundedCompactionLatencyMs = () => 1;
const boundedCompactionSummaryCharacters = (value) => value;
const evaluateCompactionBudget = () => ({ shouldCompact: true, level: "hard-limit", reason: "test", projectedTokens: 120, targetAfterTokens: 100, minimumReductionTokens: 1 });
${implementation}
`;
const compiled = ts.transpileModule(harness, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: "use-context-compaction-controller.ts",
}).outputText;

const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const controllerModule = await import(moduleUrl);

function makeChat() {
  return {
    id: "chat-1",
    sessionId: "session-old",
    revision: 0,
    title: "test",
    items: [
      { id: "user-1", type: "user", text: "A long user request that should be compacted.", createdAt: "2026-01-01T00:00:00Z" },
      { id: "agent-1", type: "agent", response: "A long assistant response that should be compacted.", timeline: [], createdAt: "2026-01-01T00:00:01Z" },
      { id: "user-2", type: "user", text: "Keep this newest user turn.", createdAt: "2026-01-01T00:00:02Z" },
    ],
  };
}

function makeHarness(chat, persistChatsNow) {
  let cursor = 0;
  const slots = [];
  const ref = (initial) => {
    const index = cursor++;
    if (!slots[index]) slots[index] = { current: initial };
    return slots[index];
  };
  const state = (initial) => {
    const slot = cursor++;
    if (!(slot in slots)) slots[slot] = initial;
    return [slots[slot], (value) => { slots[slot] = typeof value === "function" ? value(slots[slot]) : value; }];
  };
  const harness = {
    hooks: { useRef: ref, useState: state },
    compactAgentHistory: (...args) => harness.compactAgentHistoryState.promise(...args),
    compactAgentHistoryState: {},
  };
  harness.render = (guard) => {
    cursor = 0;
    return controllerModule.useContextCompactionController({
      getChatById: () => structuredClone(chat),
      updateChat: (_id, updater) => Object.assign(chat, updater(chat)),
      updateChatIfRevision: (_id, expected, updater) => {
        if ((chat.revision || 0) !== expected) return false;
        Object.assign(chat, updater(chat));
        chat.revision = (chat.revision || 0) + 1;
        return true;
      },
      persistChatsNow,
      setError: () => undefined,
      hasUnresolvedRuntimeState: guard,
    });
  };
  globalThis.__compactionHarness = harness;
  return harness;
}

function request() {
  return { chatId: "chat-1", endpoint: "http://test", trigger: "manual", phase: "standalone", provider: "test", model: "test" };
}

test("latest guard callback observed during compaction await prevents replacement", async () => {
  const chat = makeChat();
  let resolveSummary;
  const summaryPromise = new Promise((resolve) => { resolveSummary = resolve; });
  const persist = async () => undefined;
  const harness = makeHarness(chat, persist);
  harness.compactAgentHistoryState.promise = () => summaryPromise;
  const first = harness.render(() => false);
  const pending = first.compactChat(request());
  harness.render(() => true);
  resolveSummary({ summary: "summary", summaryDigest: "digest", entryCount: 2 });
  const outcome = await pending;
  assert.equal(outcome.status, "skipped");
  assert.equal(chat.sessionId, "session-old");
  assert.deepEqual(chat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);
});

test("successful replacement clears session and persistence failure restores it", async () => {
  const chat = makeChat();
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2 });
  const controller = harness.render(() => false);
  const outcome = await controller.compactChat(request());
  assert.equal(outcome.status, "applied");
  assert.equal(chat.sessionId, "");
  assert.notDeepEqual(chat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);

  const restoredChat = makeChat();
  const failingHarness = makeHarness(restoredChat, async () => { throw new Error("disk unavailable"); });
  failingHarness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2 });
  const failing = failingHarness.render(() => false);
  const failed = await failing.compactChat(request());
  assert.equal(failed.status, "failed");
  assert.equal(restoredChat.sessionId, "session-old");
  assert.deepEqual(restoredChat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);
});
