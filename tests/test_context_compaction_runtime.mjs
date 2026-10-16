import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = await readFile(path.join(root, "src/hooks/use-context-compaction-controller.ts"), "utf8");
const implementation = source.slice(source.indexOf("type CompactTrigger"));
const compactionSource = await readFile(path.join(root, "src/lib/context-compaction.ts"), "utf8");
const invalidationStart = compactionSource.indexOf("export function invalidateCompactedWindowUsage");
const invalidationEnd = compactionSource.indexOf("export function projectRuntimeCompactionItems", invalidationStart);
const invalidationImplementation = invalidationStart >= 0 && invalidationEnd >= 0
  ? compactionSource.slice(invalidationStart, invalidationEnd).replace("export function", "function")
  : "function invalidateCompactedWindowUsage(items) { return items; }";
const boundaryStart = compactionSource.indexOf("export function contextUsageBoundaryItemId");
const boundaryEnd = compactionSource.indexOf("export function latestContextWindowUsage", boundaryStart);
const boundaryImplementation = boundaryStart >= 0 && boundaryEnd >= 0
  ? compactionSource.slice(boundaryStart, boundaryEnd).replace("export function", "function")
  : "function contextUsageBoundaryItemId() { return \"\"; }";
const windowStart = compactionSource.indexOf("export function latestContextWindowUsage");
const windowEnd = compactionSource.indexOf("export function projectRuntimeCompactionItems", windowStart);
const windowImplementation = windowStart >= 0 && windowEnd >= 0
  ? compactionSource.slice(windowStart, windowEnd).replace("export function", "function")
  : "function latestContextWindowUsage() { return undefined; }";
