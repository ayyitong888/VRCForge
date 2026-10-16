import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  buildTimelinePresentation,
  isFinalAssistantTimelineEvent,
  isFinalAssistantPresentationEntry,
  materializeRuntimeTimeline,
  projectRuntimeResponseForDisplay,
} from "../src/lib/chat-timeline-presentation.ts";
import { normalizeAgentRuntimeTimelineEvent } from "../src/lib/chat-streaming.ts";

const commentary = {
  id: "assistant-commentary-1",
  sequence: 0,
  timestamp: "2026-10-14T01:00:00.000Z",
  kind: "assistant",
  payload: {
    summary: "same public text",
    status: "done",
    phase: "commentary",
    messageId: "public-message-1",
  },
};
const duplicateCommentary = {
  ...commentary,
  id: "assistant-commentary-2",
  sequence: 1,
  payload: { ...commentary.payload, messageId: "public-message-2" },
};
const finalAnswer = {
  id: "assistant-final",
  sequence: 2,
  timestamp: "2026-10-14T01:00:02.000Z",
  kind: "assistant",
  payload: {
    summary: "final answer",
    status: "progress",
    phase: "final_answer",
    messageId: "public-message-final",
  },
};

assert.equal(isFinalAssistantTimelineEvent(commentary), false, "commentary never becomes final because status is done");
assert.equal(isFinalAssistantTimelineEvent(finalAnswer), true, "final_answer phase wins over a nonterminal status");
const normalized = normalizeAgentRuntimeTimelineEvent(commentary);
assert.equal(normalized?.payload.phase, "commentary");
assert.equal(normalized?.payload.messageId, "public-message-1");

const presentation = buildTimelinePresentation([commentary, duplicateCommentary, finalAnswer]);
assert.equal(presentation.elapsedSeconds, 2, "commentary without tools still has a real elapsed process header");
assert.equal(buildTimelinePresentation([commentary, finalAnswer], 9).elapsedSeconds, 9, "explicit turn duration wins over derived timestamps");
assert.equal(buildTimelinePresentation([finalAnswer], 9).elapsedSeconds, undefined, "a plain final reply keeps its existing presentation");
assert.deepEqual(
  presentation.entries.filter((entry) => entry.type === "assistant").map((entry) => ({
    text: entry.text,
    phase: entry.phase,
    messageId: entry.messageId,
  })),
  [
    { text: "same public text", phase: "commentary", messageId: "public-message-1" },
    { text: "same public text", phase: "commentary", messageId: "public-message-2" },
    { text: "final answer", phase: "final_answer", messageId: "public-message-final" },
  ],
  "same-text public messages remain independent and ordered",
);

const response = {
  ok: true,
  session_id: "session-phase",
  sessionId: "session-phase",
  turn_id: "turn-phase",
  turnId: "turn-phase",
  observe: {},
  plan: {
    summary: "summary",
    reply: "raw failure",
    planner: "fixture",
    shellNeeded: false,
    publicMessages: [
      { id: "public-message-1", text: "same public text", phase: "commentary" },
      { id: "public-message-final", text: "final answer", phase: "final_answer" },
    ],
    plannerFailure: { code: "provider_request_failed" },
  },
  timeline: [commentary, finalAnswer],
};
const projected = projectRuntimeResponseForDisplay(response, (key) => key === "chat.runtimeTerminal.providerRequestFailedMessage" ? "localized failure" : key);
assert.equal(projected.timeline[0].payload.summary, "same public text", "failure projection leaves commentary untouched");
assert.equal(projected.timeline[1].payload.summary, "localized failure", "failure projection targets final_answer only");
const commentaryOnlyResponse = {
  ...response,
  plan: { ...response.plan, reply: "", summary: "The turn ended without a final answer.", reason: "native_final_answer_missing" },
  timeline: [commentary],
};
const projectedCommentaryOnly = projectRuntimeResponseForDisplay(commentaryOnlyResponse, (key) => key === "chat.runtimeTerminal.providerRequestFailedMessage" ? "localized failure" : key);
assert.equal(projectedCommentaryOnly.plan.reply, "", "commentary-only projection cannot synthesize a final reply");
assert.equal(projectedCommentaryOnly.timeline[0].payload.summary, "same public text");
const timelineSource = await readFile(resolve(import.meta.dirname, "../src/components/chat/conversation-timeline.tsx"), "utf8");
assert.match(timelineSource, /native_final_answer_missing/, "missing-final reason is handled explicitly");
assert.match(timelineSource, /data-vrcforge-missing-final-answer/, "missing-final status has a stable DOM marker");

const materialized = materializeRuntimeTimeline(response);
assert.equal(materialized[0].payload.phase, "commentary");
assert.equal(materialized[0].payload.messageId, "public-message-1");

const chatStorageSource = await readFile(resolve(import.meta.dirname, "../src/lib/chat-thread.ts"), "utf8");
assert.match(chatStorageSource, /items: referencedItems/, "chat persistence keeps durable response items in its storage projection");
const continuationSource = await readFile(resolve(import.meta.dirname, "../src/hooks/use-runtime-turn-continuation.ts"), "utf8");
assert.match(continuationSource, /publicMessages/, "continuation response retains the public message contract");
assert.match(continuationSource, /reason: event\.plan\?\.reason/, "continuation response retains the missing-final reason");
const persisted = JSON.parse(JSON.stringify([{
  id: "chat-phase",
  sessionId: "session-phase",
  title: "phase fixture",
  items: [{ id: "item-phase", type: "agent", response }],
}]))[0];
assert.equal(persisted.items[0].response.timeline[0].payload.phase, "commentary", "chat storage retains assistant phase");
assert.equal(persisted.items[0].response.timeline[0].payload.messageId, "public-message-1", "chat storage retains public message identity");
console.log("chat public message phase contract: ok");

const unaccepted = { ...finalAnswer, payload: { ...finalAnswer.payload, status: "completion_unverified" } };
const accepted = { ...finalAnswer, id: "accepted-control", sequence: 3,
  timestamp: "2026-10-14T01:00:03.000Z", payload: { summary: "Accepted final.", status: "done" } };
const acceptanceResponse = { ...response, plan: { ...response.plan, reply: "Accepted final.", plannerFailure: undefined },
  timeline: [commentary, unaccepted, accepted] };
const roundtripped = JSON.parse(JSON.stringify(acceptanceResponse));
const acceptanceTimeline = materializeRuntimeTimeline(roundtripped);
assert.equal(acceptanceTimeline.length, 3, "materialization never manufactures another final");
assert.equal(isFinalAssistantTimelineEvent(unaccepted), false);
assert.equal(acceptanceTimeline[1].payload.phase, "final_answer");
assert.equal(acceptanceTimeline[1].payload.summary, "final answer");
const acceptanceEntries = buildTimelinePresentation(acceptanceTimeline).entries;
assert.deepEqual(acceptanceEntries.filter(isFinalAssistantPresentationEntry).map(e => e.text), ["Accepted final."]);
const cancelled = { ...accepted, payload: { summary: "Request cancelled.", status: "cancelled" } };
const cancelledProjection = projectRuntimeResponseForDisplay({ ...acceptanceResponse,
  plan: { ...response.plan, plannerFailure: { code: "provider_request_failed" } }, timeline: [unaccepted, cancelled] }, () => "Failure notice.");
assert.equal(cancelledProjection.timeline[0].payload.summary, "final answer", "failure projection preserves unaccepted model text");
assert.equal(cancelledProjection.timeline[1].payload.summary, "Failure notice.");
assert.match(timelineSource, /isFinalAssistantPresentationEntry\(entry\)/, "folding uses the shared final predicate");
