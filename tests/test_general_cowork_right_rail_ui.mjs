import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, sidebar, sections, enUS, zhCN, zhTW, jaJP] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-sidebar.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "project-workbench-sections.tsx"), "utf8"),
  readFile(resolve(root, "src", "locales", "en-US.json"), "utf8",
  ),
  readFile(resolve(root, "src", "locales", "zh-CN.json"), "utf8"),
  readFile(resolve(root, "src", "locales", "zh-TW.json"), "utf8"),
  readFile(resolve(root, "src", "locales", "ja-JP.json"), "utf8"),
]);

const workbenchTodoTitle = 'title={isUnityProject ? t("workspace.todo") : t("workspace.progress")}';
const todoTitleIndex = sections.indexOf(workbenchTodoTitle);
const subAgentsTitle = 'title={t("workspace.subAgents")}';
const subAgentsTitleIndex = sections.indexOf(subAgentsTitle);
const workspaceTitle = "title={workspaceSectionTitle}";
const workspaceTitleIndex = sections.indexOf(workspaceTitle);
const sourcesTitle = 'title={t("workspace.sources")}';
const sourcesIndex = sections.indexOf(sourcesTitle);

assert.ok(todoTitleIndex >= 0, "Workbench progress title should be branch-aware (todo for Unity, progress for General)");
assert.ok(subAgentsTitleIndex > todoTitleIndex, "Sub Agents should follow the first section");
assert.ok(workspaceTitleIndex > subAgentsTitleIndex, "Workspace section should follow Sub Agents");
assert.ok(sourcesIndex > workspaceTitleIndex, "Conditional Sources should remain below Workspace");

