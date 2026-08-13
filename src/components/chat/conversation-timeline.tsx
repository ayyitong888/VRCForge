import { AlertTriangle, ChevronDown, ChevronRight, Eye, ListChecks, Pencil, TerminalSquare, Wrench } from "lucide-react";
import { ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import { presentApproval } from "../../lib/approval-presentation";
import type { AgentApproval, AgentRuntimeResponse, AgentShellResult, AgentSkillResult } from "../../lib/api";
import {
  buildTimelinePresentation,
  hasDurableExecutionEvents,
  runtimeTerminalStatusKey,
  type TimelineBatchKind,
} from "../../lib/chat-timeline-presentation";
import type { ApprovalActionState, ChatTimelineEvent } from "../../lib/chat-types";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";
import { ChatMarkdown } from "./chat-markdown";

type AgentTimelineStep = NonNullable<AgentRuntimeResponse["steps"]>[number] & {
  actionId?: string;
  result?: unknown;
  outcome?: Record<string, unknown>;
  error?: string;
  historical?: boolean;
};

export type OrderedAgentStep = {
  step: AgentTimelineStep;
  sourceIndex: number;
};

export function buildAgentTimelineRows({
  response,
  shell,
  vision,
  skill,
  write,
  approval,
  approvalAction,
  onModifyApproval,
  showIntent,
  nextStep,
  planLabel,
  providerLine,
  awaitingApproval,
  elapsedSeconds,
  t,
}: {
  response: AgentRuntimeResponse;
  shell?: AgentRuntimeResponse["shell"];
  vision?: AgentRuntimeResponse["vision"];
  skill?: AgentSkillResult;
  write?: AgentRuntimeResponse["write"];
  approval?: AgentApproval | null;
  approvalAction?: ApprovalActionState;
  onModifyApproval?: (approval: AgentApproval) => void;
  showIntent: boolean;
  nextStep: string;
  planLabel: string;
  providerLine: string;
  awaitingApproval: boolean;
  elapsedSeconds?: number;
  t: typeof i18n.t;
}): ReactNode[] {
  const durableTimeline = (response.timeline || []) as ChatTimelineEvent[];
  const durableRows = buildDurableTimelineRows(
    durableTimeline,
    elapsedSeconds,
    response.plan.plannerFailure?.code,
  );
  if (durableRows.length) {
    const durableKinds = new Set(durableTimeline.map((event) => event.kind || ""));
    const rows = [...durableRows];
    const orderedSteps = normalizeAgentSteps(response.steps);
    const hasDurableExecution = hasDurableExecutionEvents(durableTimeline);
    if (!hasDurableExecution && orderedSteps.length) {
      rows.push(...buildAgentTimelineRowsFromSteps({
        steps: orderedSteps,
        response,
        shell,
        vision,
        skill,
        write,
        approval,
        approvalAction,
        onModifyApproval,
        providerLine,
        planLabel,
        awaitingApproval,
        elapsedSeconds,
        t,
      }));
    }
    if (!durableKinds.has("assistant")) {
      const fallbackAnswer = response.plan.reply || response.plan.summary;
      if (fallbackAnswer) {
        rows.push(renderPlanReplyRow(fallbackAnswer, planLabel, elapsedSeconds, t));
      }
    }
    return rows;
  }
  const orderedSteps = normalizeAgentSteps(response.steps);
  const legacyFallback = orderedSteps.length === 0;
  if (legacyFallback) {
    return buildLegacyAgentTimelineRows({
      response,
      shell,
      vision,
      skill,
      write,
      approval,
      approvalAction,
      onModifyApproval,
      showIntent,
      nextStep,
      planLabel,
      providerLine,
      awaitingApproval,
      elapsedSeconds,
      t,
    });
  }

  const rows = buildAgentTimelineRowsFromSteps({
    steps: orderedSteps,
    response,
    shell,
    vision,
    skill,
    write,
    approval,
    approvalAction,
    onModifyApproval,
    providerLine,
    planLabel,
    awaitingApproval,
    elapsedSeconds,
    t,
  });
  const hasAnswer = orderedSteps.some(({ step }) => isStepKindAnswer(step.kind));
  if (!hasAnswer) {
    const fallbackAnswer = response.plan.reply || response.plan.summary;
    if (fallbackAnswer) {
      rows.push(renderPlanReplyRow(fallbackAnswer, planLabel, elapsedSeconds, t));
    }
  }
  return rows.length ? rows : buildLegacyAgentTimelineRows({
    response,
    shell,
    vision,
    skill,
    write,
    approval,
    approvalAction,
    onModifyApproval,
    showIntent,
    nextStep,
    planLabel,
    providerLine,
    awaitingApproval,
    elapsedSeconds,
    t,
  });
}

/** Render the server-owned ordered projection without exposing hidden reasoning. */
export function buildDurableTimelineRows(
  events: ChatTimelineEvent[] = [],
  elapsedSeconds?: number,
  terminalFailureCode = "",
): ReactNode[] {
  const presentation = buildTimelinePresentation(events, elapsedSeconds);
  if (!presentation.entries.length) return [];
  const rows: ReactNode[] = [];
  if (Number.isFinite(presentation.elapsedSeconds)) {
    rows.push(
      <div key="agent-turn-duration" data-agent-turn-duration className="px-1 text-xs text-muted-foreground">
        {i18n.t("agent.workSegmentElapsed", { duration: formatDuration(presentation.elapsedSeconds || 0) })}
      </div>,
    );
  }
  for (const entry of presentation.entries) {
    if (entry.type === "assistant") {
      const terminalStatusKey = runtimeTerminalStatusKey(terminalFailureCode);
      rows.push(terminalStatusKey ? (
        <div
          key={entry.id}
          data-vrcforge-terminal-status={terminalFailureCode}
          role="status"
          className="space-y-1.5 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-sm leading-relaxed"
        >
          <div className="flex items-center gap-1.5 text-xs font-medium text-destructive">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{i18n.t(terminalStatusKey)}</span>
          </div>
          <p className="text-muted-foreground">{entry.text}</p>
        </div>
      ) : (
        <div key={entry.id} className="space-y-1 px-1 text-sm leading-relaxed">
          <ChatMarkdown text={entry.text} />
        </div>
      ));
      continue;
    }
    const failed = entry.invocations.some((invocation) => ["failed", "error"].includes(invocation.status.toLowerCase()));
    rows.push(
      <WorkSegmentRow
        key={entry.id}
        kind={entry.kind}
        title={workBatchTitle(entry.kind, entry.invocations.length)}
        statusLabel={i18n.t("agent.workSegmentItems", { count: entry.invocations.length })}
        statusTone={failed ? "danger" : "muted"}
      >
        {entry.invocations.map((invocation) => {
          const danger = ["failed", "error"].includes(invocation.status.toLowerCase());
          return (
            <RunRow
              key={invocation.id}
              icon={workBatchIcon(entry.kind)}
              title={invocation.label}
              statusTone={danger ? "danger" : invocation.status === "started" ? "warn" : "ok"}
              statusLabel={invocation.status}
            >
              {invocation.summary ? <div className="text-xs text-muted-foreground">{invocation.summary}</div> : null}
            </RunRow>
          );
        })}
      </WorkSegmentRow>,
    );
  }
  return rows;
}

function workBatchTitle(kind: TimelineBatchKind, count: number): string {
  if (kind === "command") return i18n.t("agent.commandBatch");
  if (kind === "tool") return i18n.t("agent.toolBatch");
  if (kind === "file_edit") return i18n.t("agent.workSegmentFiles", { count });
  if (kind === "subagent") return i18n.t("agent.subagentBatch");
  return i18n.t("agent.workSegment");
}

function workBatchIcon(kind: TimelineBatchKind): "shell" | "skill" | "plan" {
  if (kind === "command") return "shell";
  if (kind === "tool" || kind === "file_edit" || kind === "subagent") return "skill";
  return "plan";
}

function buildAgentTimelineRowsFromSteps({
  steps,
  response,
  shell,
  vision,
  skill,
  write,
  approval,
  approvalAction,
  onModifyApproval,
  providerLine,
  planLabel,
  awaitingApproval,
  elapsedSeconds,
  t,
}: {
  steps: OrderedAgentStep[];
  response: AgentRuntimeResponse;
  shell?: AgentRuntimeResponse["shell"];
  vision?: AgentRuntimeResponse["vision"];
  skill?: AgentSkillResult;
  write?: AgentRuntimeResponse["write"];
  approval?: AgentApproval | null;
  approvalAction?: ApprovalActionState;
  onModifyApproval?: (approval: AgentApproval) => void;
  providerLine: string;
  planLabel: string;
  awaitingApproval: boolean;
  elapsedSeconds?: number;
  t: typeof i18n.t;
}): ReactNode[] {
  const rows: ReactNode[] = [];
  const countSteps = (kind: string) => steps.filter(({ step }) => normalizeAgentStepKind(step.kind || "", step.tool || "") === kind).length;
  const visionStepCount = countSteps("vision");
  const shellStepCount = countSteps("shell");
  const skillStepCount = countSteps("skill");
  const writeStepCount = countSteps("write");
  for (const { step, sourceIndex } of steps) {
    const rawKind = (step.kind || "").toLowerCase();
    const normalizedKind = normalizeAgentStepKind(rawKind, step.tool || "");
    const rowKey = `${normalizedKind}-${sourceIndex}`;
    if (normalizedKind === "assistant") {
      const answer = step.summary || response.plan.reply || response.plan.summary;
      if (answer) {
        rows.push(renderPlanReplyRow(answer, planLabel, elapsedSeconds, t, rowKey));
      }
      continue;
    }
    if (normalizedKind === "plan") {
      rows.push(
        <RunRow
          key={rowKey}
          icon="plan"
          title={response.plan.skillTool ? response.plan.skillTool : response.plan.shellCommand ? response.plan.shellCommand : displayStep(nextStepLabel(step) || nextStepLabel(step) || "plan")}
          statusTone="muted"
          statusLabel={response.plan.skillTool ? "tool planned" : response.plan.shellCommand ? "command planned" : "planned"}
        >
          <DataLine label="Planner" value={response.plan.plannerLabel || displayPlanner(response.plan.planner)} />
          {response.plan.skillTool ? <DataLine label="Tool" value={response.plan.skillTool} mono /> : null}
          {response.plan.skillCategory ? <DataLine label="Category" value={response.plan.skillCategory} /> : null}
          {response.plan.shellCommand ? <OutputBlock label="Command" value={response.plan.shellCommand} /> : null}
          {response.plan.expectedResult ? <DataLine label="Expected" value={response.plan.expectedResult} /> : null}
          {step.summary ? <DataLine label="Step" value={step.summary} /> : null}
        </RunRow>,
      );
      continue;
    }
    if (normalizedKind === "reasoning") {
      rows.push(
      );
      continue;
    }
    if (normalizedKind === "vision") {
      const detailedVision = visionStepCount === 1 ? vision : undefined;
      const stepVisionStatus = step.status || detailedVision?.status || "vision";
      const stepVisionProvider = step.providerLabel || step.provider || detailedVision?.providerLabel || detailedVision?.provider;
      const stepVisionModel = step.model || detailedVision?.model;
      rows.push(
        <RunRow
          key={rowKey}
          icon="vision"
          title={stepVisionStatus === "analyzed"
              ? t("vision.stepTitle", {
                  model: [stepVisionProvider, stepVisionModel].filter(Boolean).join(" · ") || step.tool || "vision",
                })
              : step.tool || t("vision.stepTitleSkipped")}
          statusTone={stepVisionStatus === "analyzed" ? "ok" : stepVisionStatus === "error" || stepVisionStatus === "failed" ? "danger" : "warn"}
          statusLabel={stepVisionStatus === "analyzed"
              ? t("vision.images", { count: step.imageCount ?? detailedVision?.imageCount ?? 0 })
              : stepVisionStatus === "error" || stepVisionStatus === "failed"
                ? t("skillStatus.failed")
                : stepVisionStatus}
        >
          {detailedVision?.imageNames && detailedVision.imageNames.length > 0 ? (
            <DataLine label={t("vision.images")} value={detailedVision.imageNames.join(", ")} />
          ) : null}
          {(step.source || detailedVision?.source) ? <DataLine label={t("vision.source")} value={(step.source || detailedVision?.source) === "main" ? t("vision.sourceMain") : t("vision.sourceProfile")} /> : null}
          {stepVisionStatus === "analyzed" && (step.usage?.totalTokens || detailedVision?.usage?.totalTokens) ? (
            <DataLine label={t("vision.tokens")} value={String(step.usage?.totalTokens || detailedVision?.usage?.totalTokens)} />
          ) : null}
          {detailedVision?.text ? <OutputBlock label={t("vision.analysis")} value={detailedVision.text} /> : null}
          {detailedVision?.error ? <DataLine label={t("skills.error")} value={detailedVision.error} /> : null}
          {detailedVision?.reason && stepVisionStatus !== "analyzed" ? <DataLine label={t("vision.reason")} value={detailedVision.reason} /> : null}
          {step.summary && <OutputBlock label={t("agent.stepSummary")} value={step.summary} />}
        </RunRow>,
      );
      continue;
    }
    if (normalizedKind === "shell") {
      const detailedShell = shellStepCount === 1 ? shell : undefined;
      const stepShellStatus = step.status || detailedShell?.status || "shell";
      const stepResult = step.result !== undefined ? step.result : detailedShell?.result;
      const stepShellResult = asAgentShellResult(stepResult);
      rows.push(
        <RunRow
          key={rowKey}
          icon="shell"
          title={step.tool || detailedShell?.classification?.command || "shell"}
          statusTone={stepShellResult ? (stepShellResult.ok ? "ok" : "danger") : stepStatusTone(stepShellStatus)}
          statusLabel={stepShellResult
              ? t("shell.exitCodeDuration", { code: stepShellResult.exitCode, time: formatDuration(stepShellResult.durationSeconds) })
              : awaitingApproval
                ? t("shell.awaitConfirmation")
                : stepShellStatus}
        >
          {detailedShell?.classification ? <DataLine label={t("approval.directory")} value={detailedShell.classification.cwd} /> : null}
          {detailedShell?.classification ? <div className="overflow-hidden rounded-md border border-border bg-muted/50 p-3 font-mono text-xs">
            <pre className="whitespace-pre-wrap break-words">{detailedShell.classification.command}</pre>
          </div> : null}
          {detailedShell?.classification?.reasons.length ? (
            <div className="flex flex-wrap gap-2">
              {detailedShell.classification.reasons.map((reason) => (
                <Badge key={reason} tone="muted" className="max-w-full">
                  <span className="truncate">{reason}</span>
                </Badge>
              ))}
            </div>
          ) : null}
          {stepShellResult ? (
            <>
              <DataLine label={t("shell.elapsed")} value={formatDuration(stepShellResult.durationSeconds)} />
              <OutputBlock label={t("shell.output")} value={stepShellResult.stdout} />
              {stepShellResult.stderr ? <OutputBlock label={t("shell.errorOutput")} value={stepShellResult.stderr} danger /> : null}
            </>
          ) : stepResult !== undefined ? <OutputBlock label={t("skills.data")} value={formatPayload(stepResult)} /> : null}
          {step.error || detailedShell?.error ? <DataLine label={t("skills.error")} value={step.error || detailedShell?.error || ""} /> : null}
          {step.summary ? <DataLine label={t("agent.stepSummary")} value={step.summary} /> : null}
        </RunRow>,
      );
      continue;
    }
    if (normalizedKind === "skill") {
      const detailedSkill = skillStepCount === 1 ? skill : undefined;
      const stepSkillTool = step.tool || skill?.tool || t("skills.skillCall");
      const stepSkillStatus = step.status || skill?.status || "skill";
      const stepResult = step.result !== undefined ? step.result : detailedSkill?.result;
      rows.push(
        <RunRow key={rowKey} icon="skill" title={stepSkillTool} statusTone={detailedSkill ? skillTone(detailedSkill) : stepStatusTone(stepSkillStatus)} statusLabel={displaySkillStatus(stepSkillStatus)}>
          <DataLine label={t("skills.tool")} value={stepSkillTool} mono />
          {detailedSkill?.category ? <DataLine label={t("skills.category")} value={detailedSkill.category} /> : null}
          {step.error || detailedSkill?.error ? <DataLine label={t("skills.error")} value={step.error || detailedSkill?.error || ""} /> : null}
          {stepResult !== undefined ? <OutputBlock label={t("skills.data")} value={formatPayload(stepResult)} /> : null}
          {step.summary ? <DataLine label={t("agent.stepSummary")} value={step.summary} /> : null}
        </RunRow>,
      );
      continue;
    }
    if (normalizedKind === "write") {
      const detailedWrite = writeStepCount === 1 ? write : undefined;
      const stepWriteTool = step.tool || write?.tool || t("skills.skillCall");
      const status = step.status || write?.status || (write?.ok ? "executed" : "pending");
      const stepResult = step.result !== undefined ? step.result : detailedWrite?.result;
      rows.push(
        <RunRow key={rowKey} icon="plan" title={stepWriteTool} statusTone={detailedWrite?.ok ? "ok" : stepStatusTone(status)} statusLabel={status}>
          <DataLine label="Tool" value={stepWriteTool} mono />
          {detailedWrite?.approvalId ? <DataLine label="Approval" value={detailedWrite.approvalId} mono /> : null}
          {detailedWrite?.paramsSummary ? <DataLine label={t("skills.data")} value={formatPayload(detailedWrite.paramsSummary)} /> : null}
          {stepResult !== undefined ? <OutputBlock label={t("skills.data")} value={formatPayload(stepResult)} /> : null}
          {step.error || detailedWrite?.error ? <DataLine label={t("skills.error")} value={step.error || detailedWrite?.error || ""} /> : null}
          {step.summary ? <DataLine label={t("agent.stepSummary")} value={step.summary} /> : null}
        </RunRow>,
      );
      continue;
    }
    if (normalizedKind === "approval") {
      if (approval && approvalAction !== "approve" && approvalAction !== "reject") {
        rows.push(
          <div key={rowKey}>
            <InlineApprovalCard approval={approval} action={approvalAction} onModify={onModifyApproval} />
          </div>,
        );
      } else if (awaitingApproval) {
        rows.push(
          <div key={rowKey} className="flex items-center gap-2 px-1 py-1 text-xs text-amber-700">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span>{t("approval.awaitingInline")}</span>
          </div>,
        );
      }
      continue;
    }
    if (normalizedKind === "result" && (step.result !== undefined || response.result !== undefined)) {
      const result = (step.result !== undefined ? step.result : response.result) as { ok?: boolean; status?: string; message?: string } | AgentShellResult;
      rows.push(
        <RunRow
          key={rowKey}
          icon="shell"
          title={typeof result === "string" ? "result" : "result"}
          statusTone={(result as AgentShellResult)?.ok === false || (typeof response.write?.ok === "boolean" && !response.write.ok) ? "danger" : "muted"}
          statusLabel={(typeof result === "object" && result && "status" in result && typeof (result as { status?: string }).status === "string")
            ? String((result as { status?: string }).status)
            : "result"}
        >
          {typeof result === "string" ? <OutputBlock label={t("agent.stepSummary")} value={result} /> : <OutputBlock label={t("agent.stepSummary")} value={formatPayload(result)} />}
          {step.summary ? <DataLine label={t("agent.stepSummary")} value={step.summary} /> : null}
        </RunRow>,
      );
      continue;
    }
    if (normalizedKind === "shellError" && shell?.error) {
      rows.push(
        <RunRow key={rowKey} icon="shell" title={t("shell.executionError")} statusTone="danger" statusLabel={t("skillStatus.failed")}>
          <DataLine label={t("skills.error")} value={shell.error} />
        </RunRow>,
      );
      continue;
    }
    if (step.summary) {
      rows.push(
        <RunRow key={rowKey} icon="plan" title={step.kind || "step"} statusTone="muted" statusLabel={step.status || "step"}>
          <DataLine label={t("agent.stepSummary")} value={step.summary} />
        </RunRow>,
      );
    }
  }
  if (shell?.error && !steps.some(({ step }) => isStepKindError(step.kind))) {
    rows.push(
      <RunRow key="shell-error-fallback" icon="shell" title={t("shell.executionError")} statusTone="danger" statusLabel={t("skillStatus.failed")}>
        <DataLine label={t("skills.error")} value={shell.error} />
      </RunRow>,
    );
  }
  return rows;
}

function buildLegacyAgentTimelineRows({
  response,
  shell,
  vision,
  skill,
  write,
  approval,
  approvalAction,
  onModifyApproval,
  showIntent,
  nextStep,
  planLabel,
  providerLine,
  awaitingApproval,
  elapsedSeconds,
  t,
}: {
  response: AgentRuntimeResponse;
  shell?: AgentRuntimeResponse["shell"];
  vision?: AgentRuntimeResponse["vision"];
  skill?: AgentSkillResult;
  write?: AgentRuntimeResponse["write"];
  approval?: AgentApproval | null;
  approvalAction?: ApprovalActionState;
  onModifyApproval?: (approval: AgentApproval) => void;
  showIntent: boolean;
  nextStep: string;
  planLabel: string;
  providerLine: string;
  awaitingApproval: boolean;
  elapsedSeconds?: number;
  t: typeof i18n.t;
}): ReactNode[] {
  const rows: ReactNode[] = [];
  const planReply = response.plan.reply || response.plan.summary;
  rows.push(
    <div key="reply" className="px-1 text-sm">
      <ChatMarkdown text={planReply} />
      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
        <span>{planLabel}</span>
        {elapsedSeconds ? <span>{t("agent.elapsed", { time: formatDuration(elapsedSeconds) })}</span> : null}
      </div>
    </div>,
  );
  if (showIntent && nextStep) {
    rows.push(
      <RunRow
        key="intent"
        icon="plan"
        title={displayStep(nextStep)}
        statusTone="muted"
        statusLabel={response.plan.skillTool ? "tool planned" : response.plan.shellCommand ? "command planned" : "planned"}
      >
        <DataLine label="Planner" value={response.plan.plannerLabel || displayPlanner(response.plan.planner)} />
        {response.plan.skillTool ? <DataLine label="Tool" value={response.plan.skillTool} mono /> : null}
        {response.plan.skillCategory ? <DataLine label="Category" value={response.plan.skillCategory} /> : null}
        {response.plan.shellCommand ? <OutputBlock label="Command" value={response.plan.shellCommand} /> : null}
        {response.plan.expectedResult ? <DataLine label="Expected" value={response.plan.expectedResult} /> : null}
      </RunRow>,
    );
  }
  if (vision) {
    rows.push(
      <RunRow
        key="vision"
        icon="vision"
        title={
          vision.status === "analyzed"
            ? t("vision.stepTitle", {
                model: [vision.providerLabel || vision.provider, vision.model].filter(Boolean).join(" · ") || "vision",
              })
            : t("vision.stepTitleSkipped")
        }
        statusTone={vision.status === "analyzed" ? "ok" : vision.status === "error" ? "danger" : "warn"}
        statusLabel={
          vision.status === "analyzed"
            ? t("vision.images", { count: vision.imageCount ?? 0 })
            : vision.status === "error"
              ? t("skillStatus.failed")
              : t("vision.stepUnconfigured")
        }
      >
        {vision.imageNames && vision.imageNames.length > 0 ? (
          <DataLine label={t("vision.images")} value={vision.imageNames.join(", ")} />
        ) : null}
        {vision.source ? (
          <DataLine label={t("vision.source")} value={vision.source === "main" ? t("vision.sourceMain") : t("vision.sourceProfile")} />
        ) : null}
        {vision.status === "analyzed" && vision.usage?.totalTokens ? <DataLine label={t("vision.tokens")} value={String(vision.usage.totalTokens)} /> : null}
        {vision.text ? <OutputBlock label={t("vision.analysis")} value={vision.text} /> : null}
        {vision.error ? <DataLine label={t("skills.error")} value={vision.error} /> : null}
        {vision.reason && vision.status !== "analyzed" ? <DataLine label={t("vision.reason")} value={vision.reason} /> : null}
      </RunRow>,
    );
  }
  if (shell?.classification) {
    rows.push(
      <RunRow
        key="shell"
        icon="shell"
        title={shell.classification.command}
        statusTone={shell.result ? (shell.result.ok ? "ok" : "danger") : awaitingApproval ? "warn" : riskTone(shell.classification.risk)}
        statusLabel={
          shell.result
            ? t("shell.exitCodeDuration", { code: shell.result.exitCode, time: formatDuration(shell.result.durationSeconds) })
            : awaitingApproval
              ? t("shell.awaitConfirmation")
              : t("shell.riskLevel", { level: shell.classification.risk })
        }
      >
        <DataLine label={t("approval.directory")} value={shell.classification.cwd} />
        <div className="overflow-hidden rounded-md border border-border bg-muted/50 p-3 font-mono text-xs">
          <pre className="whitespace-pre-wrap break-words">{shell.classification.command}</pre>
        </div>
        {shell.classification.reasons.length ? (
          <div className="flex flex-wrap gap-2">
            {shell.classification.reasons.map((reason) => (
              <Badge key={reason} tone="muted" className="max-w-full">
                <span className="truncate">{reason}</span>
              </Badge>
            ))}
          </div>
        ) : null}
        {shell.result ? (
          <>
            <DataLine label={t("shell.elapsed")} value={formatDuration(shell.result.durationSeconds)} />
            <OutputBlock label={t("shell.output")} value={shell.result.stdout} />
            {shell.result.stderr ? <OutputBlock label={t("shell.errorOutput")} value={shell.result.stderr} danger /> : null}
          </>
        ) : null}
      </RunRow>,
    );
  }
  if (skill) {
    rows.push(
      <RunRow
        key="skill"
        icon="skill"
        title={skill.tool || t("skills.skillCall")}
        statusTone={skillTone(skill)}
        statusLabel={displaySkillStatus(skill.status)}
      >
        <DataLine label={t("skills.tool")} value={skill.tool || "-"} mono />
        {skill.category ? <DataLine label={t("skills.category")} value={skill.category} /> : null}
        {skill.error ? <DataLine label={t("skills.error")} value={skill.error} /> : null}
        {skill.result !== undefined ? <OutputBlock label={t("skills.data")} value={formatPayload(skill.result)} /> : null}
      </RunRow>,
    );
  }
  if (approval && approvalAction !== "approve" && approvalAction !== "reject") {
    rows.push(
      <div key="approval-inline">
        <InlineApprovalCard approval={approval} action={approvalAction} onModify={onModifyApproval} />
      </div>,
    );
  } else if (awaitingApproval) {
    rows.push(
      <div key="approval-await" className="flex items-center gap-2 px-1 py-1 text-xs text-amber-700">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
        <span>{t("approval.awaitingInline")}</span>
      </div>,
    );
  }
  if (shell?.error) {
    rows.push(
      <RunRow key="shell-error" icon="shell" title={t("shell.executionError")} statusTone="danger" statusLabel={t("skillStatus.failed")}>
        <DataLine label={t("skills.error")} value={shell.error} />
      </RunRow>,
    );
  }
  if (write) {
    rows.push(
      <RunRow
        key="write"
        icon="plan"
        title={write.tool || t("skills.skillCall")}
        statusTone={write.ok ? "ok" : "warn"}
        statusLabel={write.status || "write"}
      >
        <DataLine label="Tool" value={write.tool || "-"} mono />
        {write.approvalId ? <DataLine label="Approval" value={write.approvalId} mono /> : null}
        {write.paramsSummary ? <DataLine label={t("skills.data")} value={formatPayload(write.paramsSummary)} /> : null}
        {write.result ? <OutputBlock label={t("skills.data")} value={formatPayload(write.result)} /> : null}
        {write.error ? <DataLine label={t("skills.error")} value={write.error} /> : null}
      </RunRow>,
    );
  }
  return rows;
}

function normalizeAgentSteps(steps: AgentRuntimeResponse["steps"]): OrderedAgentStep[] {
  return (steps || []).map((step, sourceIndex) => ({ step, sourceIndex })).sort((left, right) => {
    const leftIndex = typeof left.step.index === "number" ? left.step.index : Number.MAX_SAFE_INTEGER;
    const rightIndex = typeof right.step.index === "number" ? right.step.index : Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) {
      return leftIndex - rightIndex;
    }
    return left.sourceIndex - right.sourceIndex;
  });
}

