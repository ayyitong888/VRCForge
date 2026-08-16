import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  buildRuntimeStepResultQueues,
  buildTimelinePresentation,
  hasDurableExecutionEvents,
  timelineInvocationDisplayLabel,
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
assert.match(source, /renderDirectTimelineInvocation/, "single invocations must not hide behind a generic semantic batch");
assert.match(source, /entry\.kind === "process"/, "safe planner commentary must render directly even when adjacent");
assert.match(source, /data-agent-timeline-invocation="process"/, "non-CoT planner commentary owns a visible timeline row");
assert.match(source, /<ChatMarkdown text=\{commentary\}/, "safe planner commentary must retain its complete projected text");
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
assert.deepEqual(presentation.entries.map((entry) => entry.type === "batch" ? entry.kind : entry.type), ["process", "tool", "process", "tool", "command", "tool", "assistant"], "every intervening status segment splits semantic batches so the visible rows retain chronology");
const toolBatches = presentation.entries.filter((entry) => entry.type === "batch" && entry.kind === "tool");
assert.deepEqual(toolBatches.map((batch) => batch.invocations.map((entry) => entry.actionId)), [["action-a"], ["action-b"], ["action-d"]], "every safe update splits tool batches at its real occurrence");
assert.deepEqual(toolBatches.map((batch) => batch.invocations.map((entry) => entry.status)), [["ok"], ["ok"], ["ok"]], "call/result pairs merge into one terminal invocation each");
assert.equal(presentation.entries.filter((entry) => entry.type === "assistant").length, 1);
assert.equal(hasDurableExecutionEvents(timeline), true);
assert.equal(hasDurableExecutionEvents(timeline.filter((event) => ["planner", "assistant"].includes(event.kind))), false);
assert.equal(timelineInvocationDisplayLabel("vrcforge_list_directory"), "list_directory");
assert.equal(timelineInvocationDisplayLabel("vrcforge_delegate_subagent"), "delegate_subagent");
assert.equal(timelineInvocationDisplayLabel("vrcforge_progress_update"), "progress_update");
assert.equal(timelineInvocationDisplayLabel("vrcforge_ask_user"), "ask_user");
assert.equal(timelineInvocationDisplayLabel("vrcforge_execute_shell"), "execute_shell");
assert.equal(timelineInvocationDisplayLabel("vrcforge_shell_process"), "shell_process");
assert.equal(timelineInvocationDisplayLabel("vrcforge_inspect_chat_attachment"), "inspect_chat_attachment");
assert.equal(timelineInvocationDisplayLabel("vrcforge_vision_audit"), "vision_audit");
assert.equal(timelineInvocationDisplayLabel("vrcforge_unity_status"), "unity_status", "Unity-specific tools use the explicit unity_ capability namespace");
assert.equal(timelineInvocationDisplayLabel("vrcforge_scan_materials"), "unity_scan_materials", "Unity tools without a legacy unity_ segment still receive the capability namespace");

const repeatedAction = buildTimelinePresentation([
  { id: "repeat-call-1", sequence: 0, timestamp: "2026-08-14T00:01:00.000Z", kind: "tool_call", payload: { actionId: "same-action", tool: "vrcforge_read_text_file", status: "started" } },
  { id: "repeat-result-1", sequence: 1, timestamp: "2026-08-14T00:01:01.000Z", kind: "tool_result", payload: { actionId: "same-action", tool: "vrcforge_read_text_file", status: "ok", summary: "first" } },
  { id: "other-call", sequence: 2, timestamp: "2026-08-14T00:01:02.000Z", kind: "tool_call", payload: { actionId: "other-action", tool: "vrcforge_find_files", status: "started" } },
  { id: "other-result", sequence: 3, timestamp: "2026-08-14T00:01:03.000Z", kind: "tool_result", payload: { actionId: "other-action", tool: "vrcforge_find_files", status: "ok", summary: "other" } },
  { id: "repeat-call-2", sequence: 4, timestamp: "2026-08-14T00:01:04.000Z", kind: "tool_call", payload: { actionId: "same-action", tool: "vrcforge_read_text_file", status: "started" } },
  { id: "repeat-result-2", sequence: 5, timestamp: "2026-08-14T00:01:05.000Z", kind: "tool_result", payload: { actionId: "same-action", tool: "vrcforge_read_text_file", status: "ok", summary: "second" } },
]);
const repeatedInvocations = repeatedAction.entries.flatMap((entry) => entry.type === "batch" ? entry.invocations : []);
assert.deepEqual(repeatedInvocations.map((entry) => entry.actionId), ["same-action", "other-action", "same-action"], "A -> B -> A remains three chronological invocations");
assert.deepEqual(repeatedInvocations.map((entry) => entry.summary), ["first", "other", "second"], "repeated action ids must not collapse into the last result");

const tailMarker = "TAIL_MARKER_AFTER_1000";
const resultQueues = buildRuntimeStepResultQueues([
  { actionId: "same-action", result: { text: "x".repeat(1200) + tailMarker } },
  { actionId: "other-action", result: { text: "other" } },
  { actionId: "same-action", result: { text: "second-result" } },
]);
assert.equal(resultQueues.get("same-action")?.length, 2, "reused action ids retain one full result per occurrence");
assert.match(resultQueues.get("same-action")?.shift()?.text || "", new RegExp(`${tailMarker}$`), "the expanded result keeps content beyond the safe 1000-character timeline summary");
assert.equal(resultQueues.get("same-action")?.shift()?.text, "second-result", "reused action results are consumed in chronological order");
const directAnswer = buildTimelinePresentation([
  { id: "answer-only", sequence: 0, timestamp: "2026-08-14T00:00:09.000Z", kind: "assistant", payload: { summary: "short answer" } },
], 0.2);
assert.equal(directAnswer.elapsedSeconds, undefined, "a direct answer must not add a noisy Handled in 0s marker");
console.log("chat work segments contract: ok");
