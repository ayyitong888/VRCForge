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
  assert.equal(/<SubAgentWorkspaceSurface/.test(app), true);
  assert.equal(surface.includes("sticky bottom-0"), true);
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
  assert.equal(/const SUB_AGENT_REVIEW_NOTIFICATION_OPEN_ACTION/.test(rust), true);
  assert.equal(/approval-action/.test(rust), false);
});
