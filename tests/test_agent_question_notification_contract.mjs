import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("question notification is display-only and carries exact chat/question identity", async () => {
  const rust = await readFile(path.join(root, "src-tauri/src/approval_notification_windows.rs"), "utf8");
  assert.match(rust, /show_agent_question_notification/);
  assert.match(rust, /vrcforge-agent-question-notification-action/);
  assert.match(rust, /question_id: request\.question_id\.clone\(\)/);
  assert.match(rust, /chat_id: request\.chat_id\.clone\(\)/);
  assert.match(rust, /request\.silent/);
  assert.doesNotMatch(rust, /approve_agent_approval|answer_agent_question/);

  const source = await readFile(path.join(root, "src/lib/agent-question-notifications.ts"), "utf8");
  assert.match(source, /vrcforge\.agent-question\.notifications\.v1/);
  assert.match(source, /shouldNotifyAgentQuestion/);
  assert.match(source, /show_agent_question_notification/);
  assert.match(source, /payload\.action !== "open"/);
  const workspace = await readFile(path.join(root, "src/hooks/use-runtime-workspace.ts"), "utf8");
  assert.match(workspace, /isCallUserQuestion\(question\)/);
  assert.match(workspace, /question\.status === "pending"/);
  assert.match(workspace, /chatIdForSessionId/);
  assert.match(workspace, /normalizeAgentQuestionNotificationBody/);
  assert.match(workspace, /const notificationChatId = chatId \|\| `question-/);
  assert.match(workspace, /agentQuestionNotificationInflightRef/);
  assert.match(workspace, /\.finally\(\(\) =>/);
  assert.match(workspace, /notifyCallUserQuestions/);
  assert.doesNotMatch(workspace, /isCallUserQuestion\(question\).*ask_user/s);
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");
  assert.match(app, /AGENT_QUESTION_NOTIFICATION_ACTION_EVENT/);
  assert.match(app, /setActiveChatId\(chat\.id\)/);
  assert.match(app, /setActiveView\("chat"\)/);
  assert.match(app, /item\.questionId === action\.questionId/);
  assert.match(app, /fetchAgentQuestions\(endpoint/);
  assert.match(app, /initialGlobalQuestionFetchRef/);
  assert.match(app, /sessionId: answeredQuestion\?\.sessionId \|\| sessionId/);
  assert.match(app, /projectRoot: answeredQuestion\?\.projectRoot/);
  assert.doesNotMatch(app, /chat\.items\.some\(\(item\) => item\.type === "agent"\)/);
});

test("production notification helpers suppress delivered and concurrent deliveries", async () => {
  const module = await import(pathToFileURL(path.join(root, "src/lib/agent-question-notifications.ts")).href);
  const delivered = new Set();
  const inflight = new Set();
  assert.equal(module.shouldStartAgentQuestionNotification("q-1", delivered, inflight), true);
  inflight.add("q-1");
  assert.equal(module.shouldStartAgentQuestionNotification("q-1", delivered, inflight), false);
  assert.equal(module.normalizeAgentQuestionNotificationBody("line one\nline two\t"), "line one line two");
  assert.equal(module.normalizeAgentQuestionNotificationBody("x".repeat(1000)).length, 512);
  assert.equal(module.shouldSilenceNativeNotification(true), true);
  assert.equal(module.shouldSilenceNativeNotification(false), false);
  assert.equal(module.shouldRetainAgentQuestionNotificationAfterSound(true), true);
  assert.equal(module.shouldRetainAgentQuestionNotificationAfterSound(false), false);
  inflight.delete("q-1");
  module.markAgentQuestionNotificationDelivered("q-1", delivered);
  assert.equal(module.shouldStartAgentQuestionNotification("q-1", delivered, inflight), false);
});

test("question sound gate requires server blocking authority and an agent association", async () => {
  const module = await import(pathToFileURL(path.join(root, "src/lib/agent-question-notifications.ts")).href);
  const ordinary = { questionId: "q-ordinary", sourceTool: "call_user", sessionId: "s1", status: "pending" };
  assert.equal(module.isBlockingAgentQuestion(ordinary), false);
  assert.equal(module.isBlockingAgentQuestion({ ...ordinary, needsUserAction: true }), true);
  assert.equal(module.isBlockingAgentQuestion({ ...ordinary, needsUserAction: true, sessionId: "" }), false);
  assert.equal(module.isBlockingAgentQuestion({ ...ordinary, needsUserAction: true, status: "answered" }), false);
  assert.equal(module.isBlockingAgentQuestion({ ...ordinary, runtimeContinuation: { status: "blocked" } }), false);
});

