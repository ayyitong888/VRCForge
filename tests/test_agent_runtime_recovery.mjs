import assert from "node:assert/strict";
import { build } from "esbuild";

const bundle = await build({
  entryPoints: ["src/lib/api/agent-runtime.ts"],
  bundle: true,
  write: false,
  format: "esm",
  platform: "node",
});
const { recoverAgentTurnResponse } = await import(
  `data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`,
);

const sessionId = "session-recovery";
const clientTurnId = "turn-recovery";
const response = {
  ok: true,
  sessionId,
  session_id: sessionId,
  clientTurnId,
  turnId: "turn-runtime",
  turn_id: "turn-runtime",
  observe: { result: "kept" },
  plan: { summary: "done", planner: "runtime", shellNeeded: false, nextStep: "done" },
  steps: [{ kind: "tool", result: { value: "kept" } }],
  consumedSteerInputIds: ["steer-1"],
};
const noWait = async () => undefined;
const transportError = new Error("Request timed out after 600s");

let posts = 0;
const states = [
  { ok: true, sessionId, clientTurnId, status: "running" },
  { ok: true, sessionId, clientTurnId, status: "completed", response },
];
const recovered = await recoverAgentTurnResponse(
  transportError,
  sessionId,
  clientTurnId,
  async () => states[posts++] ,
  undefined,
  noWait,
);
assert.equal(posts, 2, "recovery reads only after the one original POST failed");
assert.deepEqual(recovered, response, "the full recovered response reaches the controller unchanged");

await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async () => ({ ok: true, sessionId, clientTurnId, status: "missing" }), undefined, noWait),
  (error) => error === transportError,
  "initial missing must return the original transport error",
);

await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async () => ({ ok: true, sessionId: "wrong", clientTurnId, status: "running" }), undefined, noWait),
  /identity did not match/,
);

await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async () => ({ ok: true, sessionId, clientTurnId, status: "failed", error: "provider failed" }), undefined, noWait),
  /provider failed/,
);

await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async () => ({ ok: false, sessionId, clientTurnId, status: "unknown" }), undefined, noWait),
  /invalid status/,
  "unknown or non-ok recovery state must stop",
);

const abort = new AbortController();
let readCount = 0;
await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async (_s, _c, signal) => {
    readCount += 1;
    if (readCount === 1) return { ok: true, sessionId, clientTurnId, status: "running" };
    abort.abort();
    signal?.throwIfAborted?.();
    return { ok: true, sessionId, clientTurnId, status: "running" };
  }, abort.signal, noWait),
  /cancelled/i,
);
assert.equal(readCount, 2, "abort during a read stops subsequent polling");

const sleepAbort = new AbortController();
let sleepTimerFired = false;
let sleepReadCount = 0;
const sleep = (signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(() => { sleepTimerFired = true; resolve(); }, 50);
  signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new Error("cancelled")); }, { once: true });
  setTimeout(() => sleepAbort.abort(), 0);
});
await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async () => {
    sleepReadCount += 1;
    return { ok: true, sessionId, clientTurnId, status: "running" };
  }, sleepAbort.signal, sleep),
  /cancelled/,
);
assert.equal(sleepReadCount, 1, "abort during poll sleep must not start another GET");
assert.equal(sleepTimerFired, false, "abort must clear the poll timer");

let failedReads = 0;
await assert.rejects(
  recoverAgentTurnResponse(transportError, sessionId, clientTurnId, async () => {
    failedReads += 1;
    throw new Error("temporary read failure");
  }, undefined, noWait),
  /temporary read failure/,
);
assert.equal(failedReads, 3, "poll read failures stop after three consecutive errors");

console.log("agent runtime recovery regression passed");
