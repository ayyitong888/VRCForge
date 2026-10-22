import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  applyAgentRuntimeDeltaToStreamingItem,
  elapsedSecondsSince,
  elapsedSecondsFromTimeline,
  finalizeCancelledStreamingTurn,
  providerWaitingWindow,
} from "../src/lib/chat-streaming.ts";
import { buildTimelinePresentation } from "../src/lib/chat-timeline-presentation.ts";
import fs from "node:fs/promises";
import { build } from "esbuild";
import { pathToFileURL } from "node:url";

const testRoot = resolve(import.meta.dirname, "..");
const testTemp = resolve(testRoot, "tests/.tmp-chat-runtime-streaming");
const continuationBundle = resolve(testTemp, "runtime-continuation.mjs");
await fs.rm(testTemp, { recursive: true, force: true });
await fs.mkdir(testTemp, { recursive: true });
await build({ entryPoints: [resolve(testRoot, "src/hooks/use-runtime-turn-continuation.ts")], outfile: continuationBundle, bundle: true, format: "esm", platform: "node", external: ["react"] });
const { runtimeContinuationDeliveryState } = await import(`${pathToFileURL(continuationBundle).href}?streaming-test`);

const startedAt = "2026-08-14T00:00:00.000Z";
const startedMs = Date.parse(startedAt);
assert.equal(elapsedSecondsSince(startedAt, startedMs + 1_499), 1, "running elapsed time advances from the local turn start");
assert.equal(elapsedSecondsSince(startedAt, startedMs + 2_501), 3, "running elapsed time uses the real wall clock");
assert.equal(elapsedSecondsFromTimeline([{ timestamp: startedAt }]), undefined, "one runtime event cannot prove a duration");
assert.equal(elapsedSecondsFromTimeline([{ timestamp: startedAt }, { timestamp: startedAt }]), undefined, "same-time runtime events cannot prove a duration");
assert.equal(elapsedSecondsFromTimeline([{ timestamp: startedAt }, { timestamp: "2026-08-14T00:00:02.000Z" }]), 2, "runtime event timestamps provide the durable duration");

const foregroundReplay = {
  schema: "vrcforge.runtime_turn_event.v1",
  continuationSource: "foreground_completed",
  sessionId: "session-replay",
  turnId: "turn-replay",
  clientTurnId: "client-replay",
  plan: { reply: "recovered" },
};
assert.equal(runtimeContinuationDeliveryState(foregroundReplay, [
  { id: "live", type: "streaming", clientTurnId: "client-replay", text: "partial" },
]), "defer", "bootstrap replay must wait for an in-flight foreground HTTP turn");
assert.equal(runtimeContinuationDeliveryState(foregroundReplay, [
  { id: "cancelled", type: "agent", response: { clientTurnId: "client-replay", turnId: "turn-replay", status: "cancelled", plan: { reply: "cancelled" } } },
]), "replace-failed", "a cancelled durable placeholder must not block the recovered completion");
assert.equal(runtimeContinuationDeliveryState(foregroundReplay, [
  { id: "done", type: "agent", response: { clientTurnId: "client-replay", turnId: "turn-replay", status: "completed", plan: { reply: "done" } } },
]), "stored", "a durable successful completion must remain exactly once");

const base = {
  id: "stream-client-live",
  type: "streaming",
  clientTurnId: "client-live",
  text: "",
  phase: "preparing",
};
const waiting = applyAgentRuntimeDeltaToStreamingItem(base, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  phase: "waiting_for_model",
}, startedAt);
assert.equal(waiting.providerLastActivityAt, startedAt);

const activityAt = "2026-08-14T00:01:30.000Z";
const activity = applyAgentRuntimeDeltaToStreamingItem(waiting, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  phase: "waiting_for_model",
  activity: true,
}, activityAt);
assert.equal(activity.providerLastActivityAt, activityAt, "reasoning activity resets the idle watchdog");
const providerEventAt = "2026-08-14T00:01:30.500Z";
const providerEvent = applyAgentRuntimeDeltaToStreamingItem(activity, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  activity: true,
}, providerEventAt);
assert.equal(providerEvent.providerLastActivityAt, providerEventAt, "an activity-only Provider event also clears reconnect progress");
const retrying = applyAgentRuntimeDeltaToStreamingItem(activity, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", phase: "waiting_for_model",
  providerRetry: { attempt: 1, max: 5, reason: "connection" },
}, providerEventAt);
assert.deepEqual(retrying.providerRetry, { attempt: 1, max: 5, reason: "connection" }, "reconnect UI uses the real retry event");
const resumed = applyAgentRuntimeDeltaToStreamingItem(retrying, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", phase: "waiting_for_model", activity: true,
}, providerEventAt);
assert.equal(resumed.providerRetry, undefined, "provider activity clears the retry banner and resets the wait clock");
const noFakeRetry = applyAgentRuntimeDeltaToStreamingItem(activity, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", phase: "waiting_for_model",
}, providerEventAt);
assert.equal(noFakeRetry.providerRetry, undefined, "idle time alone never invents a retry attempt");
assert.equal(providerWaitingWindow(activityAt, Date.parse(activityAt) + 59_999), undefined);
assert.equal(providerWaitingWindow(activityAt, Date.parse(activityAt) + 60_000), 1);
assert.equal(providerWaitingWindow(activityAt, Date.parse(activityAt) + 300_000), 5);

