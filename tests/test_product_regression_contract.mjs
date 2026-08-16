import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..");
const contract = await readFile(resolve(root, "docs/PRODUCT_REGRESSION_CONTRACT.md"), "utf8");
const compact = (value) => value.replace(/\s+/g, " ");

function section(id, nextId) {
  const start = contract.indexOf(`### ${id}`);
  assert.notEqual(start, -1, `${id} must remain a stable product contract`);
  const end = nextId ? contract.indexOf(`### ${nextId}`, start + id.length) : contract.length;
  return contract.slice(start, end < 0 ? contract.length : end);
}

const naturalLoop = section("AGT-002", "AGT-003");
assert.match(naturalLoop, /no fixed tool-call/i);
assert.match(naturalLoop, /toolCallsUsed.*telemetry/is);
assert.match(naturalLoop, /unattended.*finite/is);
assert.match(compact(naturalLoop), /foreground request must not inherit.*background-only limit/i);

const followup = section("AGT-008", "APR-001");
assert.match(followup, /durable.*FIFO/is);
assert.match(followup, /no arbitrary message[\s\S]*count cap/i);
assert.match(compact(followup), /Persistence failure\/backpressure.*visibly retryable/i);

const generalAgent = section("AGT-009", "APR-001");
const generalAgentText = compact(generalAgent);
assert.match(generalAgentText, /ordinary General Agent/i);
assert.match(generalAgentText, /list.*read.*find.*search/i);
assert.match(generalAgentText, /must not expose or invoke Unity/i);
assert.match(generalAgentText, /semantically equivalent.*observation/i);
assert.match(generalAgentText, /planner_no_progress/i);
assert.match(generalAgentText, /selected General project.*absolute path written by the user/i);
assert.match(generalAgentText, /symlink or reparse.*credential.*redacts/i);
assert.match(generalAgentText, /line growth only as a diagnostic/i);
assert.match(generalAgentText, /per-hunk rationale.*Gateway-owned coordination or trust-boundary/i);
assert.match(generalAgentText, /justified growth is never rejected by line count alone/i);

const boundInstructions = section("AGT-011", "AGT-012");
const boundInstructionsText = compact(boundInstructions);
assert.match(boundInstructionsText, /App-global.*AGENTS\.md/i);
assert.match(boundInstructionsText, /project root.*AGENTS\.md/i);
assert.match(boundInstructionsText, /cannot grant writes.*bypass supervision/i);

const profiledTools = section("AGT-012", "APR-001");
const profiledToolsText = compact(profiledTools);
assert.match(profiledToolsText, /General Mode exposes Core plus General/i);
assert.match(profiledToolsText, /Unity Project Mode exposes Core plus General plus Unity/i);
assert.match(profiledToolsText, /Read\/List\/Glob\/Grep remain available/i);
assert.match(profiledToolsText, /ordinary Shell.*cwd.*direct project-path reference/i);
assert.match(profiledToolsText, /unity_project_access.*current registered Unity project/i);
assert.match(profiledToolsText, /not an adversarial security boundary/i);
assert.match(profiledToolsText, /must not grow into an OS sandbox.*Shadow Workspace/i);

const approval = section("APR-004", "UX-001");
assert.match(approval, /detail-first/i);
assert.match(approval, /must never decide an approval/i);

const rail = section("UX-001", "UX-002");
const railText = compact(rail);
for (const label of ["Progress", "Sub Agents", "Sources", "Agent TODO", "Environment Information", "User Attachment Sources"]) {
  assert.match(railText, new RegExp(label));
}
assert.match(railText, /absent when.*no attachments/is);
assert.match(railText, /Memory and Skills.*rather than.*synthetic `Context`/is);
assert.match(railText, /Locate appears only while.*owning message remains.*Open appears only while.*preview exists/i);
assert.match(railText, /Compacted metadata exposes neither inert control/i);
assert.match(railText, /no Goal-management or Workflow-management card/i);

const todo = section("UX-002", "UX-002A");
const todoText = compact(todo);
assert.match(todoText, /Pending items use a muted gray/i);
assert.match(todoText, /reduced-motion-aware breathing/i);
assert.match(todoText, /title becomes muted.*struck through/i);

