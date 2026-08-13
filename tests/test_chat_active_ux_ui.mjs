import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { normalizeAgentRuntimePhase } from "../src/lib/chat-streaming.ts";
import { RuntimeQueueArbitrator } from "../src/lib/runtime-queue-arbitration.ts";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");
const [app, workspace, card, composer, projection, streaming, runController, capture, timeline] = await Promise.all([
  read("src/App.tsx"),
  read("src/components/chat/chat-workspace.tsx"),
  read("src/components/chat/conversation-card.tsx"),
  read("src/components/chat/composer.tsx"),
  read("src/lib/conversation-utils.ts"),
  read("src/lib/chat-streaming.ts"),
  read("src/hooks/use-chat-run-controller.ts"),
  read("src/lib/path-to-skill-context.ts"),
  read("src/components/chat/conversation-timeline.tsx"),
]);

const userBranch = card.slice(card.indexOf('if (item.type === "user")'), card.indexOf('if (item.type === "error")'));
const streamingBranch = card.slice(card.indexOf('if (item.type === "streaming")'), card.indexOf('if (item.type === "result")'));
const sendingActions = composer.slice(composer.indexOf(') : sending ? ('), composer.indexOf("</div>", composer.indexOf(') : sending ? (')));

assert.match(userBranch, /onCopy=\{\(\) => onCopyItem\?\.\(item\)\}/, "every durable user message must expose copy");
assert.match(projection, /if \(item\.type === "user"\)[\s\S]*return item\.text/);
assert.match(userBranch, /relative flex w-full max-w-\[78%\] flex-col items-end gap-2/);

assert.match(composer, /queueAllowed/);
assert.match(sendingActions, /data-composer-stop/);
assert.match(sendingActions, /data-composer-send/);
assert.match(sendingActions, /type="submit"/);
assert.match(app, /queueAllowed=\{chatRunSending && !stopRequested\}/, "compaction or a stopping run must not enable queue submission");

assert.match(streaming, /export type AgentRuntimePhase/);
assert.match(streaming, /normalizeAgentRuntimePhase/);
assert.match(runController, /normalizeAgentRuntimePhase\(delta\.phase\)/);
assert.match(runController, /arbitration === "start" \|\| !sendingRef\.current/, "queue fallback must self-drain if run ends after settlement but before enqueue");
assert.match(runController, /delta\.textDelta \? "receiving_response" : item\.phase/);
assert.doesNotMatch(runController, /delta\.label/);
assert.match(streamingBranch, /Loader2/);
assert.match(streamingBranch, /streamingPhaseLabel/);
assert.ok(streamingBranch.indexOf("Loader2") < streamingBranch.lastIndexOf("item.text"), "spinner must not be restricted to the empty-text branch");
assert.doesNotMatch(streamingBranch, /reasoning|trace|chain.of.thought|cot/i);
const arb = new RuntimeQueueArbitrator(8);
const first = arb.reserve("q0", 0);
const second = arb.reserve("q1", 0);
assert.ok(first && second);
arb.settle("q1", { acceptedSteer: false, stopRequested: false }, true);
let secondResolved = false;
void second.decision.then(() => { secondResolved = true; });
await new Promise((resolvePromise) => setTimeout(resolvePromise, 0));
assert.equal(secondResolved, false, "a later network response must wait for the earlier submitted turn");
arb.settle("q0", { acceptedSteer: false, stopRequested: false }, false);
assert.equal(await first.decision, "start");
assert.equal(await second.decision, "enqueue");
const claimArb = new RuntimeQueueArbitrator(8);
const claimedStart = claimArb.reserve("claim-0", 0);
const claimedFollowup = claimArb.reserve("claim-1", 0);
assert.ok(claimedStart && claimedFollowup);
claimArb.settle("claim-0", { acceptedSteer: false, stopRequested: false }, false);
claimArb.settle("claim-1", { acceptedSteer: false, stopRequested: false }, false);
assert.equal(await claimedStart.decision, "start");
assert.equal(await claimedFollowup.decision, "enqueue", "only one pending turn may claim an idle runner");
const lateArb = new RuntimeQueueArbitrator(8);
const lateStart = lateArb.reserve("late-0", 0);
assert.ok(lateStart);
lateArb.settle("late-0", { acceptedSteer: false, stopRequested: false }, false);
assert.equal(await lateStart.decision, "start");
lateArb.runnerStarted();
const afterRunner = lateArb.reserve("late-1", 0);
assert.ok(afterRunner);
lateArb.settle("late-1", { acceptedSteer: false, stopRequested: false }, false);
assert.equal(await afterRunner.decision, "start", "a late fallback must start after the prior race-started runner has already exited");
for (let i = 0; i < 8; i++) assert.ok(arb.reserve(`full-${i}`, 0));
assert.equal(arb.reserve("full-8", 0), null);
arb.stop();
const afterStop = arb.reserve("after-stop", 0);
assert.ok(afterStop, "stop must release every pending reservation");
arb.stop();
assert.equal(await afterStop.decision, "drop");
assert.match(runController, /queueDispatchTailRef/, "queue API mutations must be serialized in submit order");
assert.match(runController, /void submitTurn\(queuedTurn\)/, "a race-started follow-up must retain the normal FIFO drain loop");
for (const forbidden of ["ReasoningTracePanel", "thinkingTraceLabel", "thinking.provider", "hiddenSummary", "opaqueRetained"]) {
  assert.doesNotMatch(timeline, new RegExp(forbidden.replace(/[.]/g, "\\.")), `timeline must not expose ${forbidden}`);
}
for (const phase of ["preparing", "waiting_for_model", "receiving_response", "running_tool", "waiting_for_approval", "verifying"]) {
  assert.equal(normalizeAgentRuntimePhase(phase), phase);
}
for (const untrusted of ["reasoning", "chain_of_thought", "I should reveal this", "running_tool: secret args", ""]) {
  assert.equal(normalizeAgentRuntimePhase(untrusted), undefined);
}

assert.doesNotMatch(app, /<RuntimeActivityPanel|activityPanel=\{runtimeActivityPanel\}/);
assert.doesNotMatch(workspace, /activityPanel\??:|\{activityPanel\}/);
assert.match(capture, /matchPathToSkillRuntimeOperation/);
assert.match(capture, /response\.clientTurnId/);
assert.match(workspace, /matchPathToSkillRuntimeOperation\(item\.response, runtimeRuns\)/);
assert.match(card, /data-vrcforge-save-operation-as-skill/);
assert.match(card, /onSaveOperationAsSkill\?\.\(saveOperationSummary\)/);

console.log("active chat UX contract: ok");