const toolCall = {
  id: "timeline-turn-live-4",
  sequence: 4,
  timestamp: "2026-08-14T00:01:31.000Z",
  kind: "tool_call",
  payload: { actionId: "action-live", tool: "vrcforge_list_directory", status: "started" },
};
const running = applyAgentRuntimeDeltaToStreamingItem(providerEvent, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  phase: "running_tool",
  timelineEvent: toolCall,
}, "2026-08-14T00:01:31.000Z");
assert.equal(running.providerLastActivityAt, undefined, "tool execution clears the current Provider idle display");
assert.equal(running.timeline.length, 3);

const repeated = applyAgentRuntimeDeltaToStreamingItem(running, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  timelineEvent: { ...toolCall, payload: { ...toolCall.payload, status: "dispatched" } },
}, "2026-08-14T00:01:31.500Z");
assert.equal(repeated.timeline.filter((event) => event.id === toolCall.id).length, 1, "one live event id owns one row");
assert.equal(repeated.timeline.find((event) => event.id === toolCall.id).payload.status, "dispatched");

const completed = applyAgentRuntimeDeltaToStreamingItem(repeated, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  timelineEvent: {
    id: "timeline-turn-live-5",
    sequence: 5,
    timestamp: "2026-08-14T00:01:32.000Z",
    kind: "tool_result",
    payload: { actionId: "action-live", tool: "vrcforge_list_directory", status: "ok", summary: "listed" },
  },
}, "2026-08-14T00:01:32.000Z");
const livePresentation = buildTimelinePresentation(completed.timeline);
const liveToolBatch = livePresentation.entries.find((entry) => entry.type === "batch" && entry.kind === "tool");
assert.equal(liveToolBatch.invocations.length, 1, "live call/result pair must update one invocation row by actionId");
assert.equal(liveToolBatch.invocations[0].status, "ok");

const cancelled = finalizeCancelledStreamingTurn(completed, {
  sessionId: "session-live",
  cancelledText: "Request cancelled.",
  elapsedSeconds: 367,
  completedAt: "2026-08-14T00:07:37.000Z",
});
assert.equal(cancelled.type, "agent", "Stop must settle the transient card into a durable Agent turn");
assert.equal(cancelled.response.status, "cancelled");
assert.equal(cancelled.response.plan.nextStep, "cancelled");
assert.equal(cancelled.elapsedSeconds, 367, "the stopped turn keeps one whole-turn elapsed duration");
assert.equal(cancelled.timeline.filter((event) => event.kind === "assistant").at(-1).payload.status, "cancelled");
assert.equal(cancelled.response.steps, undefined, "Stop must not resurrect legacy duplicate steps");
assert.equal(cancelled.response.reasoning, undefined, "Stop must not persist partial CoT text");
const cancelledPresentation = buildTimelinePresentation(cancelled.timeline, cancelled.elapsedSeconds);
const cancelledToolBatch = cancelledPresentation.entries.find((entry) => entry.type === "batch" && entry.kind === "tool");
assert.equal(cancelledToolBatch.invocations.length, 1, "Stop must retain the live call/result as one invocation");
assert.equal(cancelledToolBatch.invocations[0].status, "ok");
assert.equal(cancelledPresentation.entries.filter((entry) => entry.type === "assistant").length, 1);
assert.equal(cancelledPresentation.entries.find((entry) => entry.type === "assistant").text, "Request cancelled.");

const nextCallAt = "2026-08-14T00:01:40.000Z";
const nextWaiting = applyAgentRuntimeDeltaToStreamingItem(completed, {
  type: "agentRuntimeDelta",
  clientTurnId: "client-live",
  phase: "waiting_for_model",
}, nextCallAt);
assert.equal(nextWaiting.providerLastActivityAt, nextCallAt, "the next Provider call owns a fresh idle clock");

