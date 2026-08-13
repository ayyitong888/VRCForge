import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");
const [app, composer, sidebarMenus, providerSettings] = await Promise.all([
  read("src/App.tsx"),
  read("src/components/chat/composer.tsx"),
  read("src/components/sidebar/sidebar-menus.tsx"),
  read("src/components/settings/provider-settings.tsx"),
]);

// P1 regression: the conversation selection toolbar is App-owned and rendered
// outside ChatWorkspace, so navigation must explicitly retire both its React
// state and the browser selection that created it.
assert.match(
  app,
  /useEffect\(\(\) => \{\s*setSelectionMenu\(null\);\s*window\.getSelection\(\)\?\.removeAllRanges\(\);\s*\}, \[activeView, activeChat\?\.id, activeProjectPath, selectedSubAgentPanelOpen, showProjectModal\]\);/,
  "conversation selection UI must be cleared whenever its owning chat surface changes",
);
assert.match(
  app,
  /selectionMenu=\{activeView === "chat" && !selectedSubAgentPanelOpen && !showProjectModal \? selectionMenu : null\}/,
  "selection toolbar must unmount in the same render that leaves the conversation surface",
);

// Adjacent transient-surface audit: these controls already have a lifecycle
// boundary and therefore do not need another App-level state owner.
assert.match(sidebarMenus, /projectMenu \? createPortal\([\s\S]*?<MenuScrim onClose=\{onClose\} \/>/, "project context menu must retain its blocking close scrim");
assert.match(composer, /const \[modeMenuOpen, setModeMenuOpen\] = useState\(false\)/, "permission menu remains Composer-owned");
assert.match(composer, /modeMenuOpen \? <div className="fixed inset-0 z-20" onClick=\{\(\) => setModeMenuOpen\(false\)\} \/>/, "permission menu must close before interaction can leave its composer surface");
assert.doesNotMatch(providerSettings, /model(?:Menu|Popover)Open/i, "model selection must not introduce a detached popover owner");
assert.match(providerSettings, /<select[\s\S]*?value=\{model\}/, "model selection stays in the settings-owned native control");

console.log("transient overlay navigation UI contracts passed");
