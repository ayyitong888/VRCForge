import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { buildTimelinePresentation } from "../src/lib/chat-timeline-presentation.ts";

const event = (id, sequence, kind, payload) => ({ id, sequence, kind, payload, timestamp: new Date(2026, 0, 1, 0, 0, sequence).toISOString() });
const invocations = (events) => buildTimelinePresentation(events).entries.flatMap((entry) => entry.type === "batch" ? entry.invocations : []);
for (const kind of ["file_edit", "command"]) {
  for (const status of ["executed", "failed", "pending_approval", "rejected"]) {
    const rows = invocations([
      event("prepared", 0, kind, { actionId: "write-a", tool: "write_tool", status: "started", summary: "Prepared supervised execution." }),
      event("result", 1, "tool_result", { actionId: "write-a", tool: "write_tool", status, summary: "Authoritative result" }),
    ]);
    assert.equal(rows.length, 1, "preparation and authoritative result are one request");
    assert.equal(rows[0].status, status, "a preparation never remains running after its own result");
  }
}
const repeated = invocations([
  event("a1", 0, "file_edit", { actionId: "repeat", status: "started" }),
  event("r1", 1, "tool_result", { actionId: "repeat", status: "failed" }),
  event("a2", 2, "file_edit", { actionId: "repeat", status: "started" }),
  event("r2", 3, "tool_result", { actionId: "repeat", status: "executed" }),
]);
assert.deepEqual(repeated.map((row) => row.status), ["failed", "executed"], "reused action IDs retain separate occurrences");
const pending = invocations([event("pending", 0, "tool_call", { actionId: "new", status: "prepared" })]);
assert.equal(pending[0].status, "prepared", "absence of result cannot imply a successful write");
if (process.env.VRCFORGE_CURRENT_TURN_REPLAY_PATH) {
  const replay = JSON.parse(await readFile(process.env.VRCFORGE_CURRENT_TURN_REPLAY_PATH, "utf8"));
  let checked = 0;
  for (const item of replay.items) {
    const events = item.type === "agent" ? item.response.timeline : item.timeline;
    if (!events) continue;
    const rows = invocations(events);
    for (const preparation of events.filter((entry) => ["file_edit", "command"].includes(entry.kind) && entry.payload?.actionId)) {
      const index = events.indexOf(preparation);
      const result = events.slice(index + 1).find((entry) => entry.kind === "tool_result" && entry.payload?.actionId === preparation.payload.actionId);
      if (!result) continue;
      const row = rows.find((entry) => entry.id === preparation.id);
      assert.equal(row.status, result.payload.status, "original write receipt settles its exact preparation");
      checked += 1;
    }
  }
  assert.ok(checked > 0, "original replay contains supervised preparations with receipts");
  console.log(`original supervised preparations settled: ${checked}`);
}
console.log("prepared request / result occurrence contract passed");
