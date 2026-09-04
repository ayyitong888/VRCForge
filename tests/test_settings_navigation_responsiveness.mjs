import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
const controller = await readFile(
  new URL("../src/hooks/use-settings-workspace-controller.ts", import.meta.url),
  "utf8",
);

const openSettingsSection = app.match(
  /function openSettingsSection\([\s\S]*?\n  \}\n\n  async function createGoalFromSlash/,
)?.[0] || "";
assert.match(openSettingsSection, /setActiveSettingsSection\(section\);/);
assert.match(openSettingsSection, /if \(activeView !== "settings"\) \{\s*void openSettings\(\);\s*\}/);

assert.match(controller, /settingsInitInflightRef/);
assert.match(controller, /settingsInitRequestSequenceRef/);
assert.match(controller, /const settingsContextKey = `\$\{endpoint\}\\u0000\$\{activeProjectPath\}`/);
assert.match(controller, /const initKey = settingsContextKey/);
assert.match(controller, /settingsInitInflightRef\.current\.get\(initKey\)/);
assert.match(controller, /settingsInitInflightRef\.current\.delete\(initKey\)/);
assert.doesNotMatch(controller, /settingsInitKeyRef/);
assert.match(controller, /requestSequence !== settingsInitRequestSequenceRef\.current/);
assert.match(controller, /settingsContextKeyRef\.current !== initKey/);
const initialize = controller.slice(controller.indexOf("const initialize ="), controller.indexOf("settingsInitInflightRef.current.set"));
assert.ok(initialize.indexOf("void loadConnectors(targetEndpoint)") < initialize.indexOf("await fetchAgentNotes(targetEndpoint)"), "archive loading must not wait on notes");
assert.ok(initialize.indexOf("void loadDiagnostics(targetEndpoint)") < initialize.indexOf("await fetchAgentNotes(targetEndpoint)"), "diagnostics must not wait on notes");
assert.match(controller, /connectorsRequestSequenceRef/);
assert.match(controller, /if \(requestSequence === connectorsRequestSequenceRef\.current\) \{\s*setConnectorStatus\(payload\);/);
assert.match(controller, /if \(requestSequence === connectorsRequestSequenceRef\.current\) \{\s*setLoadingConnectors\(false\);/);

console.log("settings navigation request dedupe and stale connector guard contract passed");
