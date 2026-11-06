import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";
import {
  buildTimelinePresentation,
  materializeRuntimeTimeline,
} from "../src/lib/chat-timeline-presentation.ts";

const temp = await fs.mkdtemp(path.resolve("tests/.tmp-output-truncation-"));
try {
  const bundle = path.join(temp, "runtime-continuation.mjs");
  await build({
    entryPoints: [path.resolve("src/hooks/use-runtime-turn-continuation.ts")],
    outfile: bundle,
    bundle: true,
    format: "esm",
    platform: "node",
    external: ["react"],
  });
  const { responseFromRuntimeTurnEvent } = await import(pathToFileURL(bundle).href);

  const hiddenContinuation = "上条回复因长度上限中断，请从断点直接继续，不要重复已写内容。";
  const mergedReply = "First paragraph.\n\nSecond paragraph with the recovered ending.";
  const event = {
    schema: "vrcforge.runtime_turn_event.v1",
    continuationSource: "foreground_completed",
    sessionId: "session-truncation",
    turnId: "turn-truncation",
    clientTurnId: "client-truncation",
    plan: {
      summary: mergedReply,
      reply: mergedReply,
      planner: "llm",
      nextStep: "done",
      publicMessages: [{ id: "final-merged", phase: "final_answer", text: mergedReply }],
    },
    timeline: [{
      id: "timeline-final-merged",
      sequence: 0,
      timestamp: "2026-11-07T00:00:00.000Z",
      kind: "assistant",
      payload: { summary: mergedReply, status: "done", phase: "final_answer", messageId: "final-merged" },
    }],
  };

  // The completion event recovery path must preserve the merged final contract.
  const recovered = responseFromRuntimeTurnEvent(event);
  assert.equal(recovered.plan.reply, mergedReply);
  assert.deepEqual(recovered.plan.publicMessages, event.plan.publicMessages);
  assert.deepEqual(recovered.timeline, event.timeline);

  // Exercise the same timeline projection consumed by the conversation card.
  const projected = materializeRuntimeTimeline(recovered);
  const presentation = buildTimelinePresentation(projected);
  const finals = presentation.entries.filter(entry => entry.type === "assistant" && entry.phase === "final_answer");
  assert.equal(finals.length, 1, "one accepted merged final becomes one visible assistant entry");
  assert.equal(finals[0].text, mergedReply);
  assert.equal(projected.map(entry => entry.payload.summary || "").join("\n"), mergedReply);
  assert.equal(JSON.stringify({ response: recovered, projected, presentation }).includes(hiddenContinuation), false,
    "hidden continuation instructions never enter the public completion event or UI projection");
} finally {
  await fs.rm(temp, { recursive: true, force: true });
}

console.log("output truncation presentation and recovery: ok");
