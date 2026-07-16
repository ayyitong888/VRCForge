import assert from "node:assert/strict";
import test from "node:test";

import { buildPathToSkillOperationSummary } from "../src/lib/path-to-skill-context.ts";

test("completed structured runtime run becomes a portable operation summary", () => {
  const summary = buildPathToSkillOperationSummary({
    id: "private-run-id",
    status: "completed",
    sessionId: "private-session-id",
    messageSummary: "localized user prompt must not be copied",
    planSummary: "private planner prose",
    providerLabel: "Private Provider",
    model: "private-model",
    projectRoot: "Q:\\private-fixture\\Avatar",
    approvalIds: ["approval-private-id"],
    checkpointIds: ["checkpoint-private-id"],
    resultSummary: { display: "must-not-copy" },
    steps: [
      {
        kind: "validation",
        tool: "vrcforge_run_validation_report",
        status: "passed",
        summary: "localized display text must not be copied",
      },
      {
        kind: "skill",
        tool: "vrcforge_build_test_readiness",
        status: "completed",
      },
    ],
  });

  assert.deepEqual(summary, {
    schema: "vrcforge.operation_summary.v1",
    source: { kind: "runtime_run" },
    workflow: "captured_runtime_operation",
    status: "completed",
    steps: [
      { kind: "validation", tool: "vrcforge_run_validation_report", status: "passed" },
      { kind: "skill", tool: "vrcforge_build_test_readiness", status: "completed" },
    ],
    evidence: { approvalRecorded: true, checkpointRecorded: true },
    validation: { requiresApproval: true, requiresCheckpoint: true, requiresRollback: true },
    projectPath: "{{projectPath}}",
  });
  const serialized = JSON.stringify(summary);
  for (const privateValue of [
    "private-fixture",
    "private-run-id",
    "private-session-id",
    "private planner prose",
    "Private Provider",
    "private-model",
    "approval-private-id",
    "checkpoint-private-id",
    "must-not-copy",
    "localized display text",
    "localized user prompt",
  ]) {
    assert.equal(serialized.includes(privateValue), false, privateValue);
  }
});

test("exact structured entrypoint selects a recipe but display text cannot", () => {
  const recipe = buildPathToSkillOperationSummary({
    status: "completed",
    steps: [{ kind: "skill", tool: "vrcforge_optimization_parameter_path_to_skill", status: "executed" }],
  });
  assert.equal(recipe?.workflow, "parameter_compression");
  assert.equal(recipe?.recipeType, "parameter_compression");
  assert.deepEqual(recipe?.steps, [
    { kind: "skill", tool: "vrcforge_optimization_parameter_path_to_skill", status: "executed" },
  ]);

  const displayOnly = buildPathToSkillOperationSummary({
    status: "completed",
    messageSummary: "parameter compression",
    planSummary: "vrcforge_optimization_parameter_path_to_skill",
    steps: [{ tool: "vrcforge_run_validation_report", status: "completed" }],
  });
  assert.equal(displayOnly?.workflow, "captured_runtime_operation");
  assert.equal("recipeType" in displayOnly, false);
});

test("multiple distinct recipe entrypoints fall back to a generic complete capture", () => {
  const summary = buildPathToSkillOperationSummary({
    status: "completed",
    steps: [
      { kind: "read", tool: "vrcforge_inspect_outfit_package", status: "executed" },
      { kind: "validation", tool: "vrcforge_optimization_upload_gate_audit", status: "passed" },
    ],
  });

  assert.equal(summary?.workflow, "captured_runtime_operation");
  assert.equal("recipeType" in summary, false);
  assert.deepEqual(summary?.steps, [
    { kind: "read", tool: "vrcforge_inspect_outfit_package", status: "executed" },
    { kind: "validation", tool: "vrcforge_optimization_upload_gate_audit", status: "passed" },
  ]);
});

test("one recipe entrypoint plus an out-of-recipe safe step falls back to generic", () => {
  const summary = buildPathToSkillOperationSummary({
    status: "completed",
    steps: [
      { kind: "read", tool: "vrcforge_inspect_outfit_package", status: "executed" },
      { kind: "read", tool: "vrcforge_scan_materials", status: "completed" },
    ],
  });

  assert.equal(summary?.workflow, "captured_runtime_operation");
  assert.equal("recipeType" in summary, false);
  assert.equal(summary?.steps?.length, 2);
});

