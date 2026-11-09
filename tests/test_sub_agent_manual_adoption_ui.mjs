import assert from "node:assert/strict";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), contents: `
    import { buildChatHistory } from "./src/lib/conversation-utils";
    import { canAdoptSubAgentResult, canDismissSubAgentResult, isAwaitingMergeReview, subAgentAdoptedHistoryItem } from "./src/lib/subagent-merge";
    export { canAdoptSubAgentResult, canDismissSubAgentResult, isAwaitingMergeReview, subAgentAdoptedHistoryItem };
    export const build = (items) => buildChatHistory(items, (key) => key);
  ` },
  bundle: true, write: false, platform: "node", format: "cjs",
});
const filename = fileURLToPath(new URL("./sub-agent-adoption-fixture.cjs", import.meta.url));
const compiled = new Module(filename);
compiled.paths = Module._nodeModulePaths(process.cwd());
compiled._compile(bundle.outputFiles[0].text, filename);
const api = compiled.exports;
const task = { id: "child", status: "completed", displayName: "Reader",
  result: { summaryText: "Preview", finalReport: "The important finding is in the complete report." } };
assert.equal(api.canAdoptSubAgentResult(task), true);
assert.equal(api.subAgentAdoptedHistoryItem(task), undefined);
for (const parentContinuationStatus of ["pending", "dispatching", "delivered", "interrupted"]) {
  const auto = { ...task, parentContinuationStatus };
  assert.equal(api.canAdoptSubAgentResult(auto), false);
  assert.equal(api.canDismissSubAgentResult(auto), false);
  assert.equal(api.isAwaitingMergeReview(auto), false);
  assert.equal(api.subAgentAdoptedHistoryItem({ ...auto, mergeDecision: "adopted" }), undefined);
}
assert.equal(api.subAgentAdoptedHistoryItem({ ...task, mergeDecision: "dismissed" }), undefined);
const adopted = api.subAgentAdoptedHistoryItem({ ...task, mergeDecision: "adopted" });
assert.equal(adopted.id, "subagent-child");
const restored = JSON.parse(JSON.stringify(adopted));
const history = api.build([restored]);
assert.equal(history.length, 1);
assert.equal(history[0].role, "agent");
assert.equal(history[0].subAgentTaskId, "child");
assert.match(history[0].text, /important finding/);
console.log("manual adoption history and automatic delivery separation: ok");