// A format correction is another Provider call in the same runtime turn.
// Its done event does not finish the turn; the next call's waiting boundary
// discards the invalid draft before any pure-content deltas arrive.
let correctedStream = applyAgentRuntimeDeltaToStreamingItem(base, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", phase: "receiving_response", textDelta: "invalid draft",
});
correctedStream = applyAgentRuntimeDeltaToStreamingItem(correctedStream, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", done: true,
});
assert.equal(correctedStream.text, "invalid draft", "done keeps its existing nonterminal semantics");
correctedStream = applyAgentRuntimeDeltaToStreamingItem(correctedStream, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", phase: "waiting_for_model",
});
assert.equal(correctedStream.text, "", "retry clears the previous draft even if it later fails or is cancelled");
correctedStream = applyAgentRuntimeDeltaToStreamingItem(correctedStream, {
  type: "agentRuntimeDelta", clientTurnId: "client-live", phase: "receiving_response", textDelta: "corrected\nreply",
});
assert.equal(correctedStream.text, "corrected\nreply", "a second pure-content response never appends the invalid draft");

for (const locale of ["en-US", "ja-JP", "zh-CN", "zh-TW"]) {
  const messages = JSON.parse(await readFile(resolve(import.meta.dirname, "..", "src", "locales", `${locale}.json`), "utf8"));
  assert.equal(typeof messages.chat.runtimePhase.reconnecting, "string", `${locale} must localize the bounded reconnect status`);
  assert.match(messages.chat.runtimePhase.reconnecting, /\{\{attempt\}\}/);
  assert.match(messages.chat.runtimePhase.reconnecting, /\{\{max\}\}/);
  assert.equal(typeof messages.chat.runtimePhase.reconnectingDetail, "string", `${locale} must localize safe reconnect detail`);
  assert.equal(typeof messages.chat.runtimePhase.reconnectingRetry, "string", `${locale} must localize terminal reconnect next step`);
  assert.equal(typeof messages.chat.runtimePhase.waitingForModelProgress, "string", `${locale} must localize time-only waiting progress`);
  assert.equal(typeof messages.chat.runtimePhase.waitingForModelTimeout, "string", `${locale} must clarify time-only timeout is not a retry`);
  assert.equal(typeof messages.chat.providerResponseTimedOut, "string", `${locale} must explain that timeout needs a manual retry`);
}

const card = await readFile(resolve(import.meta.dirname, "..", "src", "components", "chat", "conversation-card.tsx"), "utf8");
const controller = await readFile(resolve(import.meta.dirname, "..", "src", "hooks", "use-chat-run-controller.ts"), "utf8");
const timelinePresentationSource = await readFile(resolve(import.meta.dirname, "..", "src", "lib", "chat-timeline-presentation.ts"), "utf8");
assert.match(card, /clearInterval\(timer\)/, "terminal replacement and Stop unmount must clear the reconnect clock");
assert.match(card, /buildDurableTimelineRows[\s\S]*live: true,[\s\S]*suppressTrailingAssistantText: item\.text/,
  "the complete live answer must not be rendered a second time from the durable assistant event");
assert.match(card, /window\.setInterval\(\(\) => setStreamingNowMs\(Date\.now\(\)\), 1_000\)/,
  "the transient card advances elapsed time locally while backend events are quiet");
assert.match(card, /<details[\s\S]*<summary/, "reconnecting status must expose a compact disclosure detail");
assert.match(card, /ChevronRight[\s\S]*group-open:rotate-90/, "reconnecting disclosure must show an explicit expandable chevron");
assert.match(card, /reconnectingRetry/, "terminal reconnect status must provide a manual retry next step");
assert.doesNotMatch(controller, /projectRuntimeResponseForDisplay/, "localized display prose must not enter durable runtime history");
assert.match(controller, /materializeRuntimeTimeline\(response\)/, "persist the original runtime reply and timeline");
assert.match(timelinePresentationSource, /provider_timeout:\s*"chat\.providerResponseTimedOut"/);
assert.match(controller, /delta\.activity !== true/, "activity-only Provider events must reach the streaming reducer");
assert.match(controller, /abortController\.signal\.aborted/);
assert.match(controller, /finalizeCancelledStreamingTurn\(item, \{/,
  "Stop must replace the live card with its durable cancelled projection before transient cleanup");

console.log("chat runtime streaming contract: ok");
await fs.rm(testTemp, { recursive: true, force: true });