test("repeated structured calls are rejected because private call arguments cannot be replayed", () => {
  const summary = buildPathToSkillOperationSummary({
    status: "completed",
    skillTool: "vrcforge_run_validation_report",
    skillStatus: "passed",
    steps: [
      { kind: "validation", tool: "vrcforge_run_validation_report", status: "passed" },
      { kind: "validation", tool: "vrcforge_run_validation_report", status: "passed" },
    ],
  });

  assert.equal(summary, null);
});

test("duplicate top-level mirror fields collapse to one fallback operation", () => {
  const summary = buildPathToSkillOperationSummary({
    status: "completed",
    skillTool: "vrcforge_build_test_readiness",
    skillStatus: "passed",
    writeTool: "vrcforge_build_test_readiness",
    writeStatus: "passed",
  });

  assert.deepEqual(summary?.steps, [
    { kind: "skill", tool: "vrcforge_build_test_readiness", status: "passed" },
  ]);
});

test("internal progress and user-question controls are not reusable operation steps", () => {
  for (const tool of ["vrcforge_progress_update", "vrcforge_ask_user", "vrcforge_agent_desktop_action"]) {
    assert.equal(
      buildPathToSkillOperationSummary({
        status: "completed",
        steps: [{ kind: "skill", tool, status: "executed" }],
      }),
      null,
      tool,
    );
  }
});

test("mixed shell and structured runs are not offered as a lossy reusable path", () => {
  assert.equal(
    buildPathToSkillOperationSummary({
      status: "completed",
      steps: [
        { kind: "shell", tool: "powershell", status: "completed" },
        { kind: "validation", tool: "vrcforge_run_validation_report", status: "passed" },
      ],
    }),
    null,
  );
});

test("an applied approval event uses its structured target tool without evidence ids", () => {
  const summary = buildPathToSkillOperationSummary({
    status: "applied",
    targetTool: "vrcforge_apply_shader_tuning",
    approvalId: "approval-must-not-copy",
    checkpointId: "checkpoint-must-not-copy",
    messageSummary: "display text must not become a step",
  });

  assert.deepEqual(summary?.steps, [
    { kind: "write", tool: "vrcforge_apply_shader_tuning", status: "applied" },
  ]);
  assert.deepEqual(summary?.validation, {
    requiresApproval: true,
    requiresCheckpoint: true,
    requiresRollback: true,
  });
  const serialized = JSON.stringify(summary);
  assert.equal(serialized.includes("approval-must-not-copy"), false);
  assert.equal(serialized.includes("checkpoint-must-not-copy"), false);
  assert.equal(serialized.includes("display text"), false);
});

test("nonterminal runs and runs without structured VRCForge tools are not offered", () => {
  assert.equal(
    buildPathToSkillOperationSummary({
      status: "running",
      steps: [{ tool: "vrcforge_run_validation_report", status: "completed" }],
    }),
    null,
  );
  assert.equal(
    buildPathToSkillOperationSummary({
      status: "completed",
      steps: [{ kind: "shell", tool: "powershell", status: "completed" }],
    }),
    null,
  );
});

test("failed, blocked, pending, or paused attempts are never offered as completed reusable paths", () => {
  const failed = buildPathToSkillOperationSummary({
    status: "completed",
    steps: [
      { kind: "write", tool: "vrcforge_failed_write", status: "failed" },
      { kind: "write", tool: "vrcforge_blocked_write", status: "blocked" },
      { kind: "validation", tool: "vrcforge_verified_read", status: "succeeded" },
    ],
  });
  assert.equal(failed, null);
  assert.equal(
    buildPathToSkillOperationSummary({
      status: "completed",
      writeTool: "vrcforge_request_apply",
      writeStatus: "pending",
    }),
    null,
  );
  assert.equal(
    buildPathToSkillOperationSummary({
      status: "completed",
      nextStep: "paused",
      steps: [{ kind: "skill", tool: "vrcforge_run_validation_report", status: "executed" }],
    }),
    null,
  );
  assert.equal(
    buildPathToSkillOperationSummary({
      status: "completed",
      steps: [{ kind: "skill", tool: "vrcforge_status_missing" }],
    }),
    null,
  );
});
