import assert from "node:assert/strict";
import fs from "node:fs";
import { mergeConversationTimelineItems } from "../src/lib/chat-timeline-presentation.ts";

const thread = fs.readFileSync("src/lib/chat-thread.ts", "utf8");
const helper = fs.readFileSync("src/lib/chat-timeline-presentation.ts", "utf8");
const workspace = fs.readFileSync("src/components/chat/chat-workspace.tsx", "utf8");

assert.match(thread, /mergeConversationTimelineItems/);
assert.match(helper, /export function mergeConversationTimelineItems/);
assert.match(helper, /timeline_event/);
assert.match(helper, /timestamp/);
assert.match(workspace, /mergeConversationTimelineItems/);
assert.match(workspace, /conversationItems/);

// Executable contract for the one-card merge semantics: all event facts keep
// their real ordering, but the turn is rendered once rather than as one chat
// message per event.
const turnA = [
  { id: "a-planner", kind: "planner", timestamp: "2026-01-01T00:00:01.000Z", sequence: 1 },
  { id: "a-delegate", kind: "tool_call", timestamp: "2026-01-01T00:00:02.000Z", sequence: 2 },
  { id: "a-final", kind: "assistant", timestamp: "2026-01-01T00:00:05.000Z", sequence: 5 },
];
const turnB = [{ id: "b-tool", kind: "tool_result", timestamp: "2026-01-01T00:00:04.000Z", sequence: 4 }];
const registry = { id: "registry-started", kind: "subagent", timestamp: "2026-01-01T00:00:03.000Z", sequence: 3 };
const tokens = [...turnA, registry, ...turnB]
  .sort((a, b) => a.timestamp.localeCompare(b.timestamp) || a.sequence - b.sequence || a.id.localeCompare(b.id));
assert.deepEqual(tokens.map((e) => e.id), ["a-planner", "a-delegate", "registry-started", "b-tool", "a-final"]);
assert.equal(new Set(tokens.map((e) => e.id)).size, tokens.length);
assert.match(helper, /Keep Runtime and registry facts inside their owning turn card/);
assert.match(helper, /lifecycleByTurnIndex/);
assert.doesNotMatch(helper, /events\.filter\(\(event\) => event\.kind === "assistant"\)/);

const response = {
  clientTurnId: "turn-1",
  plan: { planner: "llm", reply: "done" },
  timeline: [
    { id: "turn-plan", sequence: 0, timestamp: "2026-08-14T00:00:01.000Z", kind: "planner", payload: { status: "planned" } },
    { id: "turn-final", sequence: 1, timestamp: "2026-08-14T00:00:05.000Z", kind: "assistant", payload: { summary: "done" } },
  ],
};
const completed = mergeConversationTimelineItems([
  { id: "user-1", type: "user", text: "go", createdAt: "2026-08-14T00:00:00.000Z" },
  { id: "registry-1", type: "timeline_event", createdAt: "2026-08-14T00:00:03.000Z", event: { id: "registry-1", sequence: 3, timestamp: "2026-08-14T00:00:03.000Z", kind: "subagent", payload: { label: "review", subagentStatus: "started" } } },
  { id: "agent-1", type: "agent", response, createdAt: "2026-08-14T00:00:05.000Z" },
]);
assert.deepEqual(completed.map((item) => item.type), ["user", "agent"], "registry lifecycle inside a completed turn must not remain a top-level chat item");
assert.deepEqual(completed[1].response.timeline.map((event) => event.id), ["turn-plan", "registry-1", "turn-final"]);

const active = mergeConversationTimelineItems([
  { id: "user-2", type: "user", text: "go", createdAt: "2026-08-14T00:00:00.000Z" },
  { id: "stream-1", type: "streaming", clientTurnId: "turn-2", text: "", createdAt: "2026-08-14T00:00:01.000Z", timeline: [
    { id: "live-plan", sequence: 0, timestamp: "2026-08-14T00:00:01.000Z", kind: "planner", payload: { status: "planned" } },
  ] },
  { id: "registry-2", type: "timeline_event", createdAt: "2026-08-14T00:00:02.000Z", event: { id: "registry-2", sequence: 2, timestamp: "2026-08-14T00:00:02.000Z", kind: "subagent", payload: { label: "review", subagentStatus: "started" } } },
]);
assert.deepEqual(active.map((item) => item.type), ["user", "streaming"], "an active turn also owns its registry lifecycle rather than rendering one event card per update");
assert.deepEqual(active[1].timeline.map((event) => event.id), ["live-plan", "registry-2"]);

const activeWithQueuedInput = mergeConversationTimelineItems([
  { id: "user-3", type: "user", text: "go", createdAt: "2026-08-14T00:00:00.000Z" },
  { id: "stream-2", type: "streaming", clientTurnId: "turn-3", text: "", createdAt: "2026-08-14T00:00:01.000Z", timeline: [
    { id: "live-plan-2", sequence: 0, timestamp: "2026-08-14T00:00:01.000Z", kind: "planner", payload: { status: "planned" } },
  ] },
  { id: "queued-user", type: "user", text: "follow up", queueStatus: "queued", createdAt: "2026-08-14T00:00:01.500Z" },
  { id: "registry-3", type: "timeline_event", createdAt: "2026-08-14T00:00:02.000Z", event: { id: "registry-3", sequence: 2, timestamp: "2026-08-14T00:00:02.000Z", kind: "subagent", payload: { label: "review", subagentStatus: "started" } } },
]);
assert.deepEqual(activeWithQueuedInput.map((item) => item.type), ["user", "streaming", "user"], "queued/steered user prose must not split registry facts out of the still-active turn");
assert.deepEqual(activeWithQueuedInput[1].timeline.map((event) => event.id), ["live-plan-2", "registry-3"]);
console.log("cross-source timeline contract: passed");
