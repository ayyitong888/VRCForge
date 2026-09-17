import assert from "node:assert/strict";
import {
  buildTimelinePresentation,
  materializeRuntimeTimeline,
  projectRuntimeResponseForDisplay,
} from "../src/lib/chat-timeline-presentation.ts";

const reply = "Versions:\n\n- React: `^18.3.1`\n- Vite: `^6.4.3`\n\n```json\n{\n  \"ok\": true\n}\n```\n\n" + "Evidence.\n".repeat(200);
const response = {
  turnId: "formatted-final",
  plan: { reply, nextStep: "done" },
  timeline: [
    { id: "tool", sequence: 0, timestamp: "2026-09-17T00:00:00Z", kind: "tool_result", payload: { summary: "x".repeat(1500) } },
    { id: "final", sequence: 1, timestamp: "2026-09-17T00:00:01Z", kind: "assistant", payload: { summary: reply, status: "done" } },
  ],
};
const timeline = materializeRuntimeTimeline(JSON.parse(JSON.stringify(response)));
assert.equal(timeline[0].payload.summary.length, 1000, "tool summary retains its existing bound");
assert.equal(timeline[1].payload.summary, reply, "final body retains paragraphs, lists, fenced code and content beyond 1000 chars");
assert.equal(buildTimelinePresentation(timeline).entries.find(e => e.type === "assistant").text, reply);
const oversized = structuredClone(response);
oversized.timeline[1].payload.summary = "line\n".repeat(10000);
const bounded = materializeRuntimeTimeline(oversized)[1].payload.summary;
assert.equal(bounded.length, 32000);
assert.equal(bounded, oversized.timeline[1].payload.summary.slice(0, 31999) + "…");
const failed = projectRuntimeResponseForDisplay({
  ...response, plan: { ...response.plan, plannerFailure: { code: "planner_no_progress" } },
}, () => "Stopped without progress.\nTry a different request.");
assert.equal(buildTimelinePresentation(materializeRuntimeTimeline(failed)).entries.find(e => e.type === "assistant").text,
  "Stopped without progress.\nTry a different request.", "structured terminal projection remains authoritative");
console.log("final answer formatting: ok");
