import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { createSteerDeliveryTrace } from "../src/lib/chat-steer-delivery.ts";

const controller = await readFile(resolve(import.meta.dirname, "../src/hooks/use-chat-run-controller.ts"), "utf8");
const submitStart = controller.indexOf("async function submitTurn(");
const submitEnd = controller.indexOf("  async function runTurnNow(", submitStart);
const submit = controller.slice(submitStart, submitEnd);
assert.ok(submitStart >= 0 && submitEnd > submitStart);
for (const stage of [
  "submit_received", "submit_direct", "reservation_rejected", "reservation_acquired",
  "steer_context_resolved", "persist_started", "persist_resolved", "persist_rejected",
  "dispatch_wait_started", "dispatch_wait_resolved", "dispatch_skipped_stop",
  "rpc_started", "rpc_ack", "rpc_rejected", "rpc_failed",
]) {
  assert.ok(submit.includes(`trace("${stage}"`), `submitTurn must record ${stage}`);
}
assert.ok(submit.indexOf('trace("persist_started")') < submit.indexOf("await persistChatsNow?.()"));
assert.ok(submit.indexOf("await persistChatsNow?.()") < submit.indexOf('trace("persist_resolved"'));
assert.ok(submit.indexOf('trace("persist_resolved"') < submit.indexOf('trace("dispatch_wait_started"'));
assert.ok(submit.indexOf('trace("dispatch_wait_resolved"') < submit.indexOf('trace("rpc_started"'));

const events = [];
const trace = createSteerDeliveryTrace((event) => events.push(event), {
  chatId: "chat-safe-id",
  sessionId: "session-safe-id",
  clientTurnId: "turn-safe-id",
});
let releasePersist;
const pendingPersist = new Promise((resolvePersist) => { releasePersist = resolvePersist; });
let rpcStarted = false;
trace("submit_received");
trace("persist_started");
const dispatch = pendingPersist.then(() => {
  trace("persist_resolved");
  trace("dispatch_wait_started", { targetClientTurnId: "active-turn-id", pendingDispatches: 1 });
  trace("dispatch_wait_resolved", { targetClientTurnId: "active-turn-id", waitMs: 3, pendingDispatches: 0 });
  trace("rpc_started", { targetClientTurnId: "active-turn-id" });
  rpcStarted = true;
  trace("rpc_ack", { targetClientTurnId: "active-turn-id", accepted: true, mode: "steer" });
});
await Promise.resolve();
assert.equal(rpcStarted, false, "the request must remain visibly before RPC while durable save is pending");
assert.deepEqual(events.map((event) => event.stage), ["submit_received", "persist_started"]);
releasePersist();
await dispatch;
assert.equal(rpcStarted, true);
assert.deepEqual(events.map((event) => event.stage), [
  "submit_received", "persist_started", "persist_resolved", "dispatch_wait_started",
  "dispatch_wait_resolved", "rpc_started", "rpc_ack",
]);
assert.equal(events.at(-1).clientTurnId, "turn-safe-id");
assert.equal(events.at(-1).targetClientTurnId, "active-turn-id", "the RPC ack keeps the active turn identity separate from the submitted turn");
assert.equal(events.at(-1).accepted, true);
assert.ok(events.every((event) => !Object.hasOwn(event, "message") && !Object.hasOwn(event, "body")));
assert.ok(events.every((event) => Number.isFinite(event.elapsedMs) && event.elapsedMs >= 0));

const throwingTrace = createSteerDeliveryTrace(() => { throw new Error("diagnostics are isolated"); }, {
  chatId: "chat-safe-id", clientTurnId: "turn-safe-id",
});
assert.doesNotThrow(() => throwingTrace("persist_started"), "diagnostic failures cannot alter delivery");

console.log("steer delivery trace stage replay: ok");
