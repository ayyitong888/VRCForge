import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const read = (path) => readFile(resolve(root, path), "utf8");

const [app, menu, sidebar, zhCN, zhTW, enUS, jaJP] = await Promise.all([
  read("src/App.tsx"),
  read("src/components/common/text-edit-context-menu.tsx"),
  read("src/components/runtime/runtime-sidebar.tsx"),
  read("src/locales/zh-CN.json"),
  read("src/locales/zh-TW.json"),
  read("src/locales/en-US.json"),
  read("src/locales/ja-JP.json"),
]);

assert.ok(app.includes('import { TextEditContextMenu } from "./components/common/text-edit-context-menu";'));
assert.ok(app.includes("const rightSidebarVisible = sidebarsVisible && activeView === \"chat\";"));
assert.ok(app.includes("rightSidebarVisible ? workspaceGridColumnsWithRightSidebar : workspaceGridColumnsWithoutRightSidebar"));
assert.ok(app.includes("{rightSidebarVisible ? ("), "The right splitter and status rail must render only in chat");
assert.ok(app.includes("<TextEditContextMenu />"));
assert.ok(!app.includes('window.addEventListener("contextmenu", handler)'), "App must not blanket-disable input context menus");

for (const key of ["contextMenu.cut", "contextMenu.copy", "contextMenu.paste", "contextMenu.selectAll"]) {
  assert.ok(menu.includes(`t("${key}")`), `Missing native editing action ${key}`);
}
assert.ok(menu.includes('window.addEventListener("contextmenu", openMenu)'));
assert.ok(menu.includes("HTMLInputElement") && menu.includes("HTMLTextAreaElement"));
assert.ok(menu.includes("event.preventDefault()"), "Browser-specific menu must remain suppressed");

assert.ok(sidebar.includes('data-vrcforge-status="external-agent"'));
assert.ok(sidebar.includes('t("workspace.externalAgent")'));

for (const locale of [zhCN, zhTW, enUS, jaJP]) {
  for (const key of ["cut", "copy", "paste", "selectAll"]) {
    assert.ok(locale.includes(`"${key}"`), `Locale is missing context-menu key ${key}`);
  }
  assert.ok(locale.includes('"externalAgent"'), "Locale is missing the external Agent status label");
  assert.ok(locale.includes('"serviceReady"'), "Locale is missing the friendly backend status");
  assert.ok(locale.includes('"toolsReady"'), "Locale is missing the friendly tool status");
}

console.log("user-friendly runtime chrome UI contracts passed");