function normalizeAgentStepKind(kind: string, tool = ""): string {
  const normalizedKind = kind.toLowerCase();
  const normalizedTool = tool.toLowerCase();
  if (normalizedKind.includes("assistant") || normalizedKind.includes("final") || normalizedKind.includes("reply")) {
    return "assistant";
  }
  if (normalizedKind.includes("reason") || normalizedKind.includes("think")) {
    return "reasoning";
  }
  if (normalizedKind.includes("vision") || normalizedTool.includes("vision")) {
    return "vision";
  }
  if (normalizedKind.includes("approval")) {
    return "approval";
  }
  if (normalizedKind.includes("write")) {
    return "write";
  }
  if (normalizedKind.includes("result")) {
    return "result";
  }
  if (normalizedKind.includes("error")) {
    return "shellError";
  }
  if (normalizedKind.includes("shell") || normalizedTool.includes("shell")) {
    return "shell";
  }
  if (normalizedKind.includes("skill") || normalizedKind.includes("tool")) {
    return "skill";
  }
  if (normalizedKind.includes("plan") || normalizedKind.includes("next")) {
    return "plan";
  }
  return normalizedKind || "unknown";
}

function isStepKindAnswer(kind?: string): boolean {
  const normalized = (kind || "").toLowerCase();
  return normalized.includes("assistant") || normalized.includes("final") || normalized.includes("reply");
}

