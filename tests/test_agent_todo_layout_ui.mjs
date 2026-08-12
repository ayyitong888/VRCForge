import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, sidebar, sections, todo, activity, dashboard, zhCN, enUS] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-sidebar.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "project-workbench-sections.tsx"), "utf8"),
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
assert.match(sections, /title=\{t\("workspace\.todo"\)\}/);
assert.match(sections, /title=\{t\("workspace\.subAgents"\)\}/);
assert.match(sections, /title=\{t\("workspace\.environment"\)\}/);
assert.match(sections, /title=\{t\("workspace\.userAttachmentSources"\)\}/);
assert.ok(
  sections.indexOf('title={t("workspace.todo")}') > -1,
  "Project workbench TODO title should exist",
);
assert.ok(
  sections.indexOf('title={t("workspace.todo")}') < sections.indexOf('title={t("workspace.subAgents")}'),
  "TODO must appear before Sub Agents in the project workbench",
);
assert.ok(
  sections.indexOf('title={t("workspace.subAgents")}') < sections.indexOf('title={t("workspace.environment")}'),
  "Sub Agents must appear before Environment in the project workbench",
);
assert.ok(
  sections.indexOf('title={t("workspace.environment")}') < sections.indexOf('title={t("workspace.userAttachmentSources")}'),
  "Environment must appear before User attachment sources in the project workbench",
);
assert.match(sections, /subAgentPanel/);
assert.match(sections, /userAttachmentSources/);
const todoTitleIndex = sections.indexOf('title={t("workspace.todo")}');
const subAgentsTitleIndex = sections.indexOf('title={t("workspace.subAgents")}');
const embeddedTodoIndex = sections.indexOf("<AgentTodoPanelEmbedded progress={agentProgress}");
const projectTodoBlock = sections.slice(todoTitleIndex, subAgentsTitleIndex);
assert.ok(todoTitleIndex >= 0 && embeddedTodoIndex > todoTitleIndex, "Project TODO should use embedded component");
assert.ok(
  embeddedTodoIndex < subAgentsTitleIndex,
  "Embedded TODO should appear before Sub Agents in section order",
);
assert.ok(
  projectTodoBlock.indexOf("<AgentTodoPanel progress={agentProgress}") === -1,
  "Project-workbench TODO should not reuse full panel in todo section",
);
assert.doesNotMatch(sidebar, /activityPanel=\{/);
assert.doesNotMatch(sections, /GitBranch|workspace\.changedFiles|workspaceDiff|filesSeen|workspaceDiffStatus/);
assert.doesNotMatch(sidebar, /GitBranch|workspace\.changedFiles|workspaceDiff|filesSeen|workspaceDiffStatus/);
assert.match(app, /agentProgress=\{agentProgress\}/);
assert.match(app, /const projectChatWorkspace = activeView === "chat" && Boolean\(activeChat\?\.projectPath\)/);
assert.match(app, /projectWorkspace=\{projectChatWorkspace\}/);
assert.match(app, /subAgentPanel=\{projectChatWorkspace \? subAgentActivityPanel : undefined\}/);
assert.match(app, /subAgentTaskCount=\{activeSubAgentTasks\.length\}/);
assert.match(app, /userAttachmentSources=\{userAttachmentSources\}/);
assert.doesNotMatch(app, /activityPanel=\{projectChatWorkspace \? runtimeActivityPanel : undefined\}/);
assert.doesNotMatch(app, /runtimeActivityCount=/);
assert.doesNotMatch(app, /subAgentCount=/);
assert.match(app, /subAgentPanel=\{projectChatWorkspace \? undefined : subAgentActivityPanel\}/);
assert.match(app, /subAgentPanel=\{projectChatWorkspace \? subAgentActivityPanel : undefined\}/);
assert.match(app, /userAttachmentSources=\{userAttachmentSources\}/);
assert.doesNotMatch(activity, /data-vrcforge-current-progress/);
assert.doesNotMatch(activity, /progress:\s*AgentProgress\[\]/);
assert.doesNotMatch(activity, /<RuntimeSection title=\{t\("workspace\.runLedger"\)\}/);

for (const tool of [
  "vrcforge_progress_list",
  "vrcforge_progress_replace",
  "vrcforge_progress_create",
  "vrcforge_progress_update",
  "vrcforge_progress_delete",
]) {
  assert.ok(dashboard.includes(tool), `Agent TODO CRUD tool is missing: ${tool}`);
}

assert.match(zhCN, /"todo"\s*:/);
assert.match(zhCN, /"userAttachmentSources":\s*"用户附件来源"/);
assert.match(enUS, /"todo"\s*:\s*"Todo"/);
assert.match(enUS, /"userAttachmentSources":\s*"User attachment sources"/);
assert.match(zhCN, /"noUserAttachmentSources":/);
assert.match(enUS, /"noUserAttachmentSources":/);

console.log("agent TODO upper-right layout contract: ok");
