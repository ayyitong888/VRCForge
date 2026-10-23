import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = path.join(root, "src/lib/context-compaction.ts");
const source = await readFile(sourcePath, "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: sourcePath,
}).outputText;
const policy = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`);
const stateSourcePath = path.join(root, "src/lib/chat-compaction-state.ts");
const stateSource = await readFile(stateSourcePath, "utf8");
const stateTranspiled = ts.transpileModule(stateSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: stateSourcePath,
}).outputText;
const statePolicy = await import(`data:text/javascript;base64,${Buffer.from(stateTranspiled).toString("base64")}`);

const boundaryStart = source.indexOf("export function contextUsageBoundaryItemId");
const usageStart = source.indexOf("export function latestContextWindowUsage");
const usageEnd = source.indexOf("export function projectRuntimeCompactionItems", usageStart);
const usageHarness = boundaryStart >= 0 && usageStart >= 0
  ? `${source.slice(boundaryStart, usageEnd)}
export { contextUsageBoundaryItemId, latestContextWindowUsage };
`
  : `function contextUsageBoundaryItemId() { throw new Error("RED: contextUsageBoundaryItemId is not implemented"); }
function latestContextWindowUsage() { throw new Error("RED: latestContextWindowUsage is not implemented"); }
export { contextUsageBoundaryItemId, latestContextWindowUsage };
`;
const usageCompiled = ts.transpileModule(usageHarness, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: "conversation-utils.ts",
}).outputText;
const conversationPolicy = await import(`data:text/javascript;base64,${Buffer.from(usageCompiled).toString("base64")}`);

const knownWindow = (limit = 1000) => ({ limit, known: true, source: "provider" });
const decisionAt = (tokens, options = {}) => policy.evaluateCompactionBudget({
  usage: { exact: true, peakInputTokens: tokens },
  contextLimit: knownWindow(),
  ...options,
});

test("roadmap ratios are 75/85/90/95 and boundary equality is inclusive", () => {
  assert.equal(policy.CONTEXT_COMPACTION_PREFIRE_RATIO, 0.75);
  assert.equal(policy.CONTEXT_AUTO_COMPACT_RATIO, 0.85);
  assert.equal(policy.CONTEXT_COMPACTION_MAX_TRIGGER_RATIO, 0.9);
  assert.equal(policy.CONTEXT_COMPACTION_HARD_LIMIT_RATIO, 0.95);

  assert.equal(decisionAt(749).level, "below");
  assert.equal(decisionAt(749).shouldCompact, false);
  assert.equal(decisionAt(750).level, "prefire");
  assert.equal(decisionAt(750).shouldCompact, false);
  assert.equal(decisionAt(849).level, "prefire");
  assert.equal(decisionAt(850).level, "compact");
  assert.equal(decisionAt(850).shouldCompact, true);
  assert.equal(decisionAt(851).level, "compact");
  assert.equal(decisionAt(950).level, "hard-limit");
  assert.equal(decisionAt(950).shouldCompact, true);
  assert.equal(decisionAt(951).level, "hard-limit");

  const configuredAtNinety = decisionAt(900, { policy: { triggerRatio: 0.9 } });
  assert.equal(configuredAtNinety.level, "compact");
  assert.equal(configuredAtNinety.shouldCompact, true);
  assert.equal(decisionAt(899, { policy: { triggerRatio: 0.9 } }).level, "prefire");
  assert.equal(decisionAt(901, { policy: { triggerRatio: 0.9 } }).level, "compact");
  assert.equal(policy.resolveContextCompactionPolicy({ triggerRatio: 0.99 }).triggerRatio, 0.9);
  assert.equal(policy.resolveContextCompactionPolicy({ triggerRatio: 0.8 }).triggerRatio, 0.8);
});

test("unknown windows and missing exact occupancy never auto-trigger", () => {
  const unknown = policy.evaluateCompactionBudget({
    usage: { exact: true, peakInputTokens: 200000 },
    contextLimit: { limit: 128000, known: false, source: "unknown" },
    incomingText: "still must not guess",
  });
  assert.equal(unknown.reason, "unknown_context_limit");
  assert.equal(unknown.contextLimitKnown, false);
  assert.equal(unknown.shouldCompact, false);

  const unavailable = policy.evaluateCompactionBudget({
    usage: { exact: false, peakInputTokens: 900 },
    contextLimit: knownWindow(),
  });
  assert.equal(unavailable.reason, "usage_unavailable");
  assert.equal(unavailable.shouldCompact, false);
  const legacyWithoutExactProof = policy.evaluateCompactionBudget({
    usage: { peakInputTokens: 900 },
    contextLimit: knownWindow(),
  });
  assert.equal(legacyWithoutExactProof.reason, "usage_unavailable");
  assert.equal(legacyWithoutExactProof.shouldCompact, false);
  const cumulativeOnly = policy.evaluateCompactionBudget({
    usage: { exact: true, cumulativeInputTokens: 900 },
    contextLimit: knownWindow(),
  });
  assert.equal(cumulativeOnly.reason, "usage_unavailable");
  assert.equal(cumulativeOnly.shouldCompact, false);
  assert.deepEqual(policy.resolveContextLimit("unlisted", "unknown-model"), {
    limit: 0,
    known: false,
    source: "unknown",
  });
});

test("provider metadata resolves a real limit and occupancy prefers peak then last then legacy", () => {
  assert.deepEqual(policy.resolveContextLimit("custom", "model", { maxInputTokens: 64000 }), {
    limit: 64000,
    known: true,
    source: "provider",
  });

  const peak = policy.evaluateCompactionBudget({
    usage: { exact: true, peakInputTokens: 860, lastInputTokens: 400, inputTokens: 999 },
    contextLimit: knownWindow(),
  });
  assert.equal(peak.inputTokenSource, "peak");
  assert.equal(peak.observedInputTokens, 860);

  const last = policy.evaluateCompactionBudget({
    usage: { exact: true, lastInputTokens: 860, inputTokens: 999 },
    contextLimit: knownWindow(),
  });
  assert.equal(last.inputTokenSource, "last");
  assert.equal(last.observedInputTokens, 860);

  const legacy = policy.evaluateCompactionBudget({
    usage: { exact: true, inputTokens: 860 },
    contextLimit: knownWindow(),
  });
  assert.equal(legacy.inputTokenSource, "legacy");
});

test("a user context cap can shrink but never expand the provider window", () => {
  assert.deepEqual(
    policy.resolveContextLimit("custom", "model", { contextWindow: 1_000_000 }, 128_000),
    { limit: 128_000, known: true, source: "user" },
  );
  assert.deepEqual(
    policy.resolveContextLimit("custom", "model", { contextWindow: 128_000 }, 1_000_000),
    { limit: 128_000, known: true, source: "provider" },
  );
  assert.deepEqual(
    policy.resolveContextLimit("deepseek", "deepseek-v4-pro", undefined, 96_000),
    { limit: 96_000, known: true, source: "user" },
  );
  for (const model of ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-auto"]) {
    assert.deepEqual(
      policy.resolveContextLimit("deepseek", model),
      { limit: 1_000_000, known: true, source: "known" },
      model,
    );
  }
  assert.deepEqual(
    policy.resolveContextLimit(
      "deepseek",
      "deepseek-v4-flash",
      { contextWindow: 128_000, modelContextWindow: 1_000_000 },
    ),
    { limit: 1_000_000, known: true, source: "provider" },
  );
  assert.deepEqual(
    policy.resolveContextLimit("unlisted", "unknown-model", undefined, 64_000),
    { limit: 64_000, known: true, source: "user" },
  );
});

test("usage from another provider or model is never reused after a model switch", () => {
  const usage = {
    exact: true,
    provider: "provider-a",
    model: "large-model",
    peakInputTokens: 900,
  };
  assert.equal(policy.contextUsageMatchesModel(usage, "provider-a", "large-model"), true);
  assert.equal(policy.contextUsageMatchesModel(usage, "provider-a", "small-model"), false);
  assert.equal(policy.contextUsageMatchesModel(usage, "provider-b", "large-model"), false);
  assert.equal(policy.contextUsageMatchesModel({ exact: true, peakInputTokens: 900 }, "provider-a", "large-model"), false);
  const downshift = policy.evaluateCompactionBudget({
    usage: policy.contextUsageMatchesModel(usage, "provider-a", "small-model") ? usage : undefined,
    contextLimit: knownWindow(1000),
  });
  assert.equal(downshift.reason, "usage_unavailable");
  assert.equal(downshift.shouldCompact, false);
});

test("only incoming unseen text and attachment deltas are added, with conservative CJK cost", () => {
  const cjk = "你好世界";
  assert.equal(policy.estimateTextTokens(cjk), 4);
  assert.ok(policy.estimateTextTokens(cjk) > Buffer.byteLength(cjk, "utf8") / 4);
  assert.equal(policy.estimateTextTokens("abcd"), 1);

  const incomingTokens = policy.estimateIncomingContextTokens("你好", [
    { name: "note.txt", type: "text/plain", payloadKind: "text", text: "世界" },
  ]);
  const evaluated = policy.evaluateCompactionBudget({
    usage: { exact: true, peakInputTokens: 800 },
    contextLimit: knownWindow(),
    incomingText: "你好",
    incomingAttachments: [
      { name: "note.txt", type: "text/plain", payloadKind: "text", text: "世界" },
    ],
  });
  assert.equal(evaluated.observedInputTokens, 800);
  assert.equal(evaluated.incomingTokens, incomingTokens);
  assert.equal(evaluated.projectedTokens, 800 + incomingTokens);
  assert.ok(incomingTokens > 4, "attachment metadata and text must contribute to the unseen delta");
});

test("source fingerprints are synchronous, stable, order-sensitive, and attachment-aware", () => {
  const history = [
    { role: "user", text: "goal", attachments: [{ name: "a.txt", size: 4, contentDigest: "one" }] },
    { role: "agent", text: "done" },
  ];
  const first = policy.fingerprintCompactionSource(history);
  const second = policy.fingerprintCompactionSource(structuredClone(history));
  assert.equal(first, second);
  assert.match(first, /^ctx1-/);
  assert.notEqual(first, policy.fingerprintCompactionSource([...history].reverse()));
  assert.notEqual(first, policy.fingerprintCompactionSource([
    { ...history[0], text: "goal changed" },
    history[1],
  ]));
  assert.notEqual(first, policy.fingerprintCompactionSource([
    { ...history[0], attachments: [{ name: "a.txt", size: 4, contentDigest: "two" }] },
    history[1],
  ]));
});

test("same source in the same window is suppressed and reduction requirements scale by window", () => {
  const sourceDigest = policy.fingerprintCompactionSource([{ role: "user", text: "same" }]);
  const duplicate = policy.evaluateCompactionBudget({
    usage: { exact: true, peakInputTokens: 9000 },
    contextLimit: knownWindow(10000),
    sourceDigest,
    previousCompaction: {
      sourceDigest,
      contextLimit: 10000,
      beforeTokens: 9000,
      afterTokens: 4000,
      minimumReductionTokens: 1024,
      status: "applied",
    },
  });
  assert.equal(duplicate.level, "compact");
  assert.equal(duplicate.reason, "duplicate_source");
  assert.equal(duplicate.shouldCompact, false);
  assert.equal(duplicate.previousReductionSufficient, true);
  assert.equal(duplicate.minimumReductionTokens, 1024);

  const prefireCanAdvance = policy.evaluateCompactionBudget({
    usage: { exact: true, peakInputTokens: 9000 },
    contextLimit: knownWindow(10000),
    sourceDigest,
    previousCompaction: { sourceDigest, contextLimit: 10000, status: "prefire" },
  });
  assert.equal(prefireCanAdvance.duplicateSource, false);
  assert.equal(prefireCanAdvance.shouldCompact, true);

  const largerWindow = policy.evaluateCompactionBudget({
    usage: { exact: true, peakInputTokens: 90000 },
    contextLimit: knownWindow(100000),
    sourceDigest,
    previousCompaction: { sourceDigest, contextLimit: 10000, status: "applied" },
  });
  assert.equal(largerWindow.duplicateSource, false);
  assert.equal(largerWindow.shouldCompact, true);
  assert.equal(largerWindow.minimumReductionTokens, 10000);
  assert.equal(largerWindow.targetAfterTokens, 50000);
});

test("bounded fallback keeps the primary goal and newest complete turns without slicing entries", () => {
  const primary = `PRIMARY_GOAL_${"g".repeat(40)}`;
  const recentUser = "RECENT_USER_REQUEST";
  const recentAgent = `RECENT_AGENT_RESULT_${"r".repeat(30)}`;
  const incomplete = "INCOMPLETE_LATEST_USER_MUST_NOT_APPEAR";
  const history = [
    { role: "user", text: primary },
    { role: "agent", text: "first result" },
    { role: "user", text: "middle request" },
    { role: "agent", text: "middle result" },
    { role: "user", text: recentUser },
    { role: "agent", text: recentAgent },
    { role: "user", text: incomplete },
  ];
  const fallback = policy.buildBoundedCompactionFallback(history, {
    maxEntries: 3,
    targetCharacters: 220,
  });

  assert.equal(fallback.fidelity, "fallback");
  assert.deepEqual(fallback.entries.map((entry) => entry.text), [primary, recentUser, recentAgent]);
  assert.ok(fallback.text.includes(primary));
  assert.ok(fallback.text.includes(recentUser));
  assert.ok(fallback.text.includes(recentAgent));
  assert.ok(!fallback.text.includes("middle request"));
  assert.ok(!fallback.text.includes(incomplete));
  assert.ok(!fallback.text.includes("\u2026"));
  for (const entry of fallback.entries) {
    assert.ok(history.some((original) => original.role === entry.role && original.text === entry.text));
  }

  const noPartialTurn = policy.buildBoundedCompactionFallback(history, {
    maxEntries: 2,
    targetCharacters: 220,
  });
  assert.deepEqual(noPartialTurn.entries.map((entry) => entry.text), [primary, "first result"]);
  assert.ok(!noPartialTurn.text.includes(recentUser));

  const indivisiblePrimary = policy.buildBoundedCompactionFallback(
    [{ role: "user", text: primary }],
    { targetCharacters: 20 },
  );
  assert.equal(indivisiblePrimary.entries[0].text, primary);
  assert.equal(indivisiblePrimary.overTarget, true);
});

test("mid-turn projection marks summarized dialogue and preserves the full durable order", () => {
  const items = [
    { id: "old-user", type: "user", text: "old request" },
    { id: "old-answer", type: "question_answer", questionId: "q-1", header: "Review", text: "old answer" },
    { id: "task", type: "subagent", task: { id: "task", status: "running" } },
    { id: "old-agent", type: "agent", response: { plan: { reply: "old answer" } } },
    { id: "approval", type: "result", approvalId: "approval-1" },
    { id: "current-user", type: "user", text: "continue" },
  ];
  const compactItem = {
    id: "compact-window",
    type: "compact",
    text: "Context compressed",
    detail: "safe successor summary",
    status: "completed",
  };
  const projected = policy.projectRuntimeCompactionItems(
    items,
    new Set(["old-user", "old-answer", "old-agent"]),
    compactItem,
  );
  assert.equal(projected.replacedCount, 3);
  assert.deepEqual(projected.items.map((item) => item.id), [
    "compact-window",
    "old-user",
    "old-answer",
    "task",
    "old-agent",
    "approval",
    "current-user",
  ]);
  assert.equal(projected.items[0].detail, "safe successor summary");
  assert.deepEqual(projected.items[0].summarizedItemIds, ["old-user", "old-answer", "old-agent"]);

  const stale = policy.projectRuntimeCompactionItems(items, new Set(["missing"]), compactItem);
  assert.equal(stale.replacedCount, 0);
  assert.deepEqual(stale.items, items);
});

test("durable compaction state carries only bounded ownership references", () => {
  const rawPayload = `secret=${"s".repeat(200)} C:\\Users\\someone\\private.txt`;
  const entries = policy.buildDurableCompactionStateEntries([
    {
      id: "queued-item",
      type: "user",
      text: "queued content",
      queuedFrom: true,
    },
    {
      id: "subagent-card",
      type: "subagent",
      task: {
        id: "task-42",
        status: "completed",
        handoffStatus: "materialized",
        result: { rawPayload },
      },
    },
    {
      id: "approval-card",
      type: "result",
      approvalId: "approval-42",
      result: { ok: true, stdout: rawPayload },
    },
    {
      id: "agent-card",
      type: "agent",
      response: {
        approvalId: "approval-43",
        goalDeliveryId: "goal-delivery-9",
        plan: {},
        write: { result: { checkpointId: "checkpoint-42", rawPayload } },
      },
    },
    {
      id: "unsafe-approval-card",
      type: "result",
      approvalId: "C:\\Users\\someone\\private.txt",
    },
    {
      id: "unsafe-avatar-card",
      type: "result",
      approvalId: "avtr_01234567-89ab-cdef-0123-456789abcdef",
    },
  ]);
  const serialized = JSON.stringify(entries);
  assert.equal(entries.length, 6);
  assert.ok(serialized.includes("task-42"));
  assert.ok(serialized.includes("approval-42"));
  assert.ok(serialized.includes("approval-43"));
  assert.ok(serialized.includes("checkpoint-42"));
  assert.ok(serialized.includes("goal-delivery-9"));
  assert.ok(serialized.includes("status=materialized"));
  assert.ok(!serialized.includes(rawPayload));
  assert.ok(!serialized.includes("someone"));
  assert.ok(!serialized.includes("avtr_"));

  const bounded = policy.buildDurableCompactionStateEntries(Array.from({ length: 40 }, (_, index) => ({
    id: `result-${index}`,
    type: "result",
    approvalId: `approval-${index}`,
  })));
  assert.equal(bounded.length, 24);
  assert.ok(bounded[0].text.includes("approval-16"));
  assert.ok(bounded[23].text.includes("approval-39"));
});

test("current occupancy excludes retained pre-compaction usage and accepts a post-boundary receipt", () => {
  const oldUsage = { exact: true, inputTokens: 9500, totalTokens: 9500 };
  const newUsage = { exact: true, inputTokens: 1200, totalTokens: 1200 };
  const items = [
    { id: "compact-1", type: "compact", text: "compressed", createdAt: "2026-10-14T02:24:00.000Z", contextUsageBoundaryItemId: "retained-agent", summarizedItemIds: [] },
    { id: "retained-agent", type: "agent", createdAt: "2026-10-14T02:23:59.000Z", response: { contextUsage: oldUsage } },
  ];
  assert.equal(conversationPolicy.latestContextWindowUsage(items), undefined,
    "a retained pre-compaction receipt must not become current occupancy");
  items.push({ id: "new-agent", type: "agent", createdAt: "2026-10-14T02:24:01.000Z", response: { contextUsage: newUsage } });
  assert.deepEqual(conversationPolicy.latestContextWindowUsage(items), newUsage);
});

test("explicit boundary ids work without item timestamps", () => {
  const newUsage = { exact: true, inputTokens: 1200 };
  const items = [
    { id: "compact-1", type: "compact", text: "compressed", contextUsageBoundaryItemId: "retained-agent", summarizedItemIds: [] },
    { id: "retained-agent", type: "agent", response: { contextUsage: { exact: true, inputTokens: 9500 } } },
    { id: "new-agent", type: "agent", response: { contextUsage: newUsage } },
  ];
  assert.deepEqual(conversationPolicy.latestContextWindowUsage(items), newUsage);
});

test("clock order and same-millisecond markers do not override boundary position", () => {
  const oldUsage = { exact: true, inputTokens: 9000 };
  const newUsage = { exact: true, inputTokens: 1200 };
  const items = [
    { id: "new-marker", type: "compact", text: "new", createdAt: "2026-10-14T02:25:00.000Z", contextUsageBoundaryItemId: "new-agent", summarizedItemIds: [] },
    { id: "new-agent", type: "agent", createdAt: "2026-10-14T02:24:00.000Z", response: { contextUsage: newUsage } },
    { id: "old-marker", type: "compact", text: "old", createdAt: "2026-10-14T02:24:00.000Z", contextUsageBoundaryItemId: "old-agent", summarizedItemIds: [] },
    { id: "old-agent", type: "agent", createdAt: "2026-10-14T02:23:59.000Z", response: { contextUsage: oldUsage } },
  ];
  assert.deepEqual(conversationPolicy.latestContextWindowUsage(items), undefined,
    "max boundary position wins even when marker clocks are reversed");
  items.push({ id: "after-new-marker", type: "agent", createdAt: "2026-10-14T02:24:00.001Z", response: { contextUsage: newUsage } });
  assert.deepEqual(conversationPolicy.latestContextWindowUsage(items), newUsage);
});

test("a superseded legacy marker without a boundary does not block a newer generation", () => {
  const usage = { exact: true, inputTokens: 1200 };
  const items = [
    { id: "new-marker", type: "compact", text: "new", contextUsageBoundaryItemId: "old-agent", summarizedItemIds: ["old-marker"] },
    { id: "old-marker", type: "compact", text: "legacy", summarizedItemIds: [] },
    { id: "old-agent", type: "agent", response: { contextUsage: { exact: true, inputTokens: 9000 } } },
    { id: "new-agent", type: "agent", response: { contextUsage: usage } },
  ];
  assert.deepEqual(conversationPolicy.latestContextWindowUsage(items), usage);
});

test("no agent produces an explicit empty boundary and no occupancy", () => {
  const items = [{ id: "user", type: "user", text: "no agent" }];
  assert.equal(conversationPolicy.contextUsageBoundaryItemId(items), "");
  assert.equal(conversationPolicy.latestContextWindowUsage([
    { id: "compact", type: "compact", text: "empty", createdAt: "2026-10-14T02:24:00.000Z", contextUsageBoundaryItemId: "", summarizedItemIds: [] },
    ...items,
  ]), undefined);
});

test("revision CAS rejects stale summaries and restart recovery keeps the original items", () => {
  const chat = {
    id: "chat-1",
    sessionId: "session-1",
    title: "title",
    projectPath: "",
    revision: 4,
    items: [{ id: "original", type: "user", text: "must survive" }],
  };
  const stale = statePolicy.applyRevisionedChatUpdate(chat, 3, (current) => ({ ...current, title: "stale" }));
  assert.equal(stale.applied, false);
  assert.equal(stale.chat, chat);

  const applied = statePolicy.applyRevisionedChatUpdate(chat, 4, (current) => ({ ...current, title: "fresh" }));
  assert.equal(applied.applied, true);
  assert.equal(applied.chat.revision, 5);
  assert.equal(applied.chat.title, "fresh");
  assert.deepEqual(applied.chat.items, chat.items);

  const restored = statePolicy.normalizeRestoredCompaction({
    generation: "compact-1",
    status: "compacting",
    sourceDigest: "source",
  }, "2026-07-18T00:00:00.000Z");
  assert.equal(restored.status, "failed");
  assert.equal(restored.failureClass, "interrupted");
  assert.equal(restored.completedAt, "2026-07-18T00:00:00.000Z");
  assert.equal(statePolicy.restoredCompactionRequiresPersistence({ status: "compacting" }), true);
  assert.equal(statePolicy.restoredCompactionRequiresPersistence({ status: "prefire" }), true);
  assert.equal(statePolicy.restoredCompactionRequiresPersistence({ status: "ready" }), true);
  assert.equal(statePolicy.restoredCompactionRequiresPersistence({ status: "applied" }), false);
  assert.equal(statePolicy.normalizeRestoredCompaction({ generation: "bad", status: "unknown" }), undefined);
});

test("compaction telemetry is bounded and restart normalization rejects unbounded values", () => {
  assert.equal(
    statePolicy.boundedCompactionLatencyMs(
      "2026-07-18T00:00:00.000Z",
      "2026-07-18T00:00:00.750Z",
    ),
    750,
  );
  assert.equal(
    statePolicy.boundedCompactionLatencyMs(
      "2026-07-16T00:00:00.000Z",
      "2026-07-18T00:00:00.000Z",
    ),
    24 * 60 * 60 * 1000,
  );
  assert.equal(statePolicy.boundedCompactionLatencyMs("bad", "also-bad"), undefined);
  assert.equal(statePolicy.boundedCompactionAttempts(500), 16);
  assert.equal(statePolicy.boundedCompactionAttempts(-1), undefined);
  assert.equal(statePolicy.boundedCompactionSummaryCharacters(500_000), 100_000);

  const restored = statePolicy.normalizeRestoredCompaction({
    generation: "compact-telemetry",
    status: "suppressed",
    attempts: 999,
    latencyMs: 999_999_999,
    retainedSummaryCharacters: 999_999,
    prefireOutcome: "invalid",
    suppressionReason: `size_${"x".repeat(200)}`,
  });
  assert.equal(restored.attempts, 16);
  assert.equal(restored.latencyMs, 24 * 60 * 60 * 1000);
  assert.equal(restored.retainedSummaryCharacters, 100_000);
  assert.equal(restored.prefireOutcome, undefined);
  assert.equal(restored.suppressionReason.length, 80);
  assert.equal("summary" in restored, false);
});

test("compaction provider usage sums operations once and preserves unknown without zero fill", () => {
  const first = statePolicy.mergeCompactionProviderUsage(undefined, {
    exact: true, inputTokens: 100, outputTokens: 10, totalTokens: 110,
    cacheReadTokens: 40, cacheWriteTokens: 5, cacheUsageComplete: true, cacheUsageRequestCount: 1,
  });
  const second = statePolicy.mergeCompactionProviderUsage(first, {
    exact: true, inputTokens: 50, outputTokens: 5, totalTokens: 55,
    cacheReadTokens: 20, cacheWriteTokens: 3, cacheUsageComplete: true, cacheUsageRequestCount: 1,
  });
  assert.deepEqual(second, {
    exact: true, inputTokens: 150, outputTokens: 15, totalTokens: 165,
    cacheReadTokens: 60, cacheWriteTokens: 8, cacheUsageComplete: true, cacheUsageRequestCount: 2,
  });
  const unknown = statePolicy.mergeCompactionProviderUsage(second, {
    exact: false, unavailableReason: "provider_usage_missing",
  });
  assert.equal(unknown.exact, false);
  assert.equal(unknown.inputTokens, 150);
  assert.equal(unknown.outputTokens, 15);
  assert.equal(unknown.totalTokens, 165);
  assert.equal(unknown.unavailableReason, "provider_usage_missing");
  const partialCache = statePolicy.mergeCompactionProviderUsage(second, {
    exact: true, inputTokens: 20, outputTokens: 2, totalTokens: 22,
    cacheWriteTokens: 4, cacheUsageComplete: false, cacheUsageRequestCount: 1,
  });
  assert.equal(partialCache.cacheReadTokens, 60);
  assert.equal(partialCache.cacheWriteTokens, 12);
  assert.equal(partialCache.cacheUsageComplete, false);
  const empty = statePolicy.mergeCompactionProviderUsage(undefined, {
    exact: false, unavailableReason: "provider_usage_missing",
  });
  assert.deepEqual(empty, { exact: false, unavailableReason: "provider_usage_missing" });
});

test("compact protocol coverage survives a known generation plus an unknown receipt", () => {
  const known = {
    schema: "vrcforge.provider_usage.v1", source: "compaction", exact: true, requestCount: 1,
    inputTokens: 100, outputTokens: 10, totalTokens: 110, cacheReadTokens: 40, cacheWriteTokens: 5,
    coverage: {
      inputTokens: { requestCount: 1, reportedRequestCount: 1, complete: true },
      outputTokens: { requestCount: 1, reportedRequestCount: 1, complete: true },
      totalTokens: { requestCount: 1, reportedRequestCount: 1, complete: true },
      cacheReadTokens: { requestCount: 1, reportedRequestCount: 1, complete: true },
      cacheWriteTokens: { requestCount: 1, reportedRequestCount: 1, complete: true },
    },
    cacheCoverage: {
      requestCount: 1,
      read: { requestCount: 1, reportedRequestCount: 1, complete: true },
      write: { requestCount: 1, reportedRequestCount: 1, complete: true },
    },
  };
  const unknown = {
    schema: "vrcforge.provider_usage.v1", source: "compaction", exact: false, requestCount: 1,
    inputTokens: 20, outputTokens: 2, totalTokens: 22, cacheWriteTokens: 3,
    partial: true, unavailableReason: "provider_usage_missing",
    coverage: {
      inputTokens: { requestCount: 1, reportedRequestCount: 0, complete: false },
      outputTokens: { requestCount: 1, reportedRequestCount: 0, complete: false },
      totalTokens: { requestCount: 1, reportedRequestCount: 0, complete: false },
      cacheReadTokens: { requestCount: 1, reportedRequestCount: 0, complete: false },
      cacheWriteTokens: { requestCount: 1, reportedRequestCount: 0, complete: false },
    },
    cacheCoverage: {
      requestCount: 1,
      read: { requestCount: 1, reportedRequestCount: 0, complete: false },
      write: { requestCount: 1, reportedRequestCount: 0, complete: false },
    },
  };
  const merged = statePolicy.mergeCompactionProviderUsage(known, unknown);
  assert.equal(merged.requestCount, 2);
  assert.equal(merged.inputTokens, 120);
  assert.equal(merged.cacheReadTokens, 40);
  assert.equal(merged.cacheWriteTokens, 8);
  assert.equal(merged.exact, false);
  assert.equal(merged.partial, true);
  assert.deepEqual(merged.coverage.inputTokens, { requestCount: 2, reportedRequestCount: 1, complete: false });
  assert.deepEqual(merged.cacheCoverage.read, { requestCount: 2, reportedRequestCount: 1, complete: false });
  const legacyUnknown = statePolicy.mergeCompactionProviderUsage(known, undefined);
  assert.equal(legacyUnknown.partial, true);
  assert.equal(legacyUnknown.coverage.inputTokens.complete, false);
  assert.equal(legacyUnknown.cacheCoverage.read.complete, false);
});

test("conversation meter delegates occupancy and threshold semantics to the pure policy", async () => {
  const conversationSource = await readFile(path.join(root, "src/lib/conversation-utils.ts"), "utf8");
  assert.ok(conversationSource.includes("resolveContextInputTokens(usage)"));
  assert.ok(conversationSource.includes("resolveContextLimit(provider, model, modelInfo, userContextWindow)"));
  assert.ok(conversationSource.includes("ratio >= CONTEXT_AUTO_COMPACT_RATIO"));
  assert.ok(!conversationSource.includes("const CONTEXT_AUTO_COMPACT_RATIO = 0.92"));
});
