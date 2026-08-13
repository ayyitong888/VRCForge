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

const approval = section("APR-004", "UX-001");
assert.match(approval, /detail-first/i);
assert.match(approval, /must never decide an approval/i);

const rail = section("UX-001", "UX-002");
const railText = compact(rail);
for (const label of ["Agent TODO", "Sub Agents", "Environment Information", "User Attachment Sources"]) {
  assert.match(railText, new RegExp(label));
}
assert.match(railText, /locates its owning message only while.*Open action appears only when.*preview.*available/i);
assert.match(railText, /Compacted metadata.*no inert Locate\/Open control/i);

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

const provider = section("PRV-005", "PKG-001");
const providerText = compact(provider);
assert.match(providerText, /first-byte.*idle.*overall/i);
assert.match(providerText, /Reasoning activity.*fixed safe phase.*chain-of-thought.*never enter user-visible output/i);

for (const current of [naturalLoop, followup, approval, rail, todo, subagents, timeline, copy, ledger, palette, provider]) {
  assert.match(current, /1\.5\.1/, "current contracts must keep their version annotation");
}

for (const current of [generalAgent, projectTypes]) {
  assert.match(current, /1\.6\.0/, "new General/Unity contracts must bind to 1.6.0");
}

console.log("product regression contract: ok");
