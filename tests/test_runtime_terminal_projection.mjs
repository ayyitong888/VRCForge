import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  buildTimelinePresentation,
  projectRuntimeResponseForDisplay,
  runtimeTerminalStatusKey,
} from "../src/lib/chat-timeline-presentation.ts";

const rawAssistantText = "opaque backend terminal body that must not be used for presentation";
const response = {
  turnId: "turn-no-progress",
  observe: {},
  plan: {
    summary: rawAssistantText,
    reply: rawAssistantText,
    planner: "llm",
    shellNeeded: false,
    nextStep: "planner_failed",
    plannerFailure: {
      code: "planner_no_progress",
      phase: "post_tool",
      retryable: true,
    },
  },
  timeline: [
    {
      id: "timeline-command-first",
      sequence: 0,
      timestamp: "2026-08-14T00:00:00.000Z",
      kind: "command",
      payload: { status: "ok", summary: "an earlier semantically repeated inspection completed" },
    },
    {
      id: "timeline-assistant-final",
      sequence: 1,
      timestamp: "2026-08-14T00:00:01.000Z",
      kind: "assistant",
      payload: { status: "planner_failed", summary: rawAssistantText },
    },
  ],
};

const translations = {
  "chat.runtimeTerminal.noProgressMessage": "LOCALIZED_NO_PROGRESS_FAILED_NOT_COMPLETE",
  "chat.providerResponseTimedOut": "LOCALIZED_PROVIDER_TIMEOUT",
};
const localized = projectRuntimeResponseForDisplay(response, (key) => translations[key] || key);
assert.equal(localized.plan.reply, translations["chat.runtimeTerminal.noProgressMessage"]);
assert.equal(localized.timeline[0].payload.summary, response.timeline[0].payload.summary,
  "earlier execution evidence must remain intact");
assert.equal(localized.timeline.at(-1).payload.summary, translations["chat.runtimeTerminal.noProgressMessage"],
  "the final assistant display must use the structured reason code, not raw backend prose");
assert.doesNotMatch(localized.timeline.at(-1).payload.summary, /no repeated command/i,
  "terminal presentation must not make a global claim contradicted by earlier timeline evidence");
assert.equal(runtimeTerminalStatusKey("planner_no_progress"),
  "chat.runtimeTerminal.noProgressStatus");
const missingAssistantStatus = localized.timeline.map((event) => event.kind === "assistant"
  ? { ...event, payload: { summary: event.payload.summary } }
  : event);
const missingStatusAssistantEntry = buildTimelinePresentation(missingAssistantStatus).entries
  .find((entry) => entry.type === "assistant");
assert.equal(missingStatusAssistantEntry.status, "done",
  "a fresh merged assistant event may arrive without its terminal status");
assert.equal(
  runtimeTerminalStatusKey(localized.plan.plannerFailure.code),
  "chat.runtimeTerminal.noProgressStatus",
  "structured plannerFailure.code alone must preserve the Runtime terminal card",
);
assert.equal(runtimeTerminalStatusKey("future_failure"), undefined,
  "an unknown structured failure must not be misclassified as no progress");

const providerTimeout = projectRuntimeResponseForDisplay({
  ...response,
  plan: {
    ...response.plan,
    plannerFailure: { code: "provider_timeout", phase: "post_tool", retryable: true },
  },
}, (key) => translations[key] || key);
assert.equal(providerTimeout.plan.reply, translations["chat.providerResponseTimedOut"]);
assert.equal(providerTimeout.timeline.at(-1).payload.summary, translations["chat.providerResponseTimedOut"]);

const unknownResponse = {
  ...response,
  plan: { ...response.plan, plannerFailure: { code: "future_failure" } },
};
const unknown = projectRuntimeResponseForDisplay(unknownResponse, (key) => translations[key] || key);
assert.equal(unknown, unknownResponse, "unknown structured failures retain their existing projection");
assert.equal(unknown.plan.reply, rawAssistantText);

for (const locale of ["en-US", "ja-JP", "zh-CN", "zh-TW"]) {
  const messages = JSON.parse(await readFile(resolve(import.meta.dirname, "..", "src", "locales", `${locale}.json`), "utf8"));
  assert.equal(typeof messages.chat.runtimeTerminal.noProgressMessage, "string",
    `${locale} must localize the no-progress terminal message`);
  assert.equal(typeof messages.chat.runtimeTerminal.noProgressStatus, "string",
    `${locale} must localize the explicit failed/not-complete status`);
}

const timelineComponent = await readFile(
  resolve(import.meta.dirname, "..", "src", "components", "chat", "conversation-timeline.tsx"),
  "utf8",
);
const conversationCard = await readFile(
  resolve(import.meta.dirname, "..", "src", "components", "chat", "conversation-card.tsx"),
  "utf8",
);
assert.match(timelineComponent, /data-vrcforge-terminal-status/,
  "a no-progress failure must not look like an ordinary assistant answer");
const terminalBranch = timelineComponent.slice(
  timelineComponent.indexOf("const terminalStatusKey"),
  timelineComponent.indexOf("continue;", timelineComponent.indexOf("const terminalStatusKey")),
);
assert.match(terminalBranch, /role="status"/,
  "no-progress semantics must render as a structured Runtime terminal card");
assert.doesNotMatch(
  terminalBranch.slice(terminalBranch.indexOf("terminalStatusKey ? ("), terminalBranch.indexOf(") : (")),
  /ChatMarkdown/,
  "the structured failure must stay outside the ordinary assistant-reply renderer",
);
assert.match(conversationCard, /onCopyItem\?\.\(\{ \.\.\.item, response \}\)/,
  "copy must use the same localized projection that the restored card displays");

console.log("runtime terminal projection contract: ok");
