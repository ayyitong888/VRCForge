import assert from "node:assert/strict";
import { build } from "esbuild";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const root = process.cwd();
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-capture-projection-"));
const bundle = path.join(temp, "projection.mjs");
await build({
  stdin: {
    contents: `export { capturePreviewsForQuestion } from ${JSON.stringify(path.join(root, "src/lib/chat-timeline-presentation.ts"))};`,
    resolveDir: root,
    sourcefile: "question-capture-entry.mjs",
  },
  outfile: bundle,
  bundle: true,
  format: "esm",
  platform: "node",
});

const { capturePreviewsForQuestion } = await import(pathToFileURL(bundle));
const uri = "vrcforge://scene-capture-preview/11111111-1111-4111-8111-111111111111?context=runtime%3Asession-a&revision=1&store=11111111111111111111111111111111";
const otherUri = "vrcforge://scene-capture-preview/22222222-2222-4222-8222-222222222222?context=runtime%3Asession-b&revision=1&store=22222222222222222222222222222222";
const capture = (resourceUri, angle) => ({
  id: `capture-${angle}`,
  sequence: 1,
  kind: "tool_result",
  payload: { captures: [{ angle, preview: { schema: "vrcforge.scene_capture_preview.v1", mimeType: "image/png", captureId: "capture", resourceUri } }] },
});
const agent = (clientTurnId, sessionId, timeline) => ({
  id: `agent-${clientTurnId}`,
  type: "agent",
  response: { ok: true, sessionId, session_id: sessionId, clientTurnId, turnId: clientTurnId, turn_id: clientTurnId, observe: {}, plan: { summary: "" }, timeline },
});

const current = { questionId: "new", originClientTurnId: "turn-new:question:q1", sessionId: "session-a" };
assert.deepEqual(capturePreviewsForQuestion(current, [agent("turn-new", "session-a", [capture(uri, "front")])]), [
  { resourceUri: uri, label: "front", sessionId: "session-a" },
], "new visual checkpoints reuse their own approved capture resource");
assert.deepEqual(capturePreviewsForQuestion(current, [agent("turn-other", "session-a", [capture(otherUri, "back")])]), [], "unrelated turns never provide a latest-image fallback");
assert.deepEqual(capturePreviewsForQuestion({ questionId: "none", originClientTurnId: "turn-new:question:q2", sessionId: "session-a" }, [agent("turn-new", "session-a", [])]), [], "questions without a capture remain text-only");
assert.deepEqual(capturePreviewsForQuestion({ questionId: "cross", originClientTurnId: "turn-new:question:q3", sessionId: "session-b" }, [agent("turn-new", "session-a", [capture(uri, "front")])]), [], "same client turn in another session is rejected");
await fs.rm(temp, { recursive: true, force: true });
console.log("question capture projection: new/old turn binding, no-image fallback, and cross-session isolation passed");
