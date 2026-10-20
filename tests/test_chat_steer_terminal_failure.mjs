import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { settleTerminalSteerDelivery } from "../src/lib/chat-steer-delivery.ts";

const controllerPath = process.env.VRCFORGE_RUN_CONTROLLER_SOURCE_PATH
  || resolve(import.meta.dirname, "../src/hooks/use-chat-run-controller.ts");
const controller = await readFile(controllerPath, "utf8");
const runSingleTurnStart = controller.indexOf("async function runSingleTurn(");
const failureCatchStart = controller.indexOf("} catch (cause) {", runSingleTurnStart);
const failureCatchEnd = controller.indexOf("} finally {", failureCatchStart);
assert.ok(runSingleTurnStart >= 0 && failureCatchStart > runSingleTurnStart && failureCatchEnd > failureCatchStart);
assert.match(
  controller.slice(failureCatchStart, failureCatchEnd),
  /settleTerminalSteerDelivery\(/,
  "a terminal foreground run failure must settle any unresolved steer inputs",
);

const failedSessionId = "session-turn-1792380605669-1d17324ffaf8b8";
const originalSteerTurnId = "turn-1792391617440-980e0e15a65b58";
const persistedUserItem = {
  id: "user-turn-1792391617440-980e0e15a65b58",
  type: "user",
  text: "Unity Console 里有报错，先看一下具体错误，确认是不是这次操作引起的，再继续。",
  clientTurnId: originalSteerTurnId,
  queueStatus: "steering",
  queueEnvelope: { sessionId: failedSessionId },
};
const unrelatedSteer = {
  id: "user-unrelated-steer",
  type: "user",
  clientTurnId: "turn-unrelated",
  queueStatus: "steering",
};
const intents = new Map([
  [originalSteerTurnId, { chatId: "chat-sample-session", sessionId: failedSessionId }],
  ["turn-other-chat", { chatId: "chat-other", sessionId: failedSessionId }],
]);

const replay = settleTerminalSteerDelivery(
  [persistedUserItem, unrelatedSteer],
  intents,
  { chatId: "chat-sample-session", sessionId: failedSessionId },
);

assert.deepEqual(replay.settledIds, [originalSteerTurnId]);
assert.equal(replay.items[0].queueStatus, "delivery_unverified");
assert.equal(replay.items[0].queueEnvelope.sessionId, failedSessionId, "retain the immutable context for an explicit user retry");
assert.equal(replay.items[1].queueStatus, "steering", "do not change another chat's pending input");
assert.equal(intents.has(originalSteerTurnId), false, "terminal failure must release the in-memory pending intent");
assert.equal(intents.has("turn-other-chat"), true, "terminal failure must retain other chat intents");

console.log("terminal failed-run steer recovery replay: ok");
