import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");
const app = read("src/App.tsx");
const header = read("src/components/workspace/workspace-header.tsx");
const composer = read("src/components/chat/composer.tsx");
const rightRail = read("src/components/runtime/runtime-sidebar.tsx");

for (const duplicate of [
  "permissionFullAuto",
  "permissionAuto",
  "permissionBadgeTone",
  "runtimeConnected",
  "pendingApprovals",
  "StatusChip",
]) {
  assert.doesNotMatch(header, new RegExp(duplicate));
}
assert.doesNotMatch(app, /<WorkspaceHeader[\s\S]*?permissionFullAuto=/);
assert.match(composer, /currentModeVisual/);
assert.match(rightRail, /runtimeConnected/);
assert.match(rightRail, /pendingApprovals/);

console.log("workspace header status dedupe UI contract: ok");
