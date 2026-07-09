import type { TFunction } from "i18next";
import type { AgentApproval, AgentRuntimeRun, WorkspaceDiffSummary } from "./api";
import type { ConversationItem } from "./chat-types";
import { buildRuntimeFileReferences } from "./runtime-file-references";
import type { RuntimeFileReference, RuntimePlanChoice, RuntimePlanItem, RuntimeReviewEvidence, RuntimeScheduleItem } from "./runtime-ui-types";
import { formatCount } from "./utils";

type ComponentStatus = { status: string; message?: string } | null | undefined;

export type RuntimeWorkspaceViewModel = {
  workspaceDiffFiles: WorkspaceDiffSummary["files"];
  workspaceDiffChanged: boolean;
  runtimePlanItems: RuntimePlanItem[];
  runtimeFileReferences: RuntimeFileReference[];
  runtimeReviewEvidence: RuntimeReviewEvidence[];
  localizeHealthMessage: (message?: string | null) => string;
  workspaceProjectLabel: string;
  unityBridgeLabel: string;
  unityToolsLabel: string;
  providerCompactLabel: string;
  reviewSummaryLabel: string;
  changeSummaryLabel: string;
};

export function localizeRuntimeHealthMessage(t: TFunction, message?: string | null): string {
  const normalized = (message || "").trim();
  if (!normalized) {
    return "";
  }
  if (normalized === "Backend process is responding.") {
    return t("workspace.backendResponding");
  }
  if (normalized === "Unity MCP bridge online" || normalized === "Unity bridge online") {
    return t("workspace.unityBridgeOnline");
  }
  if (normalized === "Unity MCP bridge is not reachable.") {
    return t("workspace.unityBridgeNotReachable");
  }
  if (normalized === "Unity MCP is connected, but VRCForge Unity tools are missing or incomplete.") {
    return t("workspace.unityToolsMissing");
  }
  return normalized;
}

export function buildRuntimeWorkspaceViewModel({
  t,
  conversation,
  workspaceDiff,
  pendingApprovalItems,
  runtimeRuns,
  runtimeSchedule,
  workspaceProjectLabel,
  runtimeConnected,
  unityBridgeComponent,
  unityToolsComponent,
  vrcForgeToolsReady,
  vrcForgeToolsCount,
  providerLabel,
  model,
  pendingApprovals,
  loadingWorkspaceDiff,
  workspaceDiffError,
  onOpenCheckpoints,
  onToggleWorkspaceDiffReview,
}: {
  t: TFunction;
  conversation: ConversationItem[];
  workspaceDiff: WorkspaceDiffSummary | null;
  pendingApprovalItems: AgentApproval[];
  runtimeRuns: AgentRuntimeRun[];
  runtimeSchedule: RuntimeScheduleItem[];
  workspaceProjectLabel: string;
  runtimeConnected: boolean;
  unityBridgeComponent: ComponentStatus;
  unityToolsComponent: ComponentStatus;
  vrcForgeToolsReady: boolean;
  vrcForgeToolsCount: number;
  providerLabel: string;
  model: string;
  pendingApprovals: number;
  loadingWorkspaceDiff: boolean;
  workspaceDiffError: string;
  onOpenCheckpoints: () => void;
  onToggleWorkspaceDiffReview: () => void;
}): RuntimeWorkspaceViewModel {
  const workspaceDiffFiles = workspaceDiff?.files ?? [];
  const workspaceDiffChanged = workspaceDiff?.status === "changed" && workspaceDiff.fileCount > 0;
  const localizeHealthMessage = (message?: string | null) => localizeRuntimeHealthMessage(t, message);
  const runtimePlanItems = buildRuntimePlanItems({ t, conversation, runtimeSchedule });
  const runtimeFileReferences = buildRuntimeFileReferences(conversation, workspaceDiffFiles);
  const runtimeReviewEvidence = buildRuntimeReviewEvidence({
    t,
    pendingApprovalItems,
    runtimeRuns,
    workspaceDiff,
    workspaceDiffChanged,
    onOpenCheckpoints,
    onToggleWorkspaceDiffReview,
  });
  const unityBridgeLabel = !runtimeConnected
    ? t("workspace.coreOffline")
    : unityBridgeComponent?.status === "ok"
      ? t("workspace.unityBridgeOnline")
      : localizeHealthMessage(unityBridgeComponent?.message) || t("workspace.unityNotConnected");
  const unityToolsLabel = vrcForgeToolsReady
    ? t("workspace.vrcTools", { count: formatCount(vrcForgeToolsCount) })
    : localizeHealthMessage(unityToolsComponent?.message) || (runtimeConnected ? t("workspace.avatarToolsNotReady") : t("workspace.coreOffline"));
  const providerCompactLabel = `${providerLabel}${model ? ` / ${model}` : ""}`;
  const reviewSummaryLabel = pendingApprovals
    ? t("workspace.pendingApprovals", { count: formatCount(pendingApprovals) })
    : t("workspace.noPendingApprovals");
  const changeSummaryLabel = loadingWorkspaceDiff
    ? t("workspace.refreshing")
    : workspaceDiffError
      ? t("workspace.diffUnavailable")
      : workspaceDiff
        ? workspaceDiffChanged
          ? t("workspace.changedFiles", { count: formatCount(workspaceDiff.fileCount) })
          : workspaceDiff.status === "clean"
            ? t("workspace.clean")
            : workspaceDiff.status
        : runtimeConnected
          ? t("workspace.notLoaded")
          : t("workspace.coreOffline");

  return {
    workspaceDiffFiles,
    workspaceDiffChanged,
    runtimePlanItems,
    runtimeFileReferences,
    runtimeReviewEvidence,
    localizeHealthMessage,
    workspaceProjectLabel,
    unityBridgeLabel,
    unityToolsLabel,
    providerCompactLabel,
    reviewSummaryLabel,
    changeSummaryLabel,
  };
}

