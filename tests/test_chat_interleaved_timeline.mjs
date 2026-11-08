import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { buildTimelinePresentation } from "../src/lib/chat-timeline-presentation.ts";
import { projectConversationSteeringMessages } from "../src/lib/chat-steering-projection.ts";

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

const questionId = "question_visual_checkpoint";
const questionTurnId = `initial:question:${questionId}:feedback:2`;
const time = (second) => `2026-11-08T16:09:${String(second).padStart(2, "0")}Z`;
const answer = { id: `question-answer-${questionId}-2`, type: "question_answer", questionId, revision: 2, text: "Continue with the outfit", updatedAt: time(3) };
const steer = (id, second, state = {}) => ({
  id: `user-${id}`, type: "user", clientTurnId: id, text: id, queuedFrom: true, createdAt: time(second), ...state,
});
const priorFeedback = { ...answer, id: `question-answer-${questionId}-1`, revision: 1, updatedAt: time(0) };
const previousSteer = steer("previous", 1);
const consumedSteer = steer("consumed", 10);
const pendingSteer = steer("pending", 11, { queueStatus: "steering" });
const queuedFollowup = steer("queued", 12, { queueStatus: "queued", composerQueued: true });
const laterAnswer = { ...answer, id: `question-answer-${questionId}-3`, revision: 3, updatedAt: time(19) };
const laterSteer = steer("next-feedback", 20);
const stream = { id: `question-continuation:${questionTurnId}`, type: "streaming", clientTurnId: questionTurnId, text: "", createdAt: time(4) };
for (const [label, items] of [
  ["live", [priorFeedback, previousSteer, answer, stream, consumedSteer, pendingSteer, queuedFollowup, laterAnswer, laterSteer]],
  ["restored live", [priorFeedback, previousSteer, answer, consumedSteer, pendingSteer, queuedFollowup, stream, laterAnswer, laterSteer]],
  ["completed", [priorFeedback, previousSteer, answer, { ...consumedSteer, queuedFrom: false }, pendingSteer, queuedFollowup, {
    id: "question-reply", type: "agent", response: {
      clientTurnId: questionTurnId, continuationSource: "question_answered", consumedSteerInputIds: ["consumed"], startedAt: time(4), completedAt: time(18),
    },
  }, laterAnswer, laterSteer]],
]) {
  const projection = projectConversationSteeringMessages(items);
  assert.deepEqual(projection.messagesByTurnId.get(questionTurnId)?.map((item) => item.clientTurnId), ["consumed"], `${label}: only consumed messages from this feedback revision join its reply`);
  assert.deepEqual(projection.items.map((item) => item.id), items.filter((item) => item.id !== consumedSteer.id).map((item) => item.id), `${label}: keep the answer and pending or unrelated messages in their original positions`);
  assert.equal(projection.initialUsersByTurnId.has(questionTurnId), false, "the visual answer stays a standalone question answer");
}

console.log("chat interleaved timeline regression: passed");
