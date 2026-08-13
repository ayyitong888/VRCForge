import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  buildTimelinePresentation,
  hasDurableExecutionEvents,
} from "../src/lib/chat-timeline-presentation.ts";

const source = await readFile(resolve(import.meta.dirname, "..", "src", "components", "chat", "conversation-timeline.tsx"), "utf8");
const helper = await readFile(resolve(import.meta.dirname, "..", "src", "lib", "chat-timeline-presentation.ts"), "utf8");
assert.match(source, /buildTimelinePresentation/);
assert.match(helper, /materializeInvocations/);
assert.match(helper, /invocationIdentity/, "call and result must merge into one invocation row");
assert.match(source, /commandBatch/);
assert.match(source, /toolBatch/);
assert.match(source, /subagentBatch/);
assert.match(source, /data-agent-turn-duration/, "one turn-level elapsed marker must sit above semantic batches");
assert.match(source, /workSegment/);
assert.match(source, /workSegmentElapsed/);
assert.match(helper, /timestamp/);
assert.match(helper, /tool_call|tool_result|file_edit|command|subagent/);
assert.match(helper, /finalAssistant/, "only the final assistant answer stays expanded");
assert.doesNotMatch(helper, /event\.kind === "planner" \|\| event\.kind === "assistant"/, "planner commentary belongs inside the collapsed work segment");
assert.doesNotMatch(source, /<time|toLocaleTimeString/, "expanded invocation rows must not repeat timestamps");

const timeline = [
  { id: "plan-1", sequence: 9, timestamp: "2026-08-14T00:00:01.000Z", kind: "planner", payload: { label: "Working", summary: "first safe update" } },
  { id: "call-a", sequence: 8, timestamp: "2026-08-14T00:00:02.000Z", kind: "tool_call", payload: { actionId: "action-a", tool: "vrcforge_list_directory", status: "started" } },
  { id: "result-a", sequence: 7, timestamp: "2026-08-14T00:00:03.000Z", kind: "tool_result", payload: { actionId: "action-a", tool: "vrcforge_list_directory", status: "ok", summary: "listed" } },
  { id: "plan-2", sequence: 6, timestamp: "2026-08-14T00:00:04.000Z", kind: "planner", payload: { label: "Working", summary: "second safe update" } },
  { id: "call-b", sequence: 5, timestamp: "2026-08-14T00:00:05.000Z", kind: "tool_call", payload: { actionId: "action-b", tool: "vrcforge_find_files", status: "started" } },
  { id: "result-b", sequence: 4, timestamp: "2026-08-14T00:00:06.000Z", kind: "tool_result", payload: { actionId: "action-b", tool: "vrcforge_find_files", status: "ok", summary: "found" } },
  { id: "command-c", sequence: 3, timestamp: "2026-08-14T00:00:07.000Z", kind: "command", payload: { actionId: "action-c", label: "rg -n pattern", status: "ok" } },
  { id: "call-d", sequence: 3, timestamp: "2026-08-14T00:00:08.000Z", kind: "tool_call", payload: { actionId: "action-d", tool: "vrcforge_read_text_file", status: "started" } },
  { id: "result-d", sequence: 3, timestamp: "2026-08-14T00:00:08.500Z", kind: "tool_result", payload: { actionId: "action-d", tool: "vrcforge_read_text_file", status: "ok" } },
  { id: "assistant", sequence: 2, timestamp: "2026-08-14T00:00:09.000Z", kind: "assistant", payload: { summary: "final answer", status: "done" } },
  // A repeated live event with the same id must never become another row.
  { id: "call-a", sequence: 1, timestamp: "2026-08-14T00:00:02.000Z", kind: "tool_call", payload: { actionId: "action-a", tool: "vrcforge_list_directory", status: "started" } },
];
const presentation = buildTimelinePresentation(timeline, 42);
assert.equal(presentation.elapsedSeconds, 42, "the whole Agent turn owns one elapsed value");
assert.deepEqual(presentation.entries.map((entry) => entry.type === "batch" ? entry.kind : entry.type), ["process", "tool", "command", "tool", "assistant"], "an interleaved command splits tool batches instead of falsifying cross-kind chronology");
const toolBatch = presentation.entries.find((entry) => entry.type === "batch" && entry.kind === "tool");
assert.ok(toolBatch && toolBatch.type === "batch");
assert.deepEqual(toolBatch.invocations.map((entry) => entry.actionId), ["action-a", "action-b"], "tool invocations retain occurrence order inside one batch");
assert.deepEqual(toolBatch.invocations.map((entry) => entry.status), ["ok", "ok"], "call/result merge into one terminal invocation each");
const lastToolBatch = presentation.entries.findLast((entry) => entry.type === "batch" && entry.kind === "tool");
assert.deepEqual(lastToolBatch.invocations.map((entry) => entry.actionId), ["action-d"]);
assert.equal(presentation.entries.filter((entry) => entry.type === "assistant").length, 1);
assert.equal(hasDurableExecutionEvents(timeline), true);
assert.equal(hasDurableExecutionEvents(timeline.filter((event) => ["planner", "assistant"].includes(event.kind))), false);
const directAnswer = buildTimelinePresentation([
  { id: "answer-only", sequence: 0, timestamp: "2026-08-14T00:00:09.000Z", kind: "assistant", payload: { summary: "short answer" } },
], 0.2);
assert.equal(directAnswer.elapsedSeconds, undefined, "a direct answer must not add a noisy Handled in 0s marker");
console.log("chat work segments contract: ok");
