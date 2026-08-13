import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { normalizeAgentRuntimePhase, normalizeAgentRuntimeTimelineEvent } from "../src/lib/chat-streaming.ts";
import { RuntimeQueueArbitrator, takeNextRunnableQueuedTurn } from "../src/lib/runtime-queue-arbitration.ts";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");
const [app, workspace, card, composer, projection, streaming, runController, capture, timeline, chatThread, timelinePresentation] = await Promise.all([
  read("src/App.tsx"),
  read("src/components/chat/chat-workspace.tsx"),
  read("src/components/chat/conversation-card.tsx"),
  read("src/components/chat/composer.tsx"),
  read("src/lib/conversation-utils.ts"),
  read("src/lib/chat-streaming.ts"),
  read("src/hooks/use-chat-run-controller.ts"),
  read("src/lib/path-to-skill-context.ts"),
  read("src/components/chat/conversation-timeline.tsx"),
  read("src/lib/chat-thread.ts"),
  read("src/lib/chat-timeline-presentation.ts"),
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
assert.match(runController, /normalizeAgentRuntimeTimelineEvent\(delta\.timelineEvent\)/);
assert.match(runController, /arbitration === "start" \|\| !sendingRef\.current/, "queue fallback must self-drain if run ends after settlement but before enqueue");
assert.match(streaming, /delta\.textDelta \? "receiving_response" : item\.phase/);
assert.doesNotMatch(runController, /delta\.label/);
assert.match(streamingBranch, /StreamingPhaseStatus/);
assert.match(card, /function StreamingPhaseStatus/);
assert.match(card, /Loader2/);
assert.match(card, /streamingPhaseLabel/);
assert.match(streamingBranch, /data-vrcforge-live-runtime-timeline/);
assert.match(streamingBranch, /buildDurableTimelineRows\(item\.timeline\)/, "safe tool events must appear while the turn is still running");
assert.match(streaming, /phase === "waiting_for_model" && item\.phase !== "waiting_for_model"/, "a new model pass replaces stale wheel-talk instead of appending it forever");
assert.ok(streamingBranch.indexOf("StreamingPhaseStatus") < streamingBranch.lastIndexOf("item.text"), "spinner must not be restricted to the empty-text branch");
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
const waiting = [{ id: "backpressured", queueStatus: "waiting_for_resources" }, { id: "later", queueStatus: "queued" }];
assert.equal(takeNextRunnableQueuedTurn(waiting), undefined, "normal drain must retain a backpressured head");
assert.equal(waiting[0].id, "backpressured", "backpressured input must remain FIFO-visible");
waiting[0].queueStatus = "queued";
assert.equal(takeNextRunnableQueuedTurn(waiting)?.id, "backpressured", "only explicit resume may make the item runnable");
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
for (let i = 0; i < 32; i++) assert.ok(arb.reserve(`unbounded-${i}`, 0));
assert.ok(arb.reserve("unbounded-32", 0), "message count must not reject a durable follow-up");
arb.stop();
const afterStop = arb.reserve("after-stop", 0);
assert.ok(afterStop, "stop must release every pending reservation");
arb.stop();
assert.equal(await afterStop.decision, "drop");
assert.match(runController, /queueDispatchTailRef/, "queue API mutations must be serialized in submit order");
assert.match(runController, /void submitTurn\(durableTurn\)/, "a race-started follow-up must retain the normal FIFO drain loop");
assert.match(runController, /queueSequence[\s\S]*Number\.MAX_SAFE_INTEGER/, "restart recovery must sort by durable FIFO sequence");
assert.match(runController, /item\.queueStatus === "steering"[\s\S]*queueStatus: "delivery_unverified"/, "a crash-interrupted steer must become explicit and must not replay");
assert.match(runController, /if \(item\.queueStatus === "delivery_unverified"\) continue/, "ambiguous delivery must remain visible without automatic duplicate execution");
assert.match(runController, /result\.status === "acked"[\s\S]*"delivery_unverified"/, "an ack tombstone without a committed chat response must not be executed again");
assert.match(runController, /response\.consumedSteerInputIds[\s\S]*steerIntentRef\.current\.delete/, "accepted steer UI state clears only after Runtime confirms consumption");
assert.match(runController, /response\.deferredSteerFollowups[\s\S]*queueStatus: "queued"/, "a final-boundary steer race must become the next durable follow-up");
assert.match(runController, /response\.deferredSteerFollowupOutcomes[\s\S]*deferredSteerBackpressure[\s\S]*queueStatus: "waiting_for_resources"/, "late-steer durable backpressure must become a visible retryable pending input");
assert.match(chatThread, /mergeRuntimeTimelines/);
assert.match(timelinePresentation, /timestampValue\(left\.timestamp\) - timestampValue\(right\.timestamp\)/, "live phases and durable Runtime events must merge by their authoritative timestamps");
assert.match(timelinePresentation, /\.map\(\(event, sequence\) => \(\{ \.\.\.event, sequence \}\)\)/, "merged timeline order must be resequenced after timestamp sorting");
assert.match(runController, /mergeRuntimeTimelines\(streamedTimeline, durableTimeline\)/);
assert.match(runController, /sessionId: current\.sessionId[\s\S]*clientTurnId: current\.clientTurnId/, "Stop must target the exact active session and client turn");
assert.match(runController, /item\.type !== "user" \|\| item\.clientTurnId !== turn\.id/, "the pre-persisted queued user input must not be duplicated in provider history");
assert.match(runController, /resumeQueuedTurns/);
assert.match(runController, /cancelQueuedTurns/);
assert.doesNotMatch(runController, /MAX_QUEUED_TURNS|queue_full/);
assert.match(workspace, /data-chat-resume-followups/);
assert.match(workspace, /data-chat-cancel-followups/);
for (const forbidden of ["ReasoningTracePanel", "thinkingTraceLabel", "thinking.provider", "hiddenSummary", "opaqueRetained"]) {
  assert.doesNotMatch(timeline, new RegExp(forbidden.replace(/[.]/g, "\\.")), `timeline must not expose ${forbidden}`);
}
for (const phase of ["preparing", "waiting_for_model", "receiving_response", "running_tool", "waiting_for_approval", "verifying"]) {
  assert.equal(normalizeAgentRuntimePhase(phase), phase);
}
for (const untrusted of ["reasoning", "chain_of_thought", "I should reveal this", "running_tool: secret args", ""]) {
  assert.equal(normalizeAgentRuntimePhase(untrusted), undefined);
}
assert.deepEqual(
  normalizeAgentRuntimeTimelineEvent({ id: "evt-1", sequence: 4, timestamp: "2026-08-14T00:00:00Z", kind: "tool_call", payload: { tool: "vrcforge_list_directory", status: "started", actionId: "action-safe-1", arguments: { secret: true }, reasoning: "hidden" } }),
  { id: "evt-1", sequence: 4, timestamp: "2026-08-14T00:00:00Z", kind: "tool_call", payload: { status: "started", tool: "vrcforge_list_directory", actionId: "action-safe-1" } },
);
assert.equal(normalizeAgentRuntimeTimelineEvent({ kind: "reasoning", payload: { summary: "hidden" } }), undefined);

assert.doesNotMatch(app, /<RuntimeActivityPanel|activityPanel=\{runtimeActivityPanel\}/);
assert.doesNotMatch(workspace, /activityPanel\??:|\{activityPanel\}/);
assert.match(capture, /matchPathToSkillRuntimeOperation/);
assert.match(capture, /response\.clientTurnId/);
assert.match(workspace, /matchPathToSkillRuntimeOperation\(item\.response, runtimeRuns\)/);
assert.match(card, /data-vrcforge-save-operation-as-skill/);
assert.match(card, /onSaveOperationAsSkill\?\.\(saveOperationSummary\)/);

console.log("active chat UX contract: ok");