function isStepKindError(kind?: string): boolean {
  const normalized = (kind || "").toLowerCase();
  return normalized.includes("error");
}

function renderPlanReplyRow(
  answer: string,
  planLabel: string,
  elapsedSeconds: number | undefined,
  t: typeof i18n.t,
  key = "plan-reply",
) {
  return (
    <div key={key} className="px-1 text-sm">
      <ChatMarkdown text={answer} />
      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
        <span>{planLabel}</span>
        {elapsedSeconds ? <span>{t("agent.elapsed", { time: formatDuration(elapsedSeconds) })}</span> : null}
      </div>
    </div>
  );
}

function nextStepLabel(step: OrderedAgentStep["step"]): string {
  if (typeof step.kind === "string" && step.kind.trim()) {
    return step.kind;
  }
  return "";
}
export function RunRow({
  icon,
  title,
  statusTone,
  statusLabel,
  children,
  timelineOrder,
}: {
  icon: "shell" | "skill" | "plan" | "vision";
  title: string;
  statusTone: "ok" | "warn" | "danger" | "muted";
  statusLabel: string;
  children: ReactNode;
  timelineOrder?: number;
}) {
  const [open, setOpen] = useState(false);
  const Icon = icon === "shell" ? TerminalSquare : icon === "skill" ? Wrench : icon === "vision" ? Eye : ListChecks;
  return (
    <div className="group/run text-muted-foreground" style={timelineOrder !== undefined ? { order: timelineOrder } : undefined}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex min-w-0 items-center gap-2 rounded-md px-1 py-1 text-left transition-colors hover:bg-muted/50"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <Icon className="h-3.5 w-3.5 shrink-0" />
        <span className={cn("min-w-0 truncate text-xs", icon === "shell" ? "font-mono" : "")}>{title}</span>
        <span className={cn("shrink-0 text-xs", statusTone === "danger" ? "text-destructive" : statusTone === "warn" ? "text-amber-600" : statusTone === "ok" ? "text-emerald-600" : "text-muted-foreground")}>
          {statusLabel}
        </span>
      </button>
      {open ? <div className="ml-6 mt-1 space-y-2 rounded-lg bg-muted/40 px-3 py-2 text-xs">{children}</div> : null}
    </div>
  );
}

