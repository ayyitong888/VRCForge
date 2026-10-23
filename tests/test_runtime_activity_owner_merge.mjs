import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { build } from "esbuild";
const require = createRequire(import.meta.url);
async function load(file) {
  const built = await build({ entryPoints: [file], bundle: true, write: false, platform: "node", format: "cjs", external: ["react"],
    plugins: process.env.VRCFORGE_RUNTIME_OWNER_BASELINE ? [{ name: "baseline", setup(b) {
      b.onLoad({ filter: /use-runtime-turn-(activity|continuation)\.ts$/ }, args => ({ loader: "ts", contents: readFileSync(`${process.env.VRCFORGE_RUNTIME_OWNER_BASELINE}/${args.path.endsWith("activity.ts") ? "activity" : "continuation"}-before.ts`, "utf8") }));
    } }] : [] });
  const module = { exports: {} }; new Function("require", "module", "exports", built.outputFiles[0].text)(require, module, module.exports); return module.exports;
}
const activity = await load("src/hooks/use-runtime-turn-activity.ts");
const continuation = await load("src/hooks/use-runtime-turn-continuation.ts");
const original = process.env.VRCFORGE_CHAT_METADATA_REPLAY ? JSON.parse(readFileSync(process.env.VRCFORGE_CHAT_METADATA_REPLAY, "utf8")) : { id: "chat", sessionId: "session", items: [] };
const identity = { sessionId: original.sessionId, clientTurnId: "continue:question:owned", turnId: "backend-turn" };
const run = { ...identity, status: "running", startedAt: "2026-10-24T00:00:00Z" };
const question = { sessionId: identity.sessionId, questionId: "owned", status: "answered", runtimeContinuationStatus: "running", runtimeContinuation: { ...identity, status: "running" } };
const event = (id, sequence) => ({ id, sequence, timestamp: "2026-10-24T00:00:01Z", kind: "assistant", payload: { phase: "commentary", summary: id } });
let chat = activity.projectRuntimeTurnActivity(original, run);
chat = activity.applyRuntimeActivityDelta(chat, { ...identity, timelineEvent: event("accepted-before-question", 0) });
chat = continuation.projectQuestionContinuationActivity(chat, [question], [run]);
assert.equal(chat.items.filter(item => item.type === "streaming" && item.clientTurnId === identity.clientTurnId).length, 1, "runtime-first and question discovery must share one owner");
const stream = chat.items.find(item => item.type === "streaming" && item.clientTurnId === identity.clientTurnId);
assert.equal(stream.timeline[0].id, "accepted-before-question");
chat = { ...chat, items: [...chat.items, { ...stream, id: `question-continuation:${identity.clientTurnId}`, text: "later", timeline: [event("accepted-on-question", 1)] }] };
assert.equal(continuation.applyQuestionContinuationDelta([chat], () => { throw Error("non-owner must not consume a live delta"); }, { ...identity, textDelta: "once" }), false, "two observers cannot both apply text to the same CID before coalescing");
chat = continuation.projectQuestionContinuationActivity(chat, [question], [run]);
const owners = chat.items.filter(item => item.type === "streaming" && item.clientTurnId === identity.clientTurnId);
assert.equal(owners.length, 1, "existing duplicate owners coalesce");
assert.deepEqual(new Set(owners[0].timeline.map(item => item.id)), new Set(["accepted-before-question", "accepted-on-question"]), "handoff preserves both accepted timelines");
assert.equal(owners[0].text, "later");
const terminal = { ...identity, continuationSource: "foreground_completed" };
assert.equal(continuation.runtimeContinuationDeliveryState(terminal, owners), "append");
const questionFirst = continuation.projectQuestionContinuationActivity(original, [question], [run]);
assert.equal(activity.projectRuntimeTurnActivity(questionFirst, run), questionFirst, "question-first activity retains its owner");
assert.equal(continuation.runtimeContinuationDeliveryState(terminal, questionFirst.items), "append", "the shared observer can settle a question-first row");
assert.equal(continuation.runtimeContinuationDeliveryState(terminal, [{ ...owners[0], id: "local-http-stream" }]), "defer", "foreground HTTP sends keep completion ownership");
assert.equal(activity.applyRuntimeActivityDelta(chat, { ...identity, sessionId: "other", timelineEvent: event("wrong", 2) }), chat);
console.log(`PASS shared runtime/question CID owner and lossless handoff (${original.items.length} original chat items)`);
