import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { build } from "esbuild";
import Module from "node:module";

const compiled = await build({
  stdin: { resolveDir: process.cwd(), contents: `
    export { latestConversationItemId, isRetryableConversationItem, pendingConversationRetry } from "./src/lib/conversation-utils";
    export { automaticTurnWaitStage } from "./src/lib/runtime-run-status";
  ` },
  platform: "node", bundle: true, format: "cjs", write: false, packages: "external",
});
const fixture = new Module("retry-tests");
fixture.paths = Module._nodeModulePaths(process.cwd());
fixture._compile(compiled.outputFiles[0].text, "retry-tests");
const { latestConversationItemId, isRetryableConversationItem, pendingConversationRetry } = fixture.exports;
function retry(chat, tasks, runs) {
  const id = latestConversationItemId(chat.items, isRetryableConversationItem);
  return pendingConversationRetry(chat, id, tasks, runs) ? "" : id;
}
const user = {type:"user", id:"user-request", clientTurnId:"request", text:"Test"};
const response = { sessionId:"session", clientTurnId:"request", turnId:"turn", plan:{nextStep:"done"} };
const item = {type:"agent", id:"reply", response};
const chat = {id:"chat", sessionId:"session", items:[user, item]};
const task = {parentChatId:"chat", parentSessionId:"session", parentTurnId:"turn", parentClientTurnId:"request", status:"running"};
const parked = {...item, response:{...response, plan:{ nextStep:"waiting_for_tool",reason:"sub_agent_pending" }}};
const withWait = {...chat, items:[user,parked]};
const main = {...response, status:"running", agent:"desktop-agent"};
if (process.argv[2]) {
  const data = name => JSON.parse(readFileSync(join(process.argv[2], name + ".json"), "utf8"));
  const original = data("original-chat");
  assert.equal(retry(original, data("original-tasks"), data("original-runs")), "", "the parked parent cannot rerun its original request");
  assert.equal(retry(original, data("after-tasks"), data("after-runs")), "", "during automatic delivery there is no ordinary retry");
  const parent = original.items.filter(i => i.type === "agent").at(-1).response;
  assert.equal(fixture.exports.automaticTurnWaitStage(parent, data("original-tasks"), data("original-runs")), "waiting");
  assert.equal(fixture.exports.automaticTurnWaitStage(parent, data("after-tasks"), data("after-runs")), "handoff");
}
assert.equal(retry(withWait, [], []), "", "the durable wait receipt arrives before the child registry");
assert.equal(retry(chat, [task], []), "", "the parent's independent actions do not end its child");
assert.equal(retry(chat, [{...task, status:"completed", parentContinuationStatus:"pending"}], []), "");
assert.equal(retry(chat, [{...task, status:"completed", parentContinuationStatus:"dispatching"}], []), "");
assert.equal(retry(chat, [{...task, status:"failed", parentContinuationStatus:"interrupted"}], []), "reply");
assert.equal(retry(withWait, [{...task, status:"failed", parentContinuationStatus:"interrupted"}], []), "reply");
assert.equal(retry(chat, [{...task, status:"completed", parentContinuationStatus:"delivered"}], []), "reply");
for (const other of [{parentChatId:"elsewhere"}, {parentSessionId:"another"}, {parentClientTurnId:"old-turn"}, {parentTurnId:"other-turn"}]) {
  assert.equal(retry(chat, [{...task,...other}], []), "reply", "independent histories retain retry");
}
const shell = {...item, response:{...response, shell:{status:"running"}, plan:{nextStep:"waiting_for_tool", reason:"action_not_terminal"}}};
const shellChat = {...chat, items:[user,shell]};
assert.equal(retry(shellChat, [], []), "", "an automatically resuming shell also owns its original request");
for (const [reason, status] of [["tool_poll_timeout","running"],["action_not_terminal","failed"]]) {
  const stopped = {...shell, response:{...shell.response, shell:{status}, plan:{nextStep:"waiting_for_tool",reason}}};
  assert.equal(retry({...chat,items:[user,stopped]}, [], []), "reply", "manual shell failures retain retry");
}
if (process.argv[3]) {
  const [record] = JSON.parse(readFileSync(process.argv[3], "utf8"));
  const restored = {id:record.turnId, type:"agent", response:{sessionId:record.sessionId, clientTurnId:record.clientTurnId,
    turnId:record.turnId, plan:{nextStep:record.nextStep,reason:record.reason}}};
  assert.equal(retry({...chat,sessionId:record.sessionId,items:[user,restored]}, [], [record]), "",
    "an old shell continuation can omit shell status even though its ledger retains it");
}
assert.equal(retry(chat, [], [main]), "", "the backend can still own the exact turn after the local POST returns");
assert.equal(retry(chat, [], [{...main, status:"failed"}]), "reply");
const continuation = {...item, id:"followup", response:{...response, clientTurnId:"follow", turnId:"child-of-turn", parentTurnId:"turn", parentClientTurnId:"request"}};
assert.equal(retry({...chat, items:[...chat.items,continuation]},[task],[]), "", "a new segment cannot replay a parent whose parallel child is running");
assert.equal(retry({...chat, items:[...chat.items,{...user,id:"user-new",clientTurnId:"new"}, {...item,id:"different",response:{...response,turnId:"new-turn",clientTurnId:"new"}}]},[task],[]), "different");
console.log("parent retry eligibility and child delivery replay: passed");