function WorkSegmentRow({
  kind,
  title,
  statusLabel,
  statusTone,
  children,
}: {
  kind: TimelineBatchKind;
  title: string;
  statusLabel: string;
  statusTone: "danger" | "muted";
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="group/work-segment text-muted-foreground">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex min-w-0 items-center gap-2 rounded-md px-1 py-1 text-left transition-colors hover:bg-muted/50"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0" />}
        {kind === "command" ? <TerminalSquare className="h-3.5 w-3.5 shrink-0" /> : kind === "tool" || kind === "file_edit" ? <Wrench className="h-3.5 w-3.5 shrink-0" /> : <ListChecks className="h-3.5 w-3.5 shrink-0" />}
        <span className="min-w-0 truncate text-xs">{title}</span>
        <span className={cn("shrink-0 text-xs", statusTone === "danger" ? "text-destructive" : "text-muted-foreground")}>{statusLabel}</span>
      </button>
      {open ? <div className="ml-6 mt-1 space-y-1 rounded-lg bg-muted/20 px-2 py-1">{children}</div> : null}
    </div>
  );
}

export function InlineApprovalCard({
  approval,
  action,
  onModify,
}: {
  approval: AgentApproval;
  action?: ApprovalActionState;
  onModify?: (approval: AgentApproval) => void;
}) {
  const { t } = useTranslation();
  const presentation = presentApproval(approval, t);
  return (
    <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600" />
      <div className="min-w-0 flex-1 text-muted-foreground">
        <span className="font-medium text-foreground">{presentation.title}</span>
        {` · ${t("approval.awaitingInline")}`}
      </div>
      {!approval.goalDeliveryId?.trim() ? (
        <Button type="button" variant="outline" className="h-7 shrink-0 px-2 text-xs" disabled={Boolean(action)} onClick={() => onModify?.(approval)}>
          <Pencil className="h-3.5 w-3.5" />
          {action === "modify" ? t("approval.modifying") : t("approval.modify")}
        </Button>
      ) : null}
    </div>
  );
}

