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
const chatCardSource = await readFile(
  new URL("../src/components/chat/conversation-card.tsx", import.meta.url),
  "utf8",
);
const chatWorkspaceSource = await readFile(
  new URL("../src/components/chat/chat-workspace.tsx", import.meta.url),
  "utf8",
);
const appSource = await readFile(
  new URL("../src/App.tsx", import.meta.url),
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
assert.ok(
  !appSource.includes("RuntimeActivityPanel") && !appSource.includes("activityPanel={runtimeActivityPanel}"),
  "the central runtime ledger must not remain in chat",
);
assert.ok(
  chatWorkspaceSource.includes("matchPathToSkillRuntimeOperation(item.response, runtimeRuns)"),
  "Save operation as Skill must use the exact agent-response/runtime-run match",
);
assert.ok(chatCardSource.includes("data-vrcforge-save-operation-as-skill"));
assert.ok(chatCardSource.includes("data-vrcforge-agent-client-turn="));
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
const identityFieldStart = pathToSkillSource.indexOf("data-vrcforge-path-to-skill-package-id");
const identityFieldEnd = pathToSkillSource.indexOf("value={skillName}", identityFieldStart);
assert.ok(identityFieldStart >= 0 && identityFieldEnd > identityFieldStart);
assert.ok(
  pathToSkillSource.slice(identityFieldStart, identityFieldEnd).includes("invalidatePreview();"),
  "changing Path-to-Skill identity after preview must invalidate preview and confirmation",
);
const invalidatePreviewStart = pathToSkillSource.indexOf("function invalidatePreview() {");
const buildRequestStart = pathToSkillSource.indexOf("function buildRequest()", invalidatePreviewStart);
assert.ok(invalidatePreviewStart >= 0 && buildRequestStart > invalidatePreviewStart);
assert.ok(
  pathToSkillSource.slice(invalidatePreviewStart, buildRequestStart).includes("invalidateConfirmation();"),
  "preview invalidation must clear the user's prior confirmation",
);

for (const selector of [
  "[data-vrcforge-skill-audit=",
  "[data-vrcforge-skill-audit-row]",
  "[data-vrcforge-skill-audit-field]",
  "[data-vrcforge-save-operation-tool=",
  "[data-vrcforge-chat-id=",
  "[data-vrcforge-agent-client-turn",
  "[data-vrcforge-sidebar-nav=",
  "[data-vrcforge-path-to-skill-panel=",
  "[data-vrcforge-path-to-skill-preview]",
  "[data-vrcforge-path-to-skill-confirmation]",
]) {
  assert.ok(probeSource.includes(selector), `packaged probe is missing semantic selector: ${selector}`);
}
assert.ok(probeSource.includes("seedAndActivateContextualRuntimeChat"));
assert.ok(probeSource.includes("session_id: sessionId"));
assert.ok(probeSource.includes("persistContextualRuntimeResponse"));
assert.ok(probeSource.includes("checks.centralRuntimeLedgerAbsent = !document.querySelector('[data-vrcforge-runtime-activity-panel]')"));
assert.ok(probeSource.includes("checks.operationInMatchedAgentMessage = agentMessage.contains(target)"));
assert.ok(probeSource.includes("operationOutsideEnvironmentPanel"));

console.log("skill package audit and Path-to-Skill semantic UI contract passed");
