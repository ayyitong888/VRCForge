import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const temp = await fs.mkdtemp(path.join(root, "tests", ".question-answer-persistence-"));
try {
  const output = path.join(temp, "modules.mjs");
  const entry = path.join(temp, "entry.mjs");
  await fs.writeFile(entry, `
    export { questionAnswerItem } from ${JSON.stringify(path.join(root, "src/lib/question-answer.ts"))};
    export { filterPersistableChats } from ${JSON.stringify(path.join(root, "src/lib/chat-thread.ts"))};
    export { buildChatHistory } from ${JSON.stringify(path.join(root, "src/lib/conversation-utils.ts"))};
    export { collectCompactedAttachmentReferences, resolveAttachmentPayloadReferences } from ${JSON.stringify(path.join(root, "src/lib/attachment-payloads.ts"))};
  `);
  await build({ entryPoints: [entry], outfile: output, bundle: true, format: "esm", platform: "node" });
  const { questionAnswerItem, filterPersistableChats, buildChatHistory, collectCompactedAttachmentReferences, resolveAttachmentPayloadReferences } = await import(pathToFileURL(output).href);
  const answer = questionAnswerItem({ questionId: "q-persist", header: "Review image", question: "What do you see?" }, "The left side is clear.", [
    { id: "img-1", name: "review.png", size: 42, type: "image/png", dataUrl: "data:image/png;base64,AA==" },
  ]);
  const chat = {
    id: "chat-original", sessionId: "session-original", title: "Original task", projectPath: "",
    items: [
      { id: "user-original", type: "user", text: "Inspect the preview", createdAt: "2026-10-23T01:00:00Z" },
      answer,
    ],
  };
  const stored = filterPersistableChats([chat])[0];
  const storedAnswer = stored.items.find((item) => item.type === "question_answer");
  assert.ok(storedAnswer, "answer survives chat persistence");
  assert.equal(storedAnswer.questionId, "q-persist");
  assert.equal(storedAnswer.text, "The left side is clear.");
  assert.equal(storedAnswer.header, "Review image", "a concise supplied question header labels the answer");
  const answerWithoutHeader = questionAnswerItem({ questionId: "q-no-header" }, "An answer");
  assert.equal(answerWithoutHeader.header, undefined, "an absent short header does not copy the full question into the answer row");
  const feedbackOne = questionAnswerItem({ questionId: "q-revision", questionFeedbackRevision: 1, updatedAt: "2026-10-23T01:01:00.000001+00:00" }, "Not sure yet");
  const sameReplay = questionAnswerItem({ questionId: "q-revision", questionFeedbackRevision: 1, updatedAt: "2026-10-23T01:01:00.000001+00:00" }, "Not sure yet");
  const feedbackTwo = questionAnswerItem({ questionId: "q-revision", questionFeedbackRevision: 2, updatedAt: "2026-10-23T01:02:00.000001+00:00" }, "Pass", [
    { id: "img-final", name: "final.png", size: 42, type: "image/png", dataUrl: "data:image/png;base64,AA==" },
  ]);
  assert.equal(feedbackOne.id, sameReplay.id, "same backend answer event replays to the same stable item identity");
  assert.notEqual(feedbackOne.id, feedbackTwo.id, "a new feedback revision remains visible instead of being dropped as a duplicate");
  assert.equal(feedbackTwo.revision, 2);
  assert.equal(feedbackTwo.text, "Pass");
  assert.equal(feedbackTwo.attachments[0].name, "final.png", "the last revision retains its own attachments");
  assert.equal(storedAnswer.attachments[0].dataUrl, undefined, "attachment body is moved out of transcript JSON");
  assert.ok(storedAnswer.attachments[0].payloadHash);
  const restored = JSON.parse(JSON.stringify(stored));
  const restoredAnswer = restored.items.find((item) => item.type === "question_answer");
  const hydrated = resolveAttachmentPayloadReferences(restoredAnswer.attachments, restored.attachmentPayloads);
  assert.equal(hydrated[0].dataUrl, "data:image/png;base64,AA==", "answer attachment survives reload from its chat vault");
  const compactedVault = {};
  const refs = collectCompactedAttachmentReferences([answer], compactedVault);
  assert.equal(refs.length, 1, "question answer attachment survives compaction archival");
  assert.ok(compactedVault[refs[0].payloadHash]);
  const history = buildChatHistory(chat.items, (key) => key);
  assert.deepEqual(history.map((entry) => entry.text), ["Inspect the preview"], "visible answer is not duplicated into provider continuation history");
  const switchedSession = { id: "other-chat", sessionId: "session-other", title: "Other task", projectPath: "", items: [] };
  assert.deepEqual(filterPersistableChats([chat, switchedSession]).map((item) => item.sessionId), ["session-original", "session-other"]);
  assert.equal(filterPersistableChats([chat, switchedSession])[1].items.some((item) => item.type === "question_answer"), false, "answer remains scoped to its original chat");
  console.log("question answer persistence: reload, attachment vault, compaction archive, provider-history exclusion, and session isolation passed");
} finally {
  const target = await fs.realpath(temp);
  const testRoot = await fs.realpath(path.join(root, "tests"));
  assert.ok(target.startsWith(testRoot + path.sep), "cleanup stays under tests");
  await fs.rm(target, { recursive: true, force: true });
}
