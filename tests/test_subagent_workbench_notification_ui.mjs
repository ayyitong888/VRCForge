import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("sub-agent panel props and surface wiring use open action flow", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");
  const panel = await readFile(path.join(root, "src/components/subagents/sub-agent-panel.tsx"), "utf8");
  const surface = await readFile(path.join(root, "src/components/subagents/sub-agent-workspace-surface.tsx"), "utf8");

  assert.equal(panel.includes("onInspect"), false);
  assert.equal(panel.includes("onCloseInspect"), false);
  assert.equal(panel.includes("onOpen"), true);
  assert.equal(/<SubAgentPanel[\s\S]*onOpen=/.test(app), true);
  assert.equal(/selectedSubAgentPanelOpen\s*\?\s*subAgentWorkspaceSurface/.test(app), true);
  assert.equal(/<AsyncSubAgentWorkspaceSurface/.test(app), true);
  assert.equal(/const AsyncSubAgentWorkspaceSurface = lazy/.test(app), true);
  assert.equal(/onSelect=\{\(taskId\) => void inspectSubAgentTask\(taskId\)\}/.test(app), true);
  assert.equal(/onOpen=\{\(\) => void openSubAgentWorkspace\(\)\}/.test(app), true);
  assert.equal(surface.includes("sticky bottom-0"), true);
});

test("sub-agent mutations refresh the selected detail snapshot and event history", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");

  assert.equal(/async function refreshSelectedSubAgentTask\(taskId: string, mode:/.test(app), true);
  assert.equal(/mode === "select" \? \+\+subAgentInspectRequestRef\.current/.test(app), true);
  assert.equal(/requestId !== subAgentInspectRequestRef\.current/.test(app), true);
  assert.equal(/function beginSubAgentAction\(taskId: string\)/.test(app), true);
  assert.equal(/async function cancelSubAgentTask[\s\S]*await refreshSelectedSubAgentTask\(taskId, "if-current"\)/.test(app), true);
  assert.equal(/async function retrySubAgentTask[\s\S]*await refreshSelectedSubAgentTask\(payload\.task\.id, "if-current"\)/.test(app), true);
  assert.equal(/async function mergeSubAgentTask[\s\S]*await refreshSelectedSubAgentTask\(payload\.task\.id, "if-current"\)/.test(app), true);
});

test("review notification listener remains installed while render callbacks change", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");

  assert.equal(app.includes("subAgentReviewNotificationActionHandlerRef"), true);
  assert.equal(
    /listen<unknown>\(SUB_AGENT_REVIEW_NOTIFICATION_ACTION_EVENT, \(event\) =>\s*subAgentReviewNotificationActionHandlerRef\.current\(event\.payload\)\s*\)/.test(app),
    true,
  );
  const listenerStart = app.indexOf("void listen<unknown>(SUB_AGENT_REVIEW_NOTIFICATION_ACTION_EVENT");
  const listenerEnd = app.indexOf("\n  useEffect(() =>", listenerStart + 1);
  assert.notEqual(listenerStart, -1);
  const listenerEffect = app.slice(listenerStart, listenerEnd);
  assert.equal(listenerEffect.includes("endpoint"), false);
  assert.equal(listenerEffect.includes("getChatById"), false);
  assert.equal(listenerEffect.includes("openChat"), false);
  assert.equal(listenerEffect.includes("}, []);"), true);
});

test("retry is single-flight and cannot reclaim a newer task selection", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");
  const retryStart = app.indexOf("async function retrySubAgentTask");
  const retryEnd = app.indexOf("async function refreshSelectedSubAgentTask", retryStart);
  assert.notEqual(retryStart, -1);
  const retry = app.slice(retryStart, retryEnd);

  assert.equal(retry.includes("if (!beginSubAgentAction(taskId))"), true);
  assert.equal(retry.includes("const selectionIntentAtStart = subAgentSelectionIntentRef.current"), true);
  assert.equal(retry.includes("subAgentSelectionIntentRef.current !== selectionIntentAtStart"), true);
  assert.equal(retry.includes('refreshSelectedSubAgentTask(payload.task.id, "if-current")'), true);
});

test("delayed review notification retry rechecks the live task snapshot", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");

  assert.equal(app.includes("subAgentTasksRef"), true);
  assert.equal(/subAgentTasksRef\.current\.find\(\(current\) => current\.id === task\.id\)/.test(app), true);
  assert.equal(/isAwaitingMergeReview\(currentTask\)/.test(app), true);
  assert.equal(/currentTask\.revision !== task\.revision/.test(app), true);
  assert.equal(/currentTask\.parentChatId !== task\.parentChatId/.test(app), true);
});

test("review notification click path uses taskId+revision+parentChatId key and re-opens by chat object", async () => {
  const app = await readFile(path.join(root, "src/App.tsx"), "utf8");

  assert.equal(/reviewNotificationId\s*=\s*`\$\{action\.taskId\}:\$\{action\.revision\}:\$\{action\.parentChatId\}`/.test(app), true);
  assert.equal(/openChat\(task\.parentChatId\)/.test(app), false);
  assert.equal(/const\s+chat\s*=\s*getChatById\(task\.parentChatId\)/.test(app), true);
  assert.equal(/openChat\(chat\)/.test(app), true);
});

test("review toast surface only emits open action and dedupe keys include parent chat id", async () => {
  const notifications = await readFile(path.join(root, "src/lib/approval-notifications.ts"), "utf8");
  const rust = await readFile(path.join(root, "src-tauri/src/approval_notification_windows.rs"), "utf8");

  assert.equal(/action: \"open\"/.test(notifications), true);
  assert.equal(/typeof revisionValue/.test(notifications), true);
  assert.equal(/Number\(payload\.revision\)/.test(notifications), false);
  assert.equal(/const SUB_AGENT_REVIEW_NOTIFICATION_OPEN_ACTION/.test(rust), true);
  assert.equal(/approval-action/.test(rust), false);
});