function buildRuntimePlanItems({
  t,
  conversation,
  runtimeSchedule,
}: {
  t: TFunction;
  conversation: ConversationItem[];
  runtimeSchedule: RuntimeScheduleItem[];
}): RuntimePlanItem[] {
  const items: RuntimePlanItem[] = runtimeSchedule.map((item) => ({
    id: `schedule-${item.id}`,
    title: item.title,
    meta: item.meta,
    status: item.status,
  }));
  const latestAgent = [...conversation].reverse().find((item) => item.type === "agent");
  if (latestAgent?.type !== "agent") {
    return items.slice(0, 8);
  }
  const response = latestAgent.response;
  const promptChoices = normalizeRuntimeChoices(response.choicePrompt?.choices ?? response.plan.choices);
  const question = response.choicePrompt?.question || (promptChoices.length ? response.plan.nextStep || response.plan.summary : "");
  if (question && promptChoices.length) {
    items.unshift({
      id: `question-${response.choicePrompt?.id || response.turnId || response.turn_id || latestAgent.id}`,
      title: question,
      meta: t("workspace.awaitingUserChoice"),
      status: "question",
      choices: promptChoices,
    });
  }
  if (response.plan.summary) {
    items.push({
      id: `plan-${response.turnId || response.turn_id || latestAgent.id}`,
      title: response.plan.summary,
      meta: response.plan.expectedResult || response.plan.nextStep || response.plan.plannerLabel || response.plan.planner,
      status: response.ok ? "completed" : "running",
    });
  }
  for (const step of response.steps ?? []) {
    const title = step.summary || step.tool || step.kind || t("workspace.planStep");
    items.push({
      id: `step-${response.turnId || response.turn_id || latestAgent.id}-${step.index ?? items.length}`,
      title,
      meta: [step.kind, step.providerLabel || step.provider, step.model].filter(Boolean).join(" / "),
      status: step.status || "completed",
    });
  }
  return items.slice(0, 8);
}

function normalizeRuntimeChoices(choices?: Array<{ id?: string; label?: string; description?: string; value?: string }>): RuntimePlanChoice[] {
  return (choices ?? [])
    .map((choice, index) => ({
      id: choice.id || `choice-${index + 1}`,
      label: String(choice.label || choice.value || "").trim(),
      description: choice.description,
      value: choice.value,
    }))
    .filter((choice) => choice.label);
}

function buildRuntimeReviewEvidence({
  t,
  pendingApprovalItems,
  runtimeRuns,
  workspaceDiff,
  workspaceDiffChanged,
  onOpenCheckpoints,
  onToggleWorkspaceDiffReview,
}: {
  t: TFunction;
  pendingApprovalItems: AgentApproval[];
  runtimeRuns: AgentRuntimeRun[];
  workspaceDiff: WorkspaceDiffSummary | null;
  workspaceDiffChanged: boolean;
  onOpenCheckpoints: () => void;
  onToggleWorkspaceDiffReview: () => void;
}): RuntimeReviewEvidence[] {
  const items: RuntimeReviewEvidence[] = [];
  for (const approval of pendingApprovalItems.slice(0, 4)) {
    items.push({
      id: `approval-${approval.id}`,
      kind: "approval",
      title: approval.targetTool || approval.preview?.command || t("workspace.approvalEvidence"),
      meta: t("workspace.approvalEvidenceMeta", { status: approval.status || "pending" }),
      status: approval.status,
    });
  }
  const seenCheckpoints = new Set<string>();
  for (const run of runtimeRuns) {
    const checkpointIds = [run.checkpointId, ...(run.checkpointIds ?? [])].filter(Boolean) as string[];
    for (const checkpointId of checkpointIds) {
      if (seenCheckpoints.has(checkpointId)) {
        continue;
      }
      seenCheckpoints.add(checkpointId);
      items.push({
        id: `checkpoint-${checkpointId}`,
        kind: "checkpoint",
        title: checkpointId,
        meta: run.targetTool || run.writeTool || run.messageSummary || t("workspace.checkpointEvidence"),
        status: run.status,
        action: onOpenCheckpoints,
      });
    }
  }
  const recentRunWithApproval = runtimeRuns.find((run) => (run.approvalId || (run.approvalIds ?? []).length) && !run.checkpointId);
  if (recentRunWithApproval && items.length < 6) {
    items.push({
      id: `run-approval-${recentRunWithApproval.id || recentRunWithApproval.turnId || recentRunWithApproval.clientTurnId}`,
      kind: "run",
      title: recentRunWithApproval.targetTool || recentRunWithApproval.writeTool || t("workspace.runEvidence"),
      meta: recentRunWithApproval.status || recentRunWithApproval.lastEvent || "",
      status: recentRunWithApproval.status,
    });
  }
  if (workspaceDiffChanged) {
    items.push({
      id: "git-diff",
      kind: "diff",
      title: t("workspace.gitDiffEvidence"),
      meta: t("workspace.gitDiffEvidenceMeta", {
        count: formatCount(workspaceDiff?.fileCount || 0),
        additions: formatCount(workspaceDiff?.additions || 0),
        deletions: formatCount(workspaceDiff?.deletions || 0),
      }),
      status: workspaceDiff?.status,
      action: onToggleWorkspaceDiffReview,
    });
  }
  return items.slice(0, 8);
}