export function displayPlanner(planner: string): string {
  if (planner === "llm") return i18n.t("planner.ai");
  return i18n.t("planner.fallback");
}

function displayStep(step: string): string {
  const labels: Record<string, string> = {
    classify_shell: i18n.t("step.classifyShell"),
    execute_shell: i18n.t("step.executeShell"),
    call_skill: i18n.t("step.callSkill"),
    request_approval: i18n.t("shell.awaitConfirmation"),
    await_user_instruction: i18n.t("step.awaitUserInstruction"),
    done: i18n.t("step.done"),
  };
  return labels[step] || step;
}

function riskTone(risk: string): "ok" | "warn" | "danger" | "muted" {
  if (risk === "low") return "ok";
  if (risk === "high") return "warn";
  if (risk === "reject") return "danger";
  return "muted";
}

function stepStatusTone(status: string): "ok" | "warn" | "danger" | "muted" {
  const normalized = status.toLowerCase();
  if (["completed", "executed", "loaded", "ok", "passed"].includes(normalized)) return "ok";
  if (["failed", "error", "rejected", "cancelled", "interrupted"].includes(normalized)) return "danger";
  if (["pending", "planned", "queued", "running", "blocked", "needs_user_action"].includes(normalized)) return "warn";
  return "muted";
}

