import type { AgentRuntimeRun } from "./api";

export type PathToSkillOperationSummary = Record<string, unknown>;

export type PathToSkillDraftSeed = {
  revision: number;
  summary: PathToSkillOperationSummary;
};

type CapturedOperationStep = {
  kind: "read" | "skill" | "validation" | "write" | "tool";
  tool: string;
  status: string;
};

const CAPTURABLE_RUN_STATUSES = new Set(["completed", "applied"]);
const STRUCTURED_TOOL_NAME = /^vrcforge_[a-z0-9_]+$/;
const STRUCTURED_STEP_STATUSES = new Set([
  "applied",
  "completed",
  "executed",
  "ok",
  "passed",
  "previewed",
]);
const STRUCTURED_STEP_KINDS = new Set(["read", "skill", "validation", "write"]);

// Recipe selection is intentionally based only on exact backend tool ids. A
// translated title, chat prompt, planner summary, or result text must never
// change the captured business workflow.
const RECIPE_BY_ENTRYPOINT = new Map<string, string>([
  ["vrcforge_optimization_ttt_atlas_plan", "ttt_material_group"],
  ["vrcforge_inspect_outfit_package", "booth_import_preflight"],
  ["vrcforge_optimization_parameter_path_to_skill", "parameter_compression"],
  ["vrcforge_optimization_upload_gate_audit", "pc_quest_upload_pass"],
]);

const RECIPE_ALLOWED_TOOLS = new Map<string, Set<string>>([
  ["ttt_material_group", new Set([
    "vrcforge_health",
    "vrcforge_unity_status",
    "vrcforge_optimization_material_slot_audit",
    "vrcforge_optimization_ttt_atlas_plan",
    "vrcforge_optimization_ttt_atlas_apply_request",
    "vrcforge_optimization_validation_delta",
  ])],
  ["booth_import_preflight", new Set([
    "vrcforge_health",
    "vrcforge_unity_status",
    "vrcforge_scan_project_index",
    "vrcforge_inspect_outfit_package",
    "vrcforge_plan_outfit_import",
    "vrcforge_build_test_readiness",
  ])],
  ["parameter_compression", new Set([
    "vrcforge_health",
    "vrcforge_unity_status",
    "vrcforge_optimization_parameter_budget_audit",
    "vrcforge_optimization_parameter_inventory",
    "vrcforge_optimization_parameter_menu_map",
    "vrcforge_optimization_parameter_animator_usage",
    "vrcforge_optimization_parameter_compressibility_plan",
    "vrcforge_optimization_parameter_vrcfury_compressor_plan",
    "vrcforge_optimization_parameter_behavior_regression",
    "vrcforge_optimization_parameter_path_to_skill",
    "vrcforge_optimization_validation_delta",
  ])],
  ["pc_quest_upload_pass", new Set([
    "vrcforge_health",
    "vrcforge_unity_status",
    "vrcforge_run_validation_report",
    "vrcforge_build_test_readiness",
    "vrcforge_optimization_upload_gate_audit",
    "vrcforge_optimization_upload_gate_fix_plan",
  ])],
]);

const GENERIC_REPLAYABLE_TOOLS = new Set([
  ...[...RECIPE_ALLOWED_TOOLS.values()].flatMap((tools) => [...tools]),
  "vrcforge_scan_materials",
  "vrcforge_plan_shader_tuning",
  "vrcforge_apply_shader_tuning",
  "vrcforge_optimization_plan",
  "vrcforge_preview_add_outfit",
]);

const INTERNAL_CONTROL_TOOLS = new Set([
  "vrcforge_agent_desktop_action",
  "vrcforge_ask_user",
]);

export function buildPathToSkillOperationSummary(
  run: AgentRuntimeRun,
): PathToSkillOperationSummary | null {
  const runStatus = normalizeToken(run.status || run.lastEvent);
  const nextStep = normalizeToken(run.nextStep);
  if (
    !CAPTURABLE_RUN_STATUSES.has(runStatus)
    || Boolean(run.error)
    || ["blocked", "cancelled", "paused"].includes(nextStep)
    || hasUncapturableRunStep(run)
    || hasIncompleteStructuredStep(run)
  ) {
    return null;
  }

  const steps = collectStructuredSteps(run);
  if (!steps.length) {
    return null;
  }

  const matchedRecipeTypes = new Set(
    steps
      .map((step) => RECIPE_BY_ENTRYPOINT.get(step.tool))
      .filter((candidate): candidate is string => Boolean(candidate)),
  );
  // A recipe allowlist represents one exact workflow contract. If one run
  // crosses multiple recipe entrypoints, keep every structured step but fall
  // back to the generic capture so no step is silently omitted by the first
  // recipe's narrower allowlist.
  const recipeCandidate = matchedRecipeTypes.size === 1 ? [...matchedRecipeTypes][0] : undefined;
  const recipeAllowedTools = recipeCandidate ? RECIPE_ALLOWED_TOOLS.get(recipeCandidate) : undefined;
  const recipeType = recipeCandidate && recipeAllowedTools
    && steps.every((step) => recipeAllowedTools.has(step.tool))
    ? recipeCandidate
    : undefined;
  if (!recipeType && steps.some((step) => !GENERIC_REPLAYABLE_TOOLS.has(step.tool))) {
    return null;
  }
  const hasApprovalEvidence = Boolean(run.approvalId || run.approvalIds?.length);
  const hasCheckpointEvidence = Boolean(run.checkpointId || run.checkpointIds?.length);
  const summary: PathToSkillOperationSummary = {
    schema: "vrcforge.operation_summary.v1",
    source: { kind: "runtime_run" },
    workflow: recipeType || "captured_runtime_operation",
    status: "completed",
    steps,
    evidence: {
      approvalRecorded: hasApprovalEvidence,
      checkpointRecorded: hasCheckpointEvidence,
    },
    validation: {
      requiresApproval: hasApprovalEvidence || steps.some((step) => step.kind === "write"),
      requiresCheckpoint: hasCheckpointEvidence,
      requiresRollback: hasCheckpointEvidence,
    },
  };
  if (recipeType) {
    summary.recipeType = recipeType;
  }
  if (run.projectRoot) {
    // Never hand a machine path to the form. The backend still performs the
    // authoritative sanitization, secret rejection, and placeholder binding
    // when previewing this structured summary.
    summary.projectPath = "{{projectPath}}";
  }
  return summary;
}