const subagents = section("UX-003", "UX-004");
const subagentText = compact(subagents);
assert.match(subagentText, /main chat remains mounted/i);
assert.match(subagentText, /own scroll owner/i);

const timeline = section("UX-004", "UX-005");
const timelineText = compact(timeline);
for (const fact of ["planner", "tool calls/results", "file edits", "commands", "Sub Agent"]) {
  assert.match(timelineText, new RegExp(fact, "i"));
}
assert.match(timelineText, /sequence and timestamp/i);
assert.match(timelineText, /Reasoning\/CoT.*never enter/i);
assert.match(timelineText, /lifecycle is projected only from the durable task registry/i);
assert.match(timelineText, /keeps its own chronological `tool_call` and `tool_result`/i);
assert.match(timelineText, /exact full result persisted/i);
assert.match(timelineText, /internally scrolling card/i);
assert.match(timelineText, /1000 characters.*tail marker/i);
assert.match(timelineText, /repeated action IDs.*FIFO/i);

const copy = section("UX-005", "UX-006");
const copyText = compact(copy);
assert.match(copyText, /visible prose only/i);
assert.match(copyText, /Planner\/tool\/result JSON.*excluded/i);

const ledger = section("UX-007", "UX-008");
assert.match(compact(ledger), /Neither the chat center nor.*project right rail.*Run Ledger/i);

const palette = section("UX-008", "LAT-001");
assert.match(palette, /`\+` and `\/` use the same compact command-palette/i);

const projectTypes = section("UX-009", "VIS-001");
const projectTypesText = compact(projectTypes);
assert.match(projectTypesText, /exactly `general` or `unity`/i);
assert.match(projectTypesText, /opening or discovering a Unity Editor.*must not.*convert/i);
assert.match(projectTypesText, /General.*absolute existing directory/i);
assert.match(projectTypesText, /Unity.*Assets.*Packages.*ProjectSettings/i);

const scrollOwner = section("UX-013", "UX-014");
const scrollOwnerText = compact(scrollOwner);
assert.match(scrollOwnerText, /mouse wheel.*must not start.*smooth programmatic scroll/i);
assert.match(scrollOwnerText, /Automatic content-follow.*immediate scroll.*already pinned/i);
assert.match(scrollOwnerText, /first-arrival bottom bounce/i);

const diagnosticIdentity = section("UX-014", "VIS-001");
const diagnosticIdentityText = compact(diagnosticIdentity);
assert.match(diagnosticIdentityText, /local identity map.*only on the Developer page.*Developer Options is enabled/i);
assert.match(diagnosticIdentityText, /no local identity map.*normal Settings/i);

const provider = section("PRV-005", "PKG-001");
const providerText = compact(provider);
assert.match(providerText, /first-byte.*idle.*overall/i);
assert.match(providerText, /Reasoning activity.*fixed safe phase.*chain-of-thought.*never enter user-visible output/i);

const unityPackage = section("PKG-001", "PKG-002");
const unityPackageText = compact(unityPackage);
assert.match(unityPackageText, /VRCForgeApprovedObjectReceipt\.cs/i);
assert.match(unityPackageText, /c03999e57815100961016fab067f9c2b/i);
assert.match(unityPackageText, /#if UNITY_EDITOR.*#endif/i);
assert.match(unityPackageText, /EditorUtility.*GlobalObjectId.*Assembly-CSharp\.dll/i);
assert.match(unityPackageText, /Editor and Player compile.*Build & Test/i);

for (const current of [naturalLoop, followup, approval, todo, subagents, copy, ledger, palette, provider]) {
  assert.match(current, /1\.5\.1/, "current contracts must keep their version annotation");
}

for (const current of [rail, timeline, boundInstructions, profiledTools, scrollOwner, diagnosticIdentity]) {
  assert.match(current, /1\.6\.2/, "changed 1.6.2 contracts must keep their version annotation");
}

for (const current of [generalAgent, projectTypes]) {
  assert.match(current, /1\.6\.0/, "new General/Unity contracts must bind to 1.6.0");
}

console.log("product regression contract: ok");