function skillTone(skill: AgentSkillResult): "ok" | "warn" | "danger" | "muted" {
  if (skill.status === "executed" && skill.ok) return "ok";
  if (skill.status === "loaded" && skill.ok) return "ok";
  if (skill.status === "blocked") return "warn";
  if (skill.status === "failed" || !skill.ok) return "danger";
  return "muted";
}

function displaySkillStatus(status: string): string {
  const labels: Record<string, string> = {
    executed: i18n.t("agent.executed"),
    loaded: i18n.t("skillStatus.loaded"),
    failed: i18n.t("skillStatus.failed"),
    blocked: i18n.t("skillStatus.blocked"),
  };
  return labels[status] || status || "-";
}

function asAgentShellResult(value: unknown): AgentShellResult | undefined {
  if (!value || typeof value !== "object") {
    return undefined;
  }
  const candidate = value as Partial<AgentShellResult>;
  return typeof candidate.ok === "boolean"
    && typeof candidate.command === "string"
    && typeof candidate.exitCode === "number"
    && typeof candidate.durationSeconds === "number"
    && typeof candidate.stdout === "string"
    && typeof candidate.stderr === "string"
    ? candidate as AgentShellResult
    : undefined;
}

export function formatPayload(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return String(seconds) + "s";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return String(minutes) + "m " + String(rest) + "s";
}
