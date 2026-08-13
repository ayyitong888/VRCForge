import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, sidebar, sections, todo, runtimeUi, activity, appSidebar, workspaceHeader, dashboard, zhCN, enUS] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-sidebar.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "project-workbench-sections.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "agent-todo-panel.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-sidebar-ui.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-activity-panel.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "sidebar", "app-sidebar.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "workspace", "workspace-header.tsx"), "utf8"),
  readFile(resolve(root, "dashboard_server.py"), "utf8"),
  readFile(resolve(root, "src", "locales", "zh-CN.json"), "utf8"),
  readFile(resolve(root, "src", "locales", "en-US.json"), "utf8"),
]);

assert.match(todo, /data-vrcforge-agent-todo/);
assert.match(todo, /workspace\.todo/);
assert.match(todo, /progress\.map/);
assert.match(todo, /progress\.map\(\(item, index\)/);
assert.match(todo, /\{index \+ 1\}/);
assert.match(todo, /<ol className="m-0 list-none space-y-0\.5 p-0">/);
assert.match(todo, /<li/);
assert.match(todo, /border-primary/);
assert.match(todo, /return "border-border text-muted-foreground"/);
assert.match(todo, /bg-primary text-background/);
assert.match(todo, /animate-pulse motion-reduce:animate-none/);
assert.match(todo, /text-muted-foreground line-through/);
assert.doesNotMatch(todo, /todoStatusLabel/);
assert.doesNotMatch(todo, /workspace\.progress(?:Completed|Blocked|Cancelled|InProgress|Pending)/);
assert.doesNotMatch(todo, /grid-cols-\[16px_minmax\(0,1fr\)_auto\]/);
assert.doesNotMatch(todo, /item\.summary/);
assert.doesNotMatch(todo, /emerald|amber|destructive/);
assert.match(todo, /aria-current=\{isActiveTodo\(item\.status\) \? "step" : undefined\}/);
assert.match(todo, /aria-hidden="true"/);
assert.match(runtimeUi, /title=\{label\}/);
assert.match(runtimeUi, /title=\{value\}/);
assert.match(runtimeUi, /title=\{value \? `\$\{label\}: \$\{value\}` : label\}/);
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
assert.doesNotMatch(sections, /hasEnvironmentAttention/);
assert.doesNotMatch(sections, /hasStartupIssue/);
assert.doesNotMatch(sections, /openDoctor/);
assert.doesNotMatch(sections, /data-vrcforge-project-environment-doctor/);
assert.match(appSidebar, /onOpenDoctor/);
assert.match(appSidebar, /sidebar\.doctor/);
assert.match(workspaceHeader, /onOpenDoctor/);
assert.match(workspaceHeader, /showDoctorStartupPrompt/);
assert.match(workspaceHeader, /onClick=\{onOpenDoctor\}/);
assert.doesNotMatch(sidebar, /hasEnvironmentAttention/);
assert.doesNotMatch(sidebar, /hasStartupIssue/);
assert.doesNotMatch(sidebar, /openDoctor/);
assert.doesNotMatch(sidebar, /sidebar\.doctor/);
assert.doesNotMatch(sidebar, /\{hasEnvironmentAttention \|\| hasStartupIssue \? \(/);
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
assert.match(app, /messageId: item\.id/);
assert.match(app, /attachment,/);
assert.match(app, /onLocateUserAttachmentSource=\{locateUserAttachmentSource\}/);
assert.match(app, /onOpenUserAttachmentSource=\{openUserAttachmentSource\}/);
assert.match(sections, /onLocateUserAttachmentSource\(source\)/);
assert.match(sections, /onOpenUserAttachmentSource\(source\)/);
assert.match(sections, /onLocateUserAttachmentSource && source\.messageId/, "Locate must not be shown for a compacted source whose owning message no longer exists");
assert.match(sections, /source\.attachment\?\.dataUrl && source\.attachment\.type\.startsWith\("image\/"\)/, "Open is only available for attachments that have a real inline image preview");
assert.doesNotMatch(app, /RuntimeActivityPanel|runtimeActivityPanel|activityPanel=\{/);
assert.match(app, /runtimeRuns=\{runtimeRuns\}/);
assert.match(app, /onSaveOperationAsSkill=\{\(summary\) => void openSkillsWithCapturedPath\(summary\)\}/);
assert.doesNotMatch(app, /runtimeActivityCount=/);
assert.doesNotMatch(app, /subAgentCount=/);
const chatWorkspaceInvocation = app.slice(app.indexOf("<ChatWorkspace"), app.indexOf("\n            />", app.indexOf("<ChatWorkspace")));
assert.doesNotMatch(chatWorkspaceInvocation, /subAgentPanel=/);
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
