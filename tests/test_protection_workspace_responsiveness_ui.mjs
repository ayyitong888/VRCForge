import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const controller = await readFile(
  new URL("../src/hooks/use-protection-workspace-controller.ts", import.meta.url),
  "utf8",
);
const desktopCommands = await readFile(
  new URL("../src-tauri/src/commands.rs", import.meta.url),
  "utf8",
);
const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");

const openStart = controller.indexOf("function openProtection()");
const openEnd = controller.indexOf("async function loadProtectionPlan", openStart);
assert.ok(openStart >= 0 && openEnd > openStart, "protection navigation must have one bounded open handler");
const openHandler = controller.slice(openStart, openEnd);
assert.match(openHandler, /setActiveView\("protection"\)/, "navigation must display the protection page immediately");
assert.doesNotMatch(openHandler, /loadProtectionPlan\(/, "opening the page must not duplicate the plan effect");
assert.doesNotMatch(openHandler, /loadProtectionAvatars\(/, "opening the page must not duplicate the avatar effect");
assert.match(openHandler, /if \(!runtimeConnected\)/, "opening protection while offline must still start the runtime");
assert.match(openHandler, /void startRuntime\(\)/, "runtime startup must not block the protection navigation handler");
assert.match(controller, /const protectionPlanCache = useRef/, "protection plans must have a per-session cache");
assert.match(controller, /const protectionAvatarCache = useRef/, "avatar scans must have a per-session cache");
assert.match(controller, /protectionPlanCache\.current\.get\(cacheKey\)/, "reopening protection must reuse an existing plan");
assert.match(controller, /protectionAvatarCache\.current\.get\(cacheKey\)/, "reopening protection must reuse existing avatars");
assert.match(controller, /if \(!force && cached\)/, "cached protection results must skip backend scans");
assert.match(app, /loadProtectionPlan\(endpoint, protectionProfile, true\)/, "the explicit plan refresh must bypass cache");
assert.match(app, /loadProtectionAvatars\(endpoint, true\)/, "the explicit avatar refresh must bypass cache");

for (const command of [
  "fetch_avatars",
  "plan_avatar_encryption",
  "request_avatar_encryption_apply",
]) {
  const signature = new RegExp(`pub async fn ${command}\\s*\\(`);
  assert.match(desktopCommands, signature, `${command} must not run synchronously on the desktop thread`);
  const start = desktopCommands.search(signature);
  const nextCommand = desktopCommands.indexOf("#[tauri::command]", start);
  const body = desktopCommands.slice(start, nextCommand < 0 ? undefined : nextCommand);
  assert.match(body, /blocking_backend_json_request\(move \|\|/, `${command} must reuse the bounded backend worker`);
  assert.match(body, /\.await/, `${command} must await its worker without blocking the desktop thread`);
}

console.log("protection workspace opens immediately without duplicate or main-thread-blocking scans: ok");
