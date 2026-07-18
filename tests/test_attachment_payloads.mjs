import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = path.join(root, "src/lib/attachment-payloads.ts");
const source = await readFile(sourcePath, "utf8");
const chatSessionsSource = await readFile(path.join(root, "src/hooks/use-chat-sessions.ts"), "utf8");
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: sourcePath,
}).outputText;
const payloads = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`);

const textAttachment = (overrides = {}) => ({
  id: "attachment-1",
  name: "notes.txt",
  size: 15,
  type: "text/plain",
  payloadKind: "text",
  text: "first\nsecond\nthird",
  ...overrides,
});

test("persistence stores one text payload and leaves only a stable message reference", () => {
  const vault = {};
  const stored = payloads.persistAttachmentReference(textAttachment(), vault);
  assert.ok(stored.payloadHash);
  assert.equal(stored.text, undefined);
  assert.equal(vault[stored.payloadHash].text, "first\nsecond\nthird");
  assert.equal(vault[stored.payloadHash].payloadHash, stored.payloadHash);
  assert.equal(stored.payloadHash, "796c06772295d9604559518dc7fd2e3a2bc14970902a6fda43d636b29d6b27fc");
  assert.equal(
    payloads.attachmentPayloadHash("\ud800"),
    "8a8de823d5ed3e12746a62ef169bcf372be0ca44f0a1236abc35df05d96928e1",
  );

  const restoredVault = payloads.normalizeAttachmentPayloadVault(JSON.parse(JSON.stringify(vault)));
  assert.deepEqual(restoredVault, vault);
  const corruptVault = { ...vault, [stored.payloadHash]: { ...vault[stored.payloadHash], text: "tampered" } };
  assert.equal(payloads.normalizeAttachmentPayloadVault(corruptVault), undefined);
});

test("restored chat normalization keeps and validates the attachment payload vault", () => {
  assert.match(
    chatSessionsSource,
    /attachmentPayloads:\s*normalizeAttachmentPayloadVault\(chat\.attachmentPayloads\)/,
  );
});

test("a file follow-up restores exactly one referenced attachment body", () => {
  const vault = {};
  const stored = payloads.persistAttachmentReference(textAttachment(), vault);
  const result = payloads.resolveHistoricalAttachmentPayloads(
    [{ id: "user-1", type: "user", text: "please inspect", attachments: [stored] }],
    vault,
    "文件里第三项是什么？",
  );
  assert.equal(result.degraded, undefined);
  assert.equal(result.attachments.length, 1);
  assert.equal(result.attachments[0].text, "first\nsecond\nthird");
  assert.equal(result.attachments[0].payloadHash, stored.payloadHash);
});

test("ambiguous or corrupt historical references never borrow another attachment body", () => {
  const vault = {};
  const first = payloads.persistAttachmentReference(textAttachment({ id: "first", name: "first.txt", text: "first payload" }), vault);
  const second = payloads.persistAttachmentReference(textAttachment({ id: "second", name: "second.txt", text: "second payload" }), vault);
  const ambiguous = payloads.resolveHistoricalAttachmentPayloads(
    [{ id: "user-1", type: "user", text: "two files", attachments: [first, second] }],
    vault,
    "文件里第三项是什么？",
  );
  assert.equal(ambiguous.degraded, "ambiguous");
  assert.equal(ambiguous.attachments.length, 1);
  assert.equal(ambiguous.attachments[0].payloadKind, "metadata");
  assert.match(ambiguous.attachments[0].error, /name the file/i);

  const corrupt = payloads.resolveHistoricalAttachmentPayloads(
    [{ id: "user-2", type: "user", text: "one file", attachments: [{ ...first, payloadHash: "deadbeef".repeat(8) }] }],
    vault,
    "what is in this attachment?",
  );
  assert.equal(corrupt.degraded, "missing_or_corrupt");
  assert.equal(corrupt.attachments.length, 1);
  assert.equal(corrupt.attachments[0].text, undefined);
  assert.match(corrupt.attachments[0].error, /missing or corrupt/i);
});

test("ordinary turns do not restore historical attachment payloads", () => {
  const vault = {};
  const stored = payloads.persistAttachmentReference(textAttachment(), vault);
  const result = payloads.resolveHistoricalAttachmentPayloads(
    [{ id: "user-1", type: "user", text: "notes", attachments: [stored] }],
    vault,
    "continue with the plan",
  );
  assert.deepEqual(result.attachments, []);
});

test("an explicitly resent payloadHash reference is hydrated without transcript guessing", () => {
  const vault = {};
  const stored = payloads.persistAttachmentReference(textAttachment(), vault);
  const restored = payloads.resolveAttachmentPayloadReferences([stored], vault);
  assert.equal(restored.length, 1);
  assert.equal(restored[0].text, "first\nsecond\nthird");

  const missing = payloads.resolveAttachmentPayloadReferences([{ ...stored, payloadHash: "0".repeat(64) }], vault);
  assert.equal(missing[0].payloadKind, "metadata");
  assert.match(missing[0].error, /missing or corrupt/i);
});

test("generic item wording does not silently attach an unrelated historical file", () => {
  const vault = {};
  const stored = payloads.persistAttachmentReference(textAttachment(), vault);
  const result = payloads.resolveHistoricalAttachmentPayloads(
    [{ id: "user-1", type: "user", text: "notes", attachments: [stored] }],
    vault,
    "continue with the third item in the plan",
  );
  assert.deepEqual(result.attachments, []);
});

test("payload garbage collection retains only bodies referenced by durable messages", () => {
  const vault = {};
  const kept = payloads.persistAttachmentReference(textAttachment({ id: "kept" }), vault);
  payloads.persistAttachmentReference(textAttachment({ id: "stale", text: "stale body" }), vault);
  const selected = payloads.referencedAttachmentPayloadVault(
    [{ id: "user-1", type: "user", text: "notes", attachments: [kept] }],
    vault,
  );
  assert.deepEqual(Object.keys(selected), [kept.payloadHash]);
});
