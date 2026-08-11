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
assert.ok(
  sidebar.indexOf("<AgentTodoPanel") < sidebar.indexOf('data-vrcforge-status="project"'),
  "the visible TODO panel must stay above environment status in the upper-right rail",
);
assert.match(app, /agentProgress=\{agentProgress\}/);
assert.doesNotMatch(app, /<RuntimeActivityPanel[\s\S]*?progress=\{agentProgress\}/);
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
