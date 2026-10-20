import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { buildTimelinePresentation } from "../src/lib/chat-timeline-presentation.ts";

const timelineSource = await readFile(resolve(import.meta.dirname, "..", "src/components/chat/conversation-timeline.tsx"), "utf8");
const timeline = [
  { id: "public-1", sequence: 0, timestamp: "2026-08-14T00:02:00.000Z", kind: "assistant", payload: { summary: "public progress 1", status: "progress" } },
  { id: "call-1", sequence: 1, timestamp: "2026-08-14T00:02:01.000Z", kind: "tool_call", payload: { actionId: "interleaved-1", tool: "vrcforge_list_directory", status: "started" } },
  { id: "result-1", sequence: 2, timestamp: "2026-08-14T00:02:02.000Z", kind: "tool_result", payload: { actionId: "interleaved-1", tool: "vrcforge_list_directory", status: "ok", summary: "listed" } },
  { id: "public-2", sequence: 3, timestamp: "2026-08-14T00:02:03.000Z", kind: "assistant", payload: { summary: "public progress 2", status: "progress" } },
  { id: "call-2", sequence: 4, timestamp: "2026-08-14T00:02:04.000Z", kind: "tool_call", payload: { actionId: "interleaved-2", tool: "vrcforge_find_files", status: "started" } },
  { id: "result-2", sequence: 5, timestamp: "2026-08-14T00:02:05.000Z", kind: "tool_result", payload: { actionId: "interleaved-2", tool: "vrcforge_find_files", status: "ok", summary: "found" } },
  { id: "public-final", sequence: 6, timestamp: "2026-08-14T00:02:06.000Z", kind: "assistant", payload: { summary: "final summary", status: "done" } },
];

const presentation = buildTimelinePresentation(timeline);
assert.deepEqual(
  presentation.entries.map((entry) => entry.type === "batch" ? entry.kind : `${entry.type}:${entry.text}`),
  ["assistant:public progress 1", "tool", "assistant:public progress 2", "tool", "assistant:final summary"],
  "all public assistant updates must remain in their original timeline order",
);

const assistantBranch = timelineSource.slice(
  timelineSource.indexOf('if (entry.type === "assistant")'),
  timelineSource.indexOf("for (const invocation", timelineSource.indexOf('if (entry.type === "assistant")')),
);
assert.match(assistantBranch, /processRows\.push/, "a public progress update must remain inside the process fold before the final summary");
assert.match(timelineSource, /renderFinalAssistantRow/, "the final summary owns a separate visible row");

console.log("chat interleaved timeline regression: passed");
