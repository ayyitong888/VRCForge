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

test("mid-turn projection replaces only the summarized dialogue and preserves live ownership state", () => {
  const items = [
    { id: "old-user", type: "user", text: "old request" },
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
    new Set(["old-user", "old-agent"]),
    compactItem,
  );
  assert.equal(projected.replacedCount, 2);
  assert.deepEqual(projected.items.map((item) => item.id), [
    "compact-window",
    "task",
    "approval",
    "current-user",
  ]);
  assert.equal(projected.items[0].detail, "safe successor summary");

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

test("a replaced context window cannot reuse the old high-water usage sample", () => {
  const items = [
    { id: "user", type: "user", text: "latest request" },
    {
      id: "agent",
      type: "agent",
      response: {
        plan: { reply: "latest reply" },
        contextUsage: { exact: true, peakInputTokens: 9500, inputTokens: 9500 },
      },
    },
  ];
  const invalidated = policy.invalidateCompactedWindowUsage(items);
  assert.equal(invalidated[0], items[0]);
  assert.equal(invalidated[1].response.contextUsage, undefined);
  assert.equal(invalidated[1].response.plan.reply, "latest reply");
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

test("conversation meter delegates occupancy and threshold semantics to the pure policy", async () => {
  const conversationSource = await readFile(path.join(root, "src/lib/conversation-utils.ts"), "utf8");
  assert.ok(conversationSource.includes("resolveContextInputTokens(usage)"));
  assert.ok(conversationSource.includes("resolveContextLimit(provider, model, modelInfo)"));
  assert.ok(conversationSource.includes("ratio >= CONTEXT_AUTO_COMPACT_RATIO"));
  assert.ok(!conversationSource.includes("const CONTEXT_AUTO_COMPACT_RATIO = 0.92"));
});
