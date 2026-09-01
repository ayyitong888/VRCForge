import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const workspace = await readFile(new URL("../src/components/checkpoints/checkpoint-workspace.tsx", import.meta.url), "utf8");
const controller = await readFile(new URL("../src/hooks/use-checkpoint-workspace-controller.ts", import.meta.url), "utf8");

assert.ok(workspace.includes("interruptedRecoveries.length > 0 || recoveryPreview"), "empty interrupted writes must not render a large card");
assert.ok(workspace.includes("<details") && workspace.includes("checkpoint.adjustmentTimeline"), "adjustment timeline must be collapsible");
assert.ok(workspace.includes("data-vrcforge-checkpoint-list"), "checkpoint list needs a semantic card marker");
assert.ok(workspace.includes("data-vrcforge-checkpoint-detail"), "checkpoint detail needs a semantic card marker");
assert.match(workspace, /title=\{checkpoint\.id\}/, "checkpoint IDs should remain available as tooltip metadata");
assert.match(workspace, /checkpoint\.targetTool \|\| checkpoint\.id/, "checkpoint rows should lead with the operation rather than the checkpoint ID");
assert.doesNotMatch(workspace, /\{selectedProjectPath \? <div/, "the full project path should not occupy the checkpoint overview");
assert.ok(workspace.includes("adjustmentPreview ?") && workspace.includes("preview ?") && workspace.includes("recoveryPreview ?"), "detail area must select one preview type");
assert.ok(workspace.includes("onRestore") && workspace.includes("onRestoreRecovery") && workspace.includes("onApplyAdjustment"), "restore and adjustment actions must remain available");
assert.ok(controller.includes("setRecoveryPreview(null)") && controller.includes("setAdjustmentPreview(null)"), "checkpoint preview must clear stale alternate previews");
assert.ok(controller.includes("setCheckpointPreview(null)"), "alternate previews must clear stale checkpoint preview");
assert.ok(controller.includes("checkpointLoadInflightRef"), "checkpoint list loads must deduplicate concurrent effects");
assert.ok(controller.includes("fetchCheckpoints(targetEndpoint") && controller.includes("Promise.allSettled"), "lightweight checkpoint list must render before ancillary archive panels");

console.log("checkpoint workspace disclosure and single-detail contract passed");