const usageConversationSource = await readFile(path.join(root, "src/lib/conversation-utils.ts"), "utf8");
const usageStart = usageConversationSource.indexOf("export function latestAgentContextUsage");
const usageEnd = usageConversationSource.indexOf("export function buildChatHistory", usageStart);
const usageImplementation = usageConversationSource
  .slice(usageStart, usageEnd)
  .replace("export function", "function");
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
${invalidationImplementation}
${boundaryImplementation}
${windowImplementation}
const mergeCompactionRecovery = (...values) => values.flatMap((value) => Array.isArray(value) ? value : []);
const buildChatHistory = (items) => {
  const summarized = new Set(items.flatMap((item) => item.type === "compact" ? (item.summarizedItemIds || []) : []));
  return items.filter((item) => !summarized.has(item.id)).map((item) => ({ role: item.type === "user" ? "user" : "assistant", text: item.type === "compact" ? item.detail : item.text || item.response || "" }));
};
const visibleHistory = (items) => buildChatHistory(items);
${usageImplementation}
const boundedCompactionAttempts = (value) => value || 1;
const boundedCompactionLatencyMs = () => 1;
const boundedCompactionSummaryCharacters = (value) => value;
const mergeCompactionProviderUsage = (prior, current) => {
  if (!prior && !current) return undefined;
  if (!prior) return { ...current };
  if (!current) return { ...prior, exact: false, unavailableReason: prior.unavailableReason || "provider_usage_missing" };
  const result = { exact: prior.exact === true && current.exact === true };
  for (const key of ["inputTokens", "outputTokens", "totalTokens", "cacheReadTokens", "cacheWriteTokens"]) {
    const left = prior[key], right = current[key];
    if (Number.isFinite(left) && left >= 0 && Number.isFinite(right) && right >= 0) result[key] = left + right;
    else if (!key.startsWith("cache") && Number.isFinite(left) && left >= 0) result[key] = left;
    else if (!key.startsWith("cache") && Number.isFinite(right) && right >= 0) result[key] = right;
  }
  if (prior.cacheUsageComplete !== undefined || current.cacheUsageComplete !== undefined) {
    result.cacheUsageRequestCount = (prior.cacheUsageRequestCount || 0) + (current.cacheUsageRequestCount || 0);
    result.cacheUsageComplete = prior.cacheUsageComplete === true && current.cacheUsageComplete === true;
  }
  if (!result.exact) result.unavailableReason = prior.unavailableReason || current.unavailableReason || "provider_usage_incomplete";
  return result;
};
const evaluateCompactionBudget = () => globalThis.__compactionHarness.budget || ({ shouldCompact: true, level: "hard-limit", reason: "test", projectedTokens: 120, targetAfterTokens: 100, minimumReductionTokens: 1 });
${implementation}
`;
const compiled = ts.transpileModule(harness, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: "use-context-compaction-controller.ts",
}).outputText;

const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const controllerModule = await import(moduleUrl);

const conversationSource = await readFile(path.join(root, "src/lib/conversation-utils.ts"), "utf8");
const historyStart = conversationSource.indexOf("export function buildChatHistory");
const historyEnd = conversationSource.indexOf("/** Recovery is sent", historyStart);
const historyHarness = `${conversationSource.slice(historyStart, historyEnd)}
const appendAttachmentSummary = (text) => text;
const visibleAgentDialogueText = (response) => String(response?.plan?.reply || response?.plan?.summary || "");
const subAgentAdoptedHistoryText = () => "";
export { buildChatHistory };
`;
const historyCompiled = ts.transpileModule(historyHarness, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: "conversation-utils.ts",
}).outputText;
const { buildChatHistory: actualBuildChatHistory } = await import(
  `data:text/javascript;base64,${Buffer.from(historyCompiled).toString("base64")}`,
);

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
      setError: (message) => { harness.lastError = message; },
      hasUnresolvedRuntimeState: guard,
    });
  };
  globalThis.__compactionHarness = harness;
  return harness;
}

function request() {
  return { chatId: "chat-1", endpoint: "http://test", trigger: "manual", phase: "standalone", provider: "test", model: "test" };
}

test("manual compact of a small conversation does not require saving ten percent of model capacity", async () => {
  const chat = makeChat();
  const harness = makeHarness(chat, async () => undefined);
  harness.budget = { shouldCompact: false, level: "normal", projectedTokens: 12099,
    minimumReductionTokens: 50000, targetAfterTokens: 0 };
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2,
    providerUsage: { exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110 } });
  const outcome = await harness.render(() => false).compactChat({ ...request(), contextLimit: 500000 });
  assert.equal(outcome.status, "applied");
  assert.ok(chat.compaction.afterTokens < chat.compaction.beforeTokens);
  assert.deepEqual(chat.compaction.providerUsage, { exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110 });
  assert.equal(chat.contextUsageCache, undefined);
  assert.deepEqual(chat.items.filter(item => item.type !== "compact").map(item => item.id), ["user-1", "agent-1", "user-2"]);
});

test("manual compact preserves usage on every retained agent item", async () => {
  const firstUsage = { exact: true, inputTokens: 9100, outputTokens: 20, totalTokens: 9120 };
  const secondUsage = { exact: true, inputTokens: 9200, outputTokens: 21, totalTokens: 9221 };
  const chat = makeChat();
  chat.items = [
    { id: "old-user", type: "user", text: "old history ".repeat(80), createdAt: "2026-10-14T02:20:00.000Z" },
    { id: "old-agent", type: "agent", response: "old answer ".repeat(80), timeline: [], createdAt: "2026-10-14T02:20:01.000Z" },
    { id: "current-user", type: "user", text: "keep this turn", createdAt: "2026-10-14T02:23:00.000Z" },
    { id: "agent-one", type: "agent", response: { plan: { reply: "one" }, contextUsage: firstUsage }, timeline: [], createdAt: "2026-10-14T02:23:01.000Z" },
    { id: "agent-two", type: "agent", response: { plan: { reply: "two" }, contextUsage: secondUsage }, timeline: [], createdAt: "2026-10-14T02:23:02.000Z" },
  ];
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 3 });
  const outcome = await harness.render(() => false).compactChat({ ...request(), contextLimit: 500000 });
  assert.equal(outcome.status, "applied");
  assert.deepEqual(chat.items.find(item => item.id === "agent-one").response.contextUsage, firstUsage);
  assert.deepEqual(chat.items.find(item => item.id === "agent-two").response.contextUsage, secondUsage);
  assert.deepEqual(JSON.parse(JSON.stringify(chat.items)).find(item => item.id === "agent-two").response.contextUsage, secondUsage);
  assert.equal(chat.items.find(item => item.type === "compact").contextUsageBoundaryItemId, "agent-two");
});

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
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2,
    providerUsage: { exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110 } });
  const controller = harness.render(() => false);
  const outcome = await controller.compactChat(request());
  assert.equal(outcome.status, "applied");
  assert.equal(chat.sessionId, "");
  assert.deepEqual(chat.items.map((item) => item.id), ["compact-", "user-1", "agent-1", "user-2"].map((id) => id === "compact-" ? chat.items[0].id : id));
  assert.deepEqual(chat.items[0].summarizedItemIds, ["user-1", "agent-1"]);
  assert.equal(chat.items[1].text, "A long user request that should be compacted.");
  assert.equal(chat.items[2].response, "A long assistant response that should be compacted.");
  // A fresh completed turn supplies real material for the second summary;
  // an immediate re-summary of seven characters has no 64-token budget.
  chat.items.push(
    { id: "agent-new", type: "agent", response: "Newly completed details ".repeat(20), timeline: [] },
    { id: "user-new", type: "user", text: "Keep this newest user turn." },
  );
  harness.compactAgentHistoryState.promise = async () => ({ summary: "s", summaryDigest: "digest-2", entryCount: 2,
    providerUsage: { exact: true, inputTokens: 50, outputTokens: 5, totalTokens: 55 } });
  const second = await controller.compactChat(request());
  assert.equal(second.status, "applied");
  assert.deepEqual(chat.compaction.providerUsage, { exact: true, inputTokens: 150, outputTokens: 15, totalTokens: 165 });
  assert.equal(chat.items.filter((item) => item.type === "compact").length, 2);
  assert.equal(chat.items.some((item) => item.type === "user" && item.text === "A long user request that should be compacted."), true);

  const restoredChat = makeChat();
  const failingHarness = makeHarness(restoredChat, async () => { throw new Error("disk unavailable"); });
  failingHarness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2,
    providerUsage: { exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110 } });
  const failing = failingHarness.render(() => false);
  const failed = await failing.compactChat(request());
  assert.equal(failed.status, "failed");
  assert.equal(restoredChat.sessionId, "session-old");
  assert.deepEqual(restoredChat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);
  assert.equal(restoredChat.compaction.failureClass, "persistence");
  assert.deepEqual(restoredChat.compaction.providerUsage, { exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110 });
});

test("empty compaction summary keeps an explicit failure class and original history", async () => {
  const chat = makeChat();
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({ summary: "", summaryDigest: "digest", entryCount: 2,
    providerUsage: { exact: true, inputTokens: 70, outputTokens: 4, totalTokens: 74 } });
  const failed = await harness.render(() => false).compactChat(request());
  assert.equal(failed.status, "failed");
  assert.equal(chat.compaction.failureClass, "empty_summary");
  assert.deepEqual(chat.compaction.providerUsage, { exact: true, inputTokens: 70, outputTokens: 4, totalTokens: 74 });
  assert.equal(chat.sessionId, "session-old");
  assert.deepEqual(chat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);
});

test("missing compact usage stays unknown and does not synthesize zero tokens", async () => {
  const chat = makeChat();
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2,
    providerUsage: { exact: false, unavailableReason: "provider_usage_missing" } });
  const outcome = await harness.render(() => false).compactChat(request());
  assert.equal(outcome.status, "applied");
  assert.deepEqual(chat.compaction.providerUsage, { exact: false, unavailableReason: "provider_usage_missing" });
  assert.equal("inputTokens" in chat.compaction.providerUsage, false);
});

test("API rejection preserves known usage as incomplete instead of claiming exact", async () => {
  const chat = makeChat();
  chat.compaction = {
    generation: "prior-generation",
    status: "applied",
    providerUsage: { exact: true, inputTokens: 80, outputTokens: 8, totalTokens: 88 },
  };
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => { throw new Error("HTTP 503"); };
  const outcome = await harness.render(() => false).compactChat(request());
  assert.equal(outcome.status, "failed");
  assert.equal(chat.compaction.providerUsage.exact, false);
  assert.equal(chat.compaction.providerUsage.inputTokens, 80);
  assert.equal(chat.compaction.providerUsage.outputTokens, 8);
  assert.equal(chat.compaction.providerUsage.totalTokens, 88);
  assert.equal(chat.compaction.providerUsage.unavailableReason, "provider_usage_missing");
});

test("legacy compact state without a receipt remains unknown when a later compact is measured", async () => {
  const chat = makeChat();
  chat.compaction = { generation: "legacy-073", status: "applied" };
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2,
    providerUsage: { exact: true, inputTokens: 80, outputTokens: 8, totalTokens: 88 } });
  const outcome = await harness.render(() => false).compactChat(request());
  assert.equal(outcome.status, "applied");
  assert.equal(chat.compaction.providerUsage.exact, false);
  assert.equal(chat.compaction.providerUsage.inputTokens, 80);
  assert.equal(chat.compaction.providerUsage.unavailableReason, "provider_usage_missing");
});

test("reduction rejection keeps an explicit stage, token evidence, and original history", async () => {
  const chat = makeChat();
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({
    summary: "x".repeat(200),
    summaryDigest: "digest",
    entryCount: 2,
  });
  const failed = await harness.render(() => false).compactChat(request());
  assert.equal(failed.status, "failed");
  assert.equal(chat.compaction.failureClass, "reduction_gate");
  assert.equal(typeof chat.compaction.beforeTokens, "number");
  assert.equal(typeof chat.compaction.afterTokens, "number");
  assert.ok(chat.compaction.afterTokens > chat.compaction.beforeTokens);
  assert.equal(chat.sessionId, "session-old");
  assert.deepEqual(chat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);
});

test("actual conversation projection survives JSON reload and second compact without replaying old dialogue", async () => {
  const chat = makeChat();
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => ({ summary: "summary", summaryDigest: "digest", entryCount: 2 });
  const controller = harness.render(() => false);
  assert.equal((await controller.compactChat(request())).status, "applied");
  const firstRoundTrip = JSON.parse(JSON.stringify(chat.items));
  assert.equal(firstRoundTrip.filter((item) => item.type === "user").length, 2);
  assert.deepEqual(actualBuildChatHistory(firstRoundTrip, (value) => value).map((entry) => entry.text), [
    "summary", "Keep this newest user turn.",
  ]);
  // A fresh completed turn supplies real material for the second summary;
  // an immediate re-summary of seven characters has no 64-token budget.
  chat.items.push(
    { id: "agent-new", type: "agent", response: "Newly completed details ".repeat(20), timeline: [] },
    { id: "user-new", type: "user", text: "Keep this newest user turn." },
  );
  harness.compactAgentHistoryState.promise = async () => ({ summary: "s", summaryDigest: "digest-2", entryCount: 2 });
  assert.equal((await controller.compactChat(request())).status, "applied");
  const secondRoundTrip = JSON.parse(JSON.stringify(chat.items));
  assert.equal(secondRoundTrip.filter((item) => item.type === "user" && item.text.includes("long user request")).length, 1);
  assert.deepEqual(actualBuildChatHistory(secondRoundTrip, (value) => value).map((entry) => entry.text), [
    "s", "Keep this newest user turn.",
  ]);
});

test("late abort after compactor response prevents the commit", async () => {
  const chat = makeChat();
  const abort = new AbortController();
  const harness = makeHarness(chat, async () => undefined);
  harness.compactAgentHistoryState.promise = async () => {
    abort.abort();
    return { summary: "summary", summaryDigest: "digest", entryCount: 2 };
  };
  const controller = harness.render(() => false);
  const outcome = await controller.compactChat({ ...request(), signal: abort.signal });
  assert.equal(outcome.status, "cancelled");
  assert.equal(chat.sessionId, "session-old");
  assert.deepEqual(chat.items.map((item) => item.id), ["user-1", "agent-1", "user-2"]);
});
