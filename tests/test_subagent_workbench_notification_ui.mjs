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
  assert.equal(/selectedSubAgentPanelOpen\s*\?\s*subAgentWorkspaceSurface/.test(app), false);
  assert.equal(/subAgentSidebarActive \? subAgentWorkspaceSurface : <AsyncRightRuntimeSidebar/.test(app), true);
  assert.equal(/<AsyncSubAgentSidebar/.test(app), true);
  assert.equal(/const AsyncSubAgentSidebar = lazy/.test(app), true);
  assert.equal(/onSelect=\{\(taskId\) => void inspectSubAgentTask\(taskId\)\}/.test(app), true);
  assert.equal(/<SubAgentPanel[^>]*onOpen=\{openSubAgentWorkspace\}/.test(app), true);
  assert.equal(panel.includes("data-vrcforge-open-subagent-thread"), false);
  assert.equal(panel.includes('t("subagent.inspect")'), false);
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

test("sub-agent reviews have no desktop notification command", async () => {
  const commands = await readFile(path.join(root, "src-tauri/src/main.rs"), "utf8");
  assert.doesNotMatch(commands, /show_sub_agent_review_notification/);
  assert.match(commands, /show_approval_notification/);
});
