import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync("src/lib/chat-title.ts", "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} };
new Function("exports", "module", output)(module.exports, module);
const { applyGeneratedChatTitle, chatTitleRunSucceeded, shouldGenerateChatTitle } = module.exports;

const fresh = { title: "", titleSource: undefined };
assert.equal(shouldGenerateChatTitle(fresh, 0, false), true);
assert.equal(shouldGenerateChatTitle(fresh, 1, false), false);
assert.equal(shouldGenerateChatTitle(fresh, 0, true), false);
assert.equal(shouldGenerateChatTitle({ title: "First request", titleSource: "auto" }, 0, false), false);
assert.equal(shouldGenerateChatTitle({ title: "User title", titleSource: "manual" }, 0, false), false);

assert.equal(chatTitleRunSucceeded({ ok: true, status: "completed", plan: {} }), true);
for (const status of ["failed", "cancelled", "blocked", "needs_user_action"]) {
  assert.equal(chatTitleRunSucceeded({ ok: true, status, plan: {} }), false, status);
}
assert.equal(chatTitleRunSucceeded({ ok: false, status: "completed", plan: {} }), false);
assert.equal(chatTitleRunSucceeded({ ok: true, status: "completed", plan: { plannerFailure: { code: "provider_failed" } } }), false);

const pending = { title: "First request", titleSource: "auto" };
assert.deepEqual(applyGeneratedChatTitle(pending, "First request", "Useful title"), {
  title: "Useful title", titleSource: "auto",
});
assert.deepEqual(applyGeneratedChatTitle({ title: "User title", titleSource: "manual" }, "First request", "Useful title"), {
  title: "User title", titleSource: "manual",
});
assert.deepEqual(applyGeneratedChatTitle(pending, "First request", ""), pending);

console.log("chat title behavior checks passed");
