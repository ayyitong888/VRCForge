import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import ts from "typescript";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = await readFile(path.join(root, "src/lib/context-compaction.ts"), "utf8");
const start = source.indexOf("export function normalizeCompactionRecovery");
const end = source.indexOf("export const CONTEXT_COMPACTION_PREFIRE_RATIO");
const compiled = ts.transpileModule(source.slice(start, end), {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
}).outputText;
const moduleUrl = `data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`;
const { normalizeCompactionRecovery, mergeCompactionRecovery } = await import(moduleUrl);

const first = {
  schema: "vrcforge.context_compaction_recovery.v1",
  sourceEntries: [{ role: "user", text: "tail" }],
  sourceDigest: "source-a",
  summary: "one",
  summaryDigest: "summary-a",
};

test("recovery accepts single or list and deduplicates by source plus summary digest", () => {
  assert.deepEqual(normalizeCompactionRecovery(first), [first]);
  assert.deepEqual(normalizeCompactionRecovery([first]), [first]);
  assert.deepEqual(mergeCompactionRecovery(first, [first, { ...first, summaryDigest: "summary-b" }]), [
    first,
    { ...first, summaryDigest: "summary-b" },
  ]);
});

test("malformed recovery is rejected without entering the model payload", () => {
  assert.deepEqual(normalizeCompactionRecovery({ sourceDigest: "bad", sourceEntries: [{ role: "user" }] }), []);
});

const conversation = await readFile(path.join(root, "src/lib/conversation-utils.ts"), "utf8");
const runController = await readFile(path.join(root, "src/hooks/use-chat-run-controller.ts"), "utf8");
test("ordinary chat history remains separate from recovery collection", () => {
  assert.match(conversation, /export function collectCompactionRecovery/);
  const historyBody = conversation.slice(conversation.indexOf("export function buildChatHistory"), conversation.indexOf("/** Recovery is sent"));
  assert.doesNotMatch(historyBody, /compactionRecovery/);
});

test("runtime replacement merges durable archives before projecting old items", () => {
  const compactStart = runController.indexOf('const compactItem: Extract<ConversationItem, { type: "compact" }>');
  const projectionStart = runController.indexOf("const projection = projectRuntimeCompactionItems", compactStart);
  const replacement = runController.slice(compactStart, projectionStart);
  assert.match(replacement, /mergeCompactionRecovery\(\s*collectCompactionRecovery\(durableItems\),\s*responseRecovery/s);
});