assert.match(sections, /title=\{isUnityProject \? t\("workspace\.todo"\) : t\("workspace\.progress"\)\}/);
assert.match(sections, /title=\{t\("workspace\.subAgents"\)\}/);
assert.match(sections, /title=\{workspaceSectionTitle\}/);
assert.match(sections, /attachmentCount > 0 \? \(/);
assert.match(sections, /title=\{t\("workspace\.sources"\)\}/);
assert.doesNotMatch(sections, /title=\{t\("workspace\.context"\)\}/);

// General branch workspace uses bounded diff snapshot (no fake full-tree projection).
assert.match(sections, /workspaceSummary: WorkspaceDiffSummary \| null;/);
assert.match(sections, /const workspaceFiles = repoSummary\?\.files \|\| \[\];/);
assert.match(sections, /const { changed: workspaceChangedFiles, outputs: workspaceOutputFiles } = useMemo\(\(\) => splitWorkspaceFiles\(workspaceFiles\), \[workspaceFiles\]\);/);
assert.match(sections, /const OUTPUT_FILE_STATUSES = new Set\(\["\?\?", "A"\]\);/);
assert.match(sections, /const hasMoreWorkspaceChangedFiles = workspaceChangedFiles.length > MAX_WORKSPACE_FILE_LIST;/);
assert.match(sections, /const hasMoreWorkspaceOutputFiles = workspaceOutputFiles.length > MAX_WORKSPACE_FILE_LIST;/);
assert.match(sections, /t\("workspace\.changes"\)/);
assert.match(sections, /t\("workspace\.local"\)/);
assert.match(sections, /t\("workspace\.branch"\)/);
assert.match(sections, /t\("workspace\.outputs"\)/);
assert.match(sections, /t\("workspace\.showLess"/);
assert.match(sections, /t\("workspace\.viewAll"/);
assert.match(sections, /t\("workspace\.noWorkspaceSummary"\)/);

// General projects show Sources only when real attachments exist; they do not
// synthesize empty Context/Memory/Skills rows.
assert.doesNotMatch(sections, /label=\{t\("workspace\.memory"\)\}/);
assert.doesNotMatch(sections, /label=\{t\("workspace\.skills"\)\}/);
assert.match(sections, /onLocateUserAttachmentSource && source\.messageId/);
assert.match(sections, /onOpenUserAttachmentSource && source\.attachment\?\.dataUrl && source\.attachment\.type\.startsWith\("image\/"\)/);

// Sources must not expose runtime prompt or credential text.
assert.doesNotMatch(sections, /promptSummary/);
assert.doesNotMatch(sections, /action\.promptSummary|\\.promptSummary/);
assert.doesNotMatch(sections, /\bcredential/i);

// Background desktop actions only expose provider/action/status surface.
assert.match(sections, /activeDesktopActions/);
assert.match(sections, /runningDesktopActions/);
assert.match(sections, /label=\{action\.provider \|\| t\("workspace\.desktopActionProviderUnknown"\)\}/);
assert.match(sections, /value=\{action\.action \|\| t\("workspace\.backgroundAction"\)\}/);
assert.match(sections, /t\("workspace\.noBackgroundActions"\)/);

// Sub-agent count/status block remains branch-safe and non-duplicative.
assert.match(sections, /const subAgentPanelHasDetails = Boolean\(subAgentPanel\);/);
assert.match(sections, /\{subAgentPanelHasDetails \? null :/);
assert.match(sections, /subAgentRunningTaskCount/);
assert.match(sections, /subAgentCompletedTaskCount/);

// Branch isolation uses two storage keys and the project type switch.
assert.match(sections, /const key = workspaceSectionsKey\(isUnityProject\);/);
assert.match(sections, /PROJECT_WORKBENCH_SECTIONS_KEY_GENERAL/);
assert.match(sections, /PROJECT_WORKBENCH_SECTIONS_KEY_UNITY/);
assert.match(sections, /isUnityProject \? t\("workspace\.todo"\) : t\("workspace\.progress"\)/);

// App contract unchanged: passes through existing right-rail payloads.
assert.match(app, /subAgentRunningTaskCount=\{runningSubAgentTaskCount\}/);
assert.match(app, /subAgentCompletedTaskCount=\{completedSubAgentTaskCount\}/);
assert.match(app, /activeDesktopActions=\{activeDesktopActions\}/);
const runtimeSidebarInvocation = app.slice(app.indexOf("<AsyncRightRuntimeSidebar"), app.indexOf("\n              />", app.indexOf("<AsyncRightRuntimeSidebar")));
assert.doesNotMatch(runtimeSidebarInvocation, /agentMemory=/);
assert.doesNotMatch(runtimeSidebarInvocation, /skillCount=/);
assert.match(app, /workspaceSummary=\{workspaceDiff\}/);

// Codex/Cowork-style rail: one compact information stack only.
const scrollAreaStart = sidebar.indexOf("app-scrollbar min-h-0 flex-1 overflow-y-auto");
assert.ok(scrollAreaStart >= 0, "Right rail keeps its scrollable workbench");
assert.ok(sidebar.includes("ProjectWorkbenchSections"), "Workbench sections must lead the scrollable right rail");
assert.doesNotMatch(sidebar, /AgentGoalPinnedCard|AgentGoalManagement|data-vrcforge-goal-summary/);
assert.doesNotMatch(sidebar, /WorkflowManagement/);
assert.match(sidebar, /PanelRightClose/);
// i18n keys required for General rail rendering.
for (const [locale, content] of [["en-US", enUS], ["zh-CN", zhCN], ["zh-TW", zhTW], ["ja-JP", jaJP]]) {
  const workspaceKeys = [
    "workspace",
    "local",
    "branch",
    "outputs",
    "noWorkspaceSummary",
    "sources",
    "desktopActionProviderUnknown",
    "backgroundAction",
    "noBackgroundActions",
    "progress",
    "changes",
  ];
  assert.match(content, /"workspace"\s*:\s*\{/);
  for (const key of workspaceKeys) {
    assert.ok(content.includes(`"${key}"`), `${locale}: missing i18n key ${key}`);
  }
}

console.log("general cowork right rail contract: ok");
