import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");

const [app, workspace, composer, workbench, runtimeSidebar, appSidebar, workspaceHeader] = await Promise.all([
  read("src/App.tsx"),
  read("src/components/chat/chat-workspace.tsx"),
  read("src/components/chat/composer.tsx"),
  read("src/components/runtime/project-workbench-sections.tsx"),
  read("src/components/runtime/runtime-sidebar.tsx"),
  read("src/components/sidebar/app-sidebar.tsx"),
  read("src/components/workspace/workspace-header.tsx"),
]);

// Goal is absent when inactive, appears directly above the composer when
// active, and remains controllable through both the user slash path and the
// Agent-owned Goal API/tool path.
assert.match(workspace, /const activeGoalBar = activeGoal \? \(/);
assert.match(workspace, /data-chat-active-goal/);
assert.match(workspace, /\{activeGoalBar\}/);
assert.match(app, /message === "\/goal" \|\| message\.startsWith\("\/goal "\)/);
assert.match(app, /createAgentGoal\(/);
assert.doesNotMatch(workbench, /AgentGoalManagement|WorkflowManagement/);

// Only the right rail owns a collapse control; the left and center duplicates
// stay removed. A detached restore control remains available when collapsed.
assert.doesNotMatch(appSidebar, /PanelLeftClose|data-vrcforge-left-sidebar-collapse/);
assert.doesNotMatch(workspaceHeader, /PanelRightClose|PanelRightOpen/);
assert.match(runtimeSidebar, /PanelRightClose/);
assert.match(app, /data-vrcforge-right-sidebar-restore/);

// Long conversations expose an explicit jump-to-bottom affordance.
assert.match(workspace, /data-chat-scroll-to-bottom/);
assert.match(workspace, /onClick=\{onScrollToBottom\}/);
assert.match(app, /const conversationPinnedRef = useRef\(true\)/);
assert.match(app, /updateConversationPinned\(nearBottom\)/);
assert.match(app, /if \(!conversationPinnedRef\.current\)/);
assert.match(app, /scrollIntoView\(\{ behavior: "auto", block: "end" \}\)/);
assert.equal((app.match(/scrollIntoView\(\{ behavior: "smooth", block: "end" \}\)/g) || []).length, 1);
assert.doesNotMatch(app, /\[pinnedToConversationBottom, conversation\.length\]/);

// Provider/model identity is complete and Unicode-safe. Narrow layouts wrap
// the whole value instead of truncating it.
assert.match(composer, /\.join\(" · "\)/);
assert.match(composer, /break-words/);
assert.match(composer, /xl:whitespace-nowrap/);
assert.doesNotMatch(composer, /providerModelLabel[\s\S]{0,220}truncate/);

// Context usage is one continuous ring. Known usage overlays a coloured arc
// on a complete base circle; unknown usage is a complete solid neutral ring.
assert.match(composer, /data-context-ring/);
assert.match(composer, /className="stroke-border"/);
assert.match(composer, /percent >= 90 \? "stroke-destructive" : percent >= 60 \? "stroke-amber-500" : "stroke-primary"/);
assert.match(composer, /strokeDasharray=\{`\$\{percent\} 100`\}/);
assert.match(composer, /data-context-segment="unknown"/);
assert.doesNotMatch(composer, /strokeDasharray="[^\"]+"[\s\S]{0,180}data-context-segment="unknown"/);

// General projects follow the compact Cowork hierarchy; Unity keeps its
// environment-specific surface.
assert.match(workbench, /title=\{isUnityProject \? t\("workspace\.todo"\) : t\("workspace\.progress"\)\}/);
assert.match(workbench, /title=\{t\("workspace\.subAgents"\)\}/);
assert.match(workbench, /title=\{workspaceSectionTitle\}/);
assert.match(workbench, /attachmentCount > 0 \? \(/);
assert.match(workbench, /title=\{t\("workspace\.sources"\)\}/);
assert.doesNotMatch(workbench, /title=\{t\("workspace\.context"\)\}/);

console.log("1.6.2 product regression UI contracts: ok");