function collectStructuredSteps(run: AgentRuntimeRun): CapturedOperationStep[] {
  const steps: CapturedOperationStep[] = [];
  const toolsRecordedInSteps = new Set<string>();
  const append = (toolValue: unknown, kindValue: unknown, statusValue: unknown, fallback = false) => {
    const tool = normalizeToken(toolValue);
    if (!STRUCTURED_TOOL_NAME.test(tool) || (fallback && toolsRecordedInSteps.has(tool))) {
      return;
    }
    const status = normalizeStepStatus(statusValue);
    // A terminal run can still contain a failed, blocked, or cancelled
    // attempt. Never rewrite that attempt as a successful reusable step.
    if (!status) {
      return;
    }
    steps.push({
      kind: normalizeStepKind(kindValue, tool),
      tool,
      status,
    });
    if (fallback) {
      toolsRecordedInSteps.add(tool);
    }
  };

  for (const step of run.steps || []) {
    append(step.tool, step.kind, step.status);
    const tool = normalizeToken(step.tool);
    if (STRUCTURED_TOOL_NAME.test(tool)) {
      toolsRecordedInSteps.add(tool);
    }
  }
  // Top-level tool fields mirror the ledger's detailed steps on most runs.
  // Add them only as fallbacks so real repeated calls remain repeated while a
  // summary mirror cannot manufacture a duplicate operation.
  append(run.skillTool, "skill", run.skillStatus, true);
  append(run.writeTool, "write", run.writeStatus, true);
  append(run.targetTool, "write", run.status, true);
  return steps;
}

function hasUncapturableRunStep(run: AgentRuntimeRun): boolean {
  const seenStructuredTools = new Set<string>();
  return (run.steps || []).some((step) => {
    const tool = normalizeToken(step.tool);
    const kind = normalizeToken(step.kind);
    if (!tool) {
      // Vision evidence can accompany a structured operation without being a
      // reusable action. Other tool-less action rows make the path incomplete.
      return Boolean(kind && kind !== "vision");
    }
    if (
      !STRUCTURED_TOOL_NAME.test(tool)
      || INTERNAL_CONTROL_TOOLS.has(tool)
      || tool.startsWith("vrcforge_progress_")
      || seenStructuredTools.has(tool)
    ) {
      return true;
    }
    seenStructuredTools.add(tool);
    return false;
  });
}

function hasIncompleteStructuredStep(run: AgentRuntimeRun): boolean {
  const candidates: Array<[unknown, unknown]> = (run.steps || []).map((step) => [step.tool, step.status]);
  candidates.push(
    [run.skillTool, run.skillStatus],
    [run.writeTool, run.writeStatus],
    [run.targetTool, run.status],
  );
  return candidates.some(([toolValue, statusValue]) => {
    const tool = normalizeToken(toolValue);
    return STRUCTURED_TOOL_NAME.test(tool) && normalizeStepStatus(statusValue) === null;
  });
}

function normalizeStepKind(value: unknown, tool: string): CapturedOperationStep["kind"] {
  const kind = normalizeToken(value);
  if (STRUCTURED_STEP_KINDS.has(kind)) {
    return kind as CapturedOperationStep["kind"];
  }
  if (/(?:validation|audit|report|readiness)/.test(tool)) {
    return "validation";
  }
  if (/(?:scan|read|list|inspect|get|status|preview|plan)/.test(tool)) {
    return "read";
  }
  return "tool";
}

function normalizeStepStatus(value: unknown): string | null {
  const status = normalizeToken(value);
  if (!status) {
    return null;
  }
  if (status === "done" || status === "success" || status === "succeeded") {
    return "completed";
  }
  return STRUCTURED_STEP_STATUSES.has(status) ? status : null;
}

function normalizeToken(value: unknown): string {
  return typeof value === "string" ? value.trim().toLowerCase() : "";
}
