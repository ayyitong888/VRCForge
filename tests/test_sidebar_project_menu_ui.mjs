import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const source = fs.readFileSync(path.join(root, "src/components/sidebar/sidebar-menus.tsx"), "utf8");

assert.match(source, /createPortal\([\s\S]*document\.body/);
assert.match(source, /className="fixed z-50 w-56/);
assert.match(source, /className="fixed inset-0 z-40/);
assert.match(source, /role="menu"/);
assert.match(source, /role="menuitem"/);
assert.match(source, /querySelector<.*button\[role=\\?"menuitem\\?"\]/);
assert.match(source, /label=\{t\("project\.newChatInProject"\)\}/);
assert.match(source, /label=\{t\("project\.removeProject"\)\}/);

console.log("sidebar project menu visibility/accessibility contract passed");
