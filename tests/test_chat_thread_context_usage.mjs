import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = await readFile(path.join(root, "src/lib/chat-thread.ts"), "utf8");
const cacheStart = source.indexOf("export function cacheChatContextUsageFast");
const filterStart = source.indexOf("export function filterPersistableChats", cacheStart);
const normalizeStart = source.indexOf("export function normalizeChatContextUsage");
const exactStart = source.indexOf("function latestExactAgentContextUsage", normalizeStart);
const unstartedStart = source.indexOf("function isUnstartedChat", exactStart);
const compactionSource = await readFile(path.join(root, "src/lib/context-compaction.ts"), "utf8");
const windowStart = compactionSource.indexOf("export function latestContextWindowUsage");
const windowEnd = compactionSource.indexOf("export function projectRuntimeCompactionItems", windowStart);
const windowImplementation = windowStart >= 0
  ? compactionSource.slice(windowStart, windowEnd).replace("export function", "function")
  : "function latestContextWindowUsage() { throw new Error(\"RED: latestContextWindowUsage is not implemented\"); }";
const harness = `${windowImplementation}
${source.slice(cacheStart, filterStart)}
export { cacheChatContextUsageFast };
`;
const compiled = ts.transpileModule(harness, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: "chat-thread.ts",
}).outputText;
const module = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);

test("context usage cache does not revive a pre-compaction receipt", () => {
  const usage = { exact: true, inputTokens: 9500, totalTokens: 9500 };
  const chat = {
    id: "chat-1",
    contextUsageCache: usage,
    items: [
      { id: "compact-1", type: "compact", text: "compressed", createdAt: "2026-10-14T02:24:00.000Z", contextUsageBoundaryItemId: "retained-agent", summarizedItemIds: [] },
      { id: "retained-agent", type: "agent", createdAt: "2026-10-14T02:23:59.000Z", response: { contextUsage: usage } },
    ],
  };
  const cached = module.cacheChatContextUsageFast(chat);
  assert.equal(cached.contextUsageCache, undefined);
  assert.deepEqual(cached.items[1].response.contextUsage, usage);
});

test("a post-compaction receipt becomes the derived cache", () => {
  const oldUsage = { exact: true, inputTokens: 9500, totalTokens: 9500 };
  const newUsage = { exact: true, inputTokens: 1200, totalTokens: 1200 };
  const chat = {
    id: "chat-1",
    contextUsageCache: oldUsage,
    items: [
      { id: "compact-1", type: "compact", text: "compressed", createdAt: "2026-10-14T02:24:00.000Z", contextUsageBoundaryItemId: "retained-agent", summarizedItemIds: [] },
      { id: "retained-agent", type: "agent", createdAt: "2026-10-14T02:23:59.000Z", response: { contextUsage: oldUsage } },
      { id: "new-agent", type: "agent", createdAt: "2026-10-14T02:24:01.000Z", response: { contextUsage: newUsage } },
    ],
  };
  assert.deepEqual(module.cacheChatContextUsageFast(chat).contextUsageCache, newUsage);
});

test("explicit boundary ids permit a timestamp-free post-compaction cache receipt", () => {
  const usage = { exact: true, inputTokens: 9500, totalTokens: 9500 };
  const chat = {
    id: "chat-1",
    contextUsageCache: usage,
    items: [
      { id: "compact-1", type: "compact", text: "compressed", createdAt: "2026-10-14T02:24:00.000Z", contextUsageBoundaryItemId: "retained-agent", summarizedItemIds: [] },
      { id: "retained-agent", type: "agent", response: { contextUsage: usage } },
      { id: "new-agent", type: "agent", response: { contextUsage: usage } },
    ],
  };
  assert.deepEqual(module.cacheChatContextUsageFast(chat).contextUsageCache, usage);
});
