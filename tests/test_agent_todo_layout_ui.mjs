import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, sidebar, todo, activity, dashboard, zhCN, enUS] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-sidebar.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "agent-todo-panel.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-activity-panel.tsx"), "utf8"),
  readFile(resolve(root, "dashboard_server.py"), "utf8"),
  readFile(resolve(root, "src", "locales", "zh-CN.json"), "utf8"),
  readFile(resolve(root, "src", "locales", "en-US.json"), "utf8"),
]);

assert.match(todo, /data-vrcforge-agent-todo/);
assert.match(todo, /workspace\.todo/);
assert.match(todo, /progress\.map/);
assert.match(sidebar, /<AgentTodoPanel progress=\{agentProgress\}/);
assert.match(sidebar, /data-vrcforge-project-workbench/);
assert.match(sidebar, /data-vrcforge-environment-status=\{projectWorkspace \? undefined : true\}/);
assert.match(sidebar, /title=\{t\("workspace\.progress"\)\}/);
assert.match(sidebar, /title=\{projectWorkspaceLabel\}/);
assert.match(sidebar, /title=\{t\("workspace\.context"\)\}/);
assert.match(sidebar, /\{activityPanel\}/);
assert.match(sidebar, /\{subAgentPanel\}/);
assert.ok(
  sidebar.indexOf("<AgentTodoPanel") < sidebar.indexOf('data-vrcforge-status="project"'),
  "the visible TODO panel must stay above environment status in the upper-right rail",
);
assert.ok(
  sidebar.indexOf("{activityPanel}") < sidebar.indexOf('data-vrcforge-status="project"'),
  "the run ledger must stay in the project work rail above environment status",
);
assert.ok(
  sidebar.indexOf("{subAgentPanel}") < sidebar.indexOf('data-vrcforge-status="project"'),
  "sub-agent activity must stay in the project work rail above environment status",
);
assert.match(app, /agentProgress=\{agentProgress\}/);
assert.match(app, /const projectChatWorkspace = activeView === "chat" && Boolean\(activeChat\?\.projectPath\)/);
assert.match(app, /activityPanel=\{projectChatWorkspace \? undefined : runtimeActivityPanel\}/);
assert.match(app, /projectWorkspace=\{projectChatWorkspace\}/);
assert.match(app, /activityPanel=\{projectChatWorkspace \? runtimeActivityPanel : undefined\}/);
assert.doesNotMatch(activity, /data-vrcforge-current-progress/);
assert.doesNotMatch(activity, /progress:\s*AgentProgress\[\]/);
assert.match(activity, /workspace\.runLedger/);

for (const tool of [
  "vrcforge_progress_list",
  "vrcforge_progress_replace",
  "vrcforge_progress_create",
  "vrcforge_progress_update",
  "vrcforge_progress_delete",
]) {
  assert.ok(dashboard.includes(tool), `Agent TODO CRUD tool is missing: ${tool}`);
}

assert.match(zhCN, /"todo":\s*"待办"/);
assert.match(enUS, /"todo":\s*"Todo"/);

console.log("agent TODO upper-right layout contract: ok");
