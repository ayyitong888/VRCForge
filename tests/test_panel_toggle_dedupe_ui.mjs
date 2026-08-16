import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const [app, sidebar, header, rightRail] = await Promise.all([
  readFile(resolve(root, "src", "App.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "sidebar", "app-sidebar.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "workspace", "workspace-header.tsx"), "utf8"),
  readFile(resolve(root, "src", "components", "runtime", "runtime-sidebar.tsx"), "utf8"),
]);

assert.doesNotMatch(sidebar, /PanelLeftClose|PanelLeftOpen|onToggleSidebar/);
assert.doesNotMatch(header, /PanelRightClose|PanelRightOpen|onToggleRightSidebar|RuntimeToolButton/);
assert.match(rightRail, /PanelRightClose/);
assert.match(app, /data-vrcforge-right-sidebar-restore/);
assert.match(app, /PanelRightOpen/);
assert.doesNotMatch(app, /leftSidebarCollapsed|setLeftSidebarCollapsed/);
assert.match(app, /collapsed=\{false\}/);
assert.match(app, /min=\{MIN_LEFT_PANE_WIDTH\}/);

console.log("panel toggle dedupe UI contract: ok");
