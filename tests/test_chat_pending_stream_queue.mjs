import assert from "node:assert/strict";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/lib/chat-thread.ts"],
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
});
const { stripSupersededStreamingItems } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`,
);

const user = { id: "user-active", type: "user", text: "active request" };
const streaming = { id: "stream-active", type: "streaming", clientTurnId: "active-turn", text: "visible live response" };
const pending = {
  id: "user-queued",
  type: "user",
  clientTurnId: "queued-turn",
  text: "queued follow-up",
  composerQueued: true,
  queueStatus: "queued",
};
const steering = {
  id: "user-steering", type: "user", clientTurnId: "steer-turn",
  text: "我更关心界面怎么连到 Unity，概括一下就好。", queuedFrom: true, queueStatus: "steering",
};
assert.deepEqual(
  stripSupersededStreamingItems([user, streaming, steering]),
  [user, streaming, steering],
  "a same-turn steer is user input for the active response and must not supersede its stream",
);

assert.deepEqual(
  stripSupersededStreamingItems([user, streaming, pending]),
  [user, streaming, pending],
  "adding a durable pending queue row must keep the active response visible",
);
assert.deepEqual(
  stripSupersededStreamingItems([user, streaming, { id: "user-next", type: "user", text: "delivered next turn" }]),
  [user, { id: "user-next", type: "user", text: "delivered next turn" }],
  "a delivered next turn still supersedes stale earlier streaming items",
);
const deliveredQueued = { ...pending, composerQueued: undefined, queuedFrom: undefined, queueStatus: undefined };
assert.deepEqual(
  stripSupersededStreamingItems([user, streaming, deliveredQueued]),
  [user, deliveredQueued],
  "a queued input converted by runSingleTurn to an ordinary delivered user row still clears stale streams",
);

const workerCompleted = { id: "worker-completed", type: "timeline_event",
  event: { kind: "subagent", clientTurnId: streaming.clientTurnId, payload: { status: "completed" } } };
const resumed = { ...streaming, id: "runtime-activity:resumed-turn", clientTurnId: "resumed-turn",
  parentClientTurnId: streaming.clientTurnId, continuationSource: "sub_agent_finished" };
const parked = { type: "agent", id: "parent-receipt", response: { clientTurnId: streaming.clientTurnId,
  plan: { nextStep: "waiting_for_tool", reason: "sub_agent_pending" } } };
assert.deepEqual(stripSupersededStreamingItems([user, streaming, workerCompleted]), [user, streaming, workerCompleted],
  "an appended worker lifecycle receipt must preserve the foreground parent until its runtime receipt");
assert.deepEqual(stripSupersededStreamingItems([user, workerCompleted, resumed, parked]), [user, workerCompleted, resumed, parked],
  "a parent receipt settling the first dispatch must preserve the live continuation");

console.log("pending queue row preserves active streaming response");
