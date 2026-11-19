import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const source = fs.readFileSync("src/lib/chat-title.ts", "utf8");
const output = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const module = { exports: {} };
new Function("exports", "module", output)(module.exports, module);
const { applyGeneratedChatTitle, canRequestChatTitleAfterTurn, shouldGenerateChatTitle } = module.exports;

const fresh = { title: "", titleSource: undefined };
assert.equal(shouldGenerateChatTitle(fresh, 0, false), true);
assert.equal(shouldGenerateChatTitle(fresh, 1, false), false);
assert.equal(shouldGenerateChatTitle(fresh, 0, true), false);
assert.equal(shouldGenerateChatTitle({ title: "First request", titleSource: "auto" }, 0, false), false);
assert.equal(shouldGenerateChatTitle({ title: "User title", titleSource: "manual" }, 0, false), false);

assert.equal(canRequestChatTitleAfterTurn({ ok: true, status: "completed" }), true);
for (const status of ["cancelled", "blocked", "needs_user_action"]) {
  assert.equal(canRequestChatTitleAfterTurn({ ok: true, status }), false, status);
}
assert.equal(canRequestChatTitleAfterTurn({ ok: false, status: "failed" }), true);
assert.equal(canRequestChatTitleAfterTurn({ turnStatus: { status: "cancelled" } }), false);
assert.equal(canRequestChatTitleAfterTurn({ dispatchStopped: true }), false);
// RC6 stored this delivery with a post-tool planner error.
assert.equal(canRequestChatTitleAfterTurn({
  ok: true,
  turnStatus: { status: "failed" },
  plan: { plannerFailure: { code: "planner_invalid_response", phase: "post_tool", retryable: true } },
}), true);

const pending = { title: "First request", titleSource: "auto" };
assert.deepEqual(applyGeneratedChatTitle(pending, "First request", "Useful title"), {
  title: "Useful title", titleSource: "auto",
});
assert.deepEqual(applyGeneratedChatTitle({ title: "First request", titleSource: "manual" }, "First request", "Useful title"), {
  title: "First request", titleSource: "manual",
});
assert.deepEqual(applyGeneratedChatTitle(pending, "First request", ""), pending);

console.log("chat title behavior checks passed");
