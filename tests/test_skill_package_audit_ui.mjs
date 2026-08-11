import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const auditSource = await readFile(
  new URL("../src/components/skills/skill-package-audit-list.tsx", import.meta.url),
  "utf8",
);
const runtimeSource = await readFile(
  new URL("../src/components/runtime/runtime-sidebar-ui.tsx", import.meta.url),
  "utf8",
);
const runtimeActivitySource = await readFile(
  new URL("../src/components/runtime/runtime-activity-panel.tsx", import.meta.url),
  "utf8",
);
const sidebarSource = await readFile(
  new URL("../src/components/sidebar/app-sidebar.tsx", import.meta.url),
  "utf8",
);
const sidebarPrimitiveSource = await readFile(
  new URL("../src/components/sidebar/sidebar.tsx", import.meta.url),
  "utf8",
);
const pathToSkillSource = await readFile(
  new URL("../src/components/skills/path-to-skill-capture-panel.tsx", import.meta.url),
  "utf8",
);
const probeSource = await readFile(
  new URL("../scripts/diagnose_packaged_skill_ecosystem.mjs", import.meta.url),
  "utf8",
);

for (const marker of [
  'data-vrcforge-skill-audit="true"',
  "data-vrcforge-skill-audit-search",
  "data-vrcforge-skill-audit-event-filter",
  "data-vrcforge-skill-audit-row",
  "data-vrcforge-skill-audit-event={event}",
  "data-vrcforge-skill-audit-version={version}",
  "data-vrcforge-skill-audit-field={key}",
  "data-vrcforge-skill-audit-field-value",
  "data-vrcforge-skill-audit-status",
  "data-vrcforge-skill-audit-next",
]) {
  assert.ok(auditSource.includes(marker), `audit UI is missing semantic marker: ${marker}`);
}

assert.ok(runtimeSource.includes("data-vrcforge-save-operation-tool="));
assert.ok(runtimeSource.includes("data-vrcforge-runtime-run-capturable="));
assert.ok(runtimeActivitySource.includes("RuntimeRunRow"));
assert.ok(runtimeActivitySource.includes("data-vrcforge-runtime-activity-panel"));
assert.ok(sidebarSource.includes('semanticId="skills"'));
assert.ok(sidebarSource.includes("data-vrcforge-sidebar-nav={semanticId}"));
assert.ok(sidebarSource.includes("chatId={chat.id}"));
assert.ok(sidebarPrimitiveSource.includes("data-vrcforge-chat-id={chatId || undefined}"));
assert.ok(sidebarPrimitiveSource.includes('data-vrcforge-chat-active={active ? "true" : "false"}'));
for (const marker of [
  'data-vrcforge-path-to-skill-panel="true"',
  "data-vrcforge-path-to-skill-package-id",
  "data-vrcforge-path-to-skill-preview",
  "data-vrcforge-path-to-skill-confirmation",
]) {
  assert.ok(pathToSkillSource.includes(marker), `Path-to-Skill UI is missing semantic marker: ${marker}`);
}

for (const selector of [
  "[data-vrcforge-skill-audit=",
  "[data-vrcforge-skill-audit-row]",
  "[data-vrcforge-skill-audit-field]",
  "[data-vrcforge-save-operation-tool=",
  "[data-vrcforge-chat-id=",
  "[data-vrcforge-runtime-run-tool]",
  "[data-vrcforge-sidebar-nav=",
  "[data-vrcforge-path-to-skill-panel=",
  "[data-vrcforge-path-to-skill-preview]",
  "[data-vrcforge-path-to-skill-confirmation]",
]) {
  assert.ok(probeSource.includes(selector), `packaged probe is missing semantic selector: ${selector}`);
}
assert.ok(probeSource.includes("seedAndActivateContextualRuntimeChat"));
assert.ok(probeSource.includes("session_id: sessionId"));
assert.ok(probeSource.includes("if (!activityPanel.querySelector('[data-vrcforge-current-run-ledger]'))"));
assert.ok(probeSource.includes("activityPanel.querySelector(':scope > button')?.click()"));
assert.ok(probeSource.includes("operationOutsideEnvironmentPanel"));

console.log("skill package audit and Path-to-Skill semantic UI contract passed");
