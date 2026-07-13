import {
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Eye,
  ListChecks,
  Loader2,
  MessageSquare,
  Pencil,
  RotateCcw,
  Settings,
  Shield,
  Sparkles,
  TerminalSquare,
  ThumbsDown,
  ThumbsUp,
  Wrench,
  X,
} from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import type { AgentApproval, AgentReasoningTrace, AgentRuntimeResponse, AgentSkillResult } from "../../lib/api";
import type { ApprovalActionState, ChatAttachment, ConversationItem, MessageFeedback } from "../../lib/chat-types";
import { thinkingTraceLabel } from "../../lib/provider-ui";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";
import { ChatMarkdown } from "./chat-markdown";
import { AttachmentStrip } from "./composer";

export function ConversationCard({
  item,
  approval,
  approvalAction,
  feedback,
  canRetry,
  canEdit,
  onCopyItem,
  onRetryItem,
  onEditItem,
  onFeedbackItem,
  onApprove,
  onReject,
  onModifyApproval,
  onOpenSettings,
  onOpenDoctor,
}: {
  item: ConversationItem;
  approval?: AgentApproval | null;
  approvalAction?: ApprovalActionState;
  feedback?: MessageFeedback;
  canRetry?: boolean;
  canEdit?: boolean;
  onCopyItem?: (item: ConversationItem) => void;
  onRetryItem?: (itemId: string) => void;
  onEditItem?: (itemId: string) => void;
  onFeedbackItem?: (itemId: string, value: MessageFeedback) => void;
  onApprove?: (approvalId: string) => void;
  onReject?: (approvalId: string) => void;
  onModifyApproval?: (approval: AgentApproval) => void;
  onOpenSettings?: () => void;
  onOpenDoctor?: () => void;
}) {
  const { t } = useTranslation();
  if (item.type === "user") {
    const attachments = item.attachments || [];
    const imageAttachments = attachments.filter((attachment) => attachment.dataUrl && attachment.type.startsWith("image/"));
    const otherAttachments = attachments.filter((attachment) => !attachment.dataUrl || !attachment.type.startsWith("image/"));
    return (
      <div className="group flex justify-end">
        <div className="relative flex max-w-[78%] flex-col items-end gap-2">
          {item.queuedFrom ? (
            <div className="flex items-center gap-1 rounded-full bg-muted/70 px-2 py-1 text-[11px] text-muted-foreground">
              <MessageSquare className="h-3 w-3" />
              {t("chat.queuedSent")}
            </div>
          ) : null}
          {imageAttachments.length ? <UserImageAttachments attachments={imageAttachments} /> : null}
          {item.text ? (
            <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm text-foreground">
              <ChatMarkdown text={item.text} />
            </div>
          ) : null}
          {otherAttachments.length ? (
            <div className="max-w-full rounded-xl bg-muted/70 px-3 py-2 text-sm">
              <AttachmentStrip attachments={otherAttachments} compact />
            </div>
          ) : null}
          <MessageActions
            align="right"
            createdAt={item.createdAt || item.id}
            onCopy={() => onCopyItem?.(item)}
            onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
            onEdit={canEdit ? () => onEditItem?.(item.id) : undefined}
          />
        </div>
      </div>
    );
  }

  if (item.type === "error") {
    return (
      <div className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive/80">
        <div className="break-words">{item.text}</div>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={() => onOpenDoctor?.()}>
            <Wrench className="h-3.5 w-3.5" />
            {t("sidebar.doctor")}
          </Button>
        </div>
        <MessageActions
          onCopy={() => onCopyItem?.(item)}
          onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
        />
      </div>
    );
  }

  if (item.type === "streaming") {
    return (
      <div className="group flex justify-start" data-conversation-streaming-turn={item.clientTurnId}>
        <div className="relative w-full max-w-[85%] space-y-1.5 px-1 text-sm">
          {item.text ? (
            <ChatMarkdown text={item.text} />
          ) : (
            <div className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>{t("chat.executingHint")}</span>
            </div>
          )}
          <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>{item.providerLabel || displayPlanner("llm")}{item.model ? ` / ${item.model}` : ""}</span>
          </div>
        </div>
      </div>
    );
  }

  if (item.type === "result") {
    return (
      <div className="group flex justify-start">
        <div className="relative w-full max-w-[85%] space-y-1">
          <RunRow
            icon="shell"
            title={item.result?.command || (item.error === "rejected" ? t("agent.rejected") : t("agent.executionResult"))}
            statusTone={item.result ? (item.result.ok ? "ok" : "danger") : "muted"}
            statusLabel={item.result ? t("shell.exitCode", { code: item.result.exitCode }) : item.error || "result"}
          >
            {item.error ? <DataLine label={t("skills.error")} value={item.error} /> : null}
            {item.result ? (
              <>
                <DataLine label={t("shell.elapsed")} value={`${item.result.durationSeconds}s`} />
                <OutputBlock label={t("shell.output")} value={item.result.stdout} />
                {item.result.stderr ? <OutputBlock label={t("shell.errorOutput")} value={item.result.stderr} danger /> : null}
              </>
            ) : null}
          </RunRow>
          <MessageActions
            createdAt={item.createdAt || item.id}
            onCopy={() => onCopyItem?.(item)}
            onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          />
        </div>
      </div>
    );
  }

  if (item.type === "compact") {
    const running = item.status === "running";
    return (
      <div className="group flex max-w-[85%] items-center gap-3 py-1 text-xs text-muted-foreground">
        <div className="h-px flex-1 bg-border/70" />
        <div className="flex shrink-0 items-center gap-1.5">
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          <span>{item.text}</span>
        </div>
        <div className="h-px flex-1 bg-border/70" />
      </div>
    );
  }

  if (item.type === "subagent") {
    const task = item.task;
    return (
      <div className="group flex justify-start">
        <div className="relative w-full max-w-[85%] space-y-2 rounded-2xl border border-border bg-card px-4 py-3 text-sm shadow-panel">
          <div className="flex min-w-0 items-center gap-2">
            <Bot className="h-4 w-4 shrink-0 text-primary" />
            <span className="min-w-0 flex-1 truncate font-medium">
              {task.displayName || t("agent.subagentTask")} · {subAgentRoleLabel(task.role)}
            </span>
            <Badge tone={subAgentStatusTone(task.status)} className="shrink-0">
              {displaySubAgentStatus(task.status)}
            </Badge>
          </div>
          <p className="whitespace-pre-wrap break-words leading-relaxed text-muted-foreground">
            {task.summary || task.error || task.task || t("agent.noSummaryReturned")}
          </p>
          {task.mergeDecision ? (
            <div className="text-xs text-muted-foreground">
              {t("subagent.review")}: {task.mergeDecision === "adopted" ? t("subagent.mergedBadge") : t("subagent.dismissedBadge")}
              {task.mergedAt ? ` · ${task.mergedAt}` : ""}
            </div>
          ) : null}
          {task.result !== undefined ? <OutputBlock label={t("subagent.result")} value={formatPayload(task.result)} /> : null}
          <MessageActions
            onCopy={() => onCopyItem?.(item)}
            onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          />
        </div>
      </div>
    );
  }

  const response = item.response;
  const shell = response.shell;
  const skill = response.skill;
  const vision = response.vision;
  const timelineOrder = buildAgentTimelineOrder(response);
  const awaitingApproval = shell?.status === "pending_approval";
  const localIdle =
    response.plan.planner === "deterministic-local" &&
    response.plan.nextStep === "await_user_instruction" &&
    !response.plan.skillTool &&
    !response.plan.shellCommand;
  const nextStep = response.plan.nextStep || "";
  const showIntent = Boolean(nextStep) && nextStep !== "await_user_instruction" && nextStep !== "done";

  if (localIdle) {
    return (
      <div className="group flex justify-start">
        <div className="relative max-w-[85%] space-y-2 px-1 text-sm">
          <p className="text-muted-foreground">
            {t("agent.keywordPlannerHint1")}
          </p>
          <p className="text-muted-foreground">
            {t("agent.keywordPlannerHint2")}
          </p>
          <Button type="button" variant="outline" className="h-8 px-3 text-xs" onClick={() => onOpenSettings?.()}>
            <Settings className="mr-1 h-3.5 w-3.5" />
            {t("agent.openSettings")}
          </Button>
          <MessageActions
            onCopy={() => onCopyItem?.(item)}
            onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="group flex justify-start">
      <div className="relative flex w-full max-w-[85%] flex-col gap-1.5">
        <div className="order-last px-1 text-sm">
          <ChatMarkdown text={response.plan.reply || response.plan.summary} />
          {false && showIntent ? (
            <p className="hidden">
              <Sparkles className="h-3.5 w-3.5 shrink-0" />
              <span>
                {t("agent.willDoNext", { step: displayStep(nextStep) })}
                {response.plan.skillTool ? `：${response.plan.skillTool}` : ""}
              </span>
            </p>
          ) : null}
          <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
            <span>{item.providerLabel || response.plan.plannerLabel || displayPlanner(response.plan.planner)}{item.model ? ` · ${item.model}` : ""}</span>
            {item.elapsedSeconds ? <span>{t("agent.elapsed", { time: formatDuration(item.elapsedSeconds) })}</span> : null}
          </div>
        </div>

        {showIntent ? (
          <RunRow
            icon="plan"
            title={displayStep(nextStep)}
            statusTone="muted"
            statusLabel={response.plan.skillTool ? "tool planned" : response.plan.shellCommand ? "command planned" : "planned"}
            timelineOrder={timelineOrder.plan}
          >
            <DataLine label="Planner" value={response.plan.plannerLabel || displayPlanner(response.plan.planner)} />
            {response.plan.skillTool ? <DataLine label="Tool" value={response.plan.skillTool} mono /> : null}
            {response.plan.skillCategory ? <DataLine label="Category" value={response.plan.skillCategory} /> : null}
            {response.plan.shellCommand ? <OutputBlock label="Command" value={response.plan.shellCommand} /> : null}
            {response.plan.expectedResult ? <DataLine label="Expected" value={response.plan.expectedResult} /> : null}
          </RunRow>
        ) : null}

        <ReasoningTracePanel
          trace={response.reasoning}
          fallbackLabel={item.providerLabel || response.plan.plannerLabel || displayPlanner(response.plan.planner)}
          elapsedSeconds={item.elapsedSeconds}
          timelineOrder={timelineOrder.reasoning}
        />

        {vision ? (
          <RunRow
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
                ? t("vision.stepAnalyzed", { count: vision.imageCount ?? 0 })
                : vision.status === "error"
                  ? t("skillStatus.failed")
                  : t("vision.stepUnconfigured")
            }
            timelineOrder={timelineOrder.vision}
          >
            {vision.imageNames && vision.imageNames.length > 0 ? (
              <DataLine label={t("vision.images")} value={vision.imageNames.join(", ")} />
            ) : null}
            {vision.source ? (
              <DataLine label={t("vision.source")} value={vision.source === "main" ? t("vision.sourceMain") : t("vision.sourceProfile")} />
            ) : null}
            {vision.status === "analyzed" && vision.usage?.totalTokens ? (
              <DataLine label={t("vision.tokens")} value={String(vision.usage.totalTokens)} />
            ) : null}
            {vision.text ? <OutputBlock label={t("vision.analysis")} value={vision.text} /> : null}
            {vision.error ? <DataLine label={t("skills.error")} value={vision.error} /> : null}
            {vision.reason && vision.status !== "analyzed" ? <DataLine label={t("vision.reason")} value={vision.reason} /> : null}
          </RunRow>
        ) : null}

        {shell?.classification ? (
          <RunRow
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
            timelineOrder={timelineOrder.shell}
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
          </RunRow>
        ) : null}

        {skill ? (
          <RunRow
            icon="skill"
            title={skill.tool || t("skills.skillCall")}
            statusTone={skillTone(skill)}
            statusLabel={displaySkillStatus(skill.status)}
            timelineOrder={timelineOrder.skill}
          >
            <DataLine label={t("skills.tool")} value={skill.tool || "-"} mono />
            {skill.category ? <DataLine label={t("skills.category")} value={skill.category} /> : null}
            {skill.error ? <DataLine label={t("skills.error")} value={skill.error} /> : null}
            {skill.result !== undefined ? <OutputBlock label={t("skills.data")} value={formatPayload(skill.result)} /> : null}
          </RunRow>
        ) : null}

        {approval ? (
          <div style={{ order: timelineOrder.approval }}>
            <InlineApprovalCard
              approval={approval}
              action={approvalAction}
              onApprove={onApprove}
              onReject={onReject}
              onModify={onModifyApproval}
            />
          </div>
        ) : awaitingApproval ? (
          <div className="flex items-center gap-2 px-1 py-1 text-xs text-amber-700" style={{ order: timelineOrder.approval }}>
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span>{t("approval.awaitingInline")}</span>
          </div>
        ) : null}
        {shell?.error ? (
          <RunRow icon="shell" title={t("shell.executionError")} statusTone="danger" statusLabel={t("skillStatus.failed")} timelineOrder={timelineOrder.shellError}>
            <DataLine label={t("skills.error")} value={shell.error} />
          </RunRow>
        ) : null}
        <MessageActions
          createdAt={item.createdAt || item.id}
          onCopy={() => onCopyItem?.(item)}
          onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          onFeedbackUp={() => onFeedbackItem?.(item.id, "up")}
          onFeedbackDown={() => onFeedbackItem?.(item.id, "down")}
          feedback={feedback}
        />
      </div>
    </div>
  );
}

export function UserImageAttachments({ attachments }: { attachments: ChatAttachment[] }) {
  const [preview, setPreview] = useState<ChatAttachment | null>(null);
  const { t } = useTranslation();
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const closeLabel = t("chat.closeImagePreview");
  useEffect(() => {
    if (!preview) {
      return;
    }
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPreview(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [preview]);
  if (!attachments.length) {
    return null;
  }
  return (
    <>
      <div className="flex flex-wrap justify-end gap-2">
        {attachments.map((attachment) => (
          <button
            key={attachment.id}
            type="button"
            className="group/image block overflow-hidden rounded-lg border border-border bg-muted/70 transition hover:border-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
            onClick={() => setPreview(attachment)}
            aria-label={t("chat.imagePreview")}
            title={attachment.name}
          >
            <img src={attachment.dataUrl} alt={attachment.name} className="h-20 w-28 object-cover transition group-hover/image:scale-[1.02]" />
          </button>
        ))}
      </div>
      {preview?.dataUrl ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
          role="dialog"
          aria-modal="true"
          aria-label={t("chat.imagePreview")}
          onClick={() => setPreview(null)}
        >
          <div className="relative flex max-h-full max-w-full items-center justify-center" onClick={(event) => event.stopPropagation()}>
            <button
              ref={closeButtonRef}
              type="button"
              className="fixed right-5 top-5 z-10 rounded-full bg-black/70 p-2 text-white transition hover:bg-black focus:outline-none focus:ring-2 focus:ring-white/80"
              onClick={() => setPreview(null)}
              aria-label={closeLabel}
              title={closeLabel}
            >
              <X className="h-4 w-4" />
            </button>
            <img src={preview.dataUrl} alt={preview.name} className="max-h-[82vh] max-w-[86vw] rounded-xl object-contain shadow-panel" />
          </div>
        </div>
      ) : null}
    </>
  );
}

function MessageActions({
  align = "left",
  createdAt,
  feedback,
  onCopy,
  onRetry,
  onEdit,
  onFeedbackUp,
  onFeedbackDown,
}: {
  align?: "left" | "right";
  createdAt?: string;
  feedback?: MessageFeedback;
  onCopy?: () => void;
  onRetry?: () => void;
  onEdit?: () => void;
  onFeedbackUp?: () => void;
  onFeedbackDown?: () => void;
}) {
  const { t } = useTranslation();
  const hasActions = onCopy || onRetry || onEdit || onFeedbackUp || onFeedbackDown;
  const timeLabel = formatMessageTime(createdAt, i18n.language);
  if (!hasActions) {
    return null;
  }
  return (
    <div
      className={cn(
        "order-last mt-1 flex items-center gap-1 px-1 text-muted-foreground",
        align === "right" ? "justify-end" : "justify-start",
      )}
    >
      {timeLabel ? <span className="px-1 text-xs text-muted-foreground/80">{timeLabel}</span> : null}
      {onCopy ? (
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onCopy}
          title={t("chat.copyMessage")}
          aria-label={t("chat.copyMessage")}
        >
          <Copy className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onRetry ? (
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onRetry}
          title={t("chat.retryMessage")}
          aria-label={t("chat.retryMessage")}
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onEdit ? (
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onEdit}
          title={t("chat.editMessage")}
          aria-label={t("chat.editMessage")}
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onFeedbackUp ? (
        <button
          type="button"
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
            feedback === "up" && "bg-muted text-foreground",
          )}
          onClick={onFeedbackUp}
          title={t("messageActions.goodResponse")}
          aria-label={t("messageActions.goodResponse")}
        >
          <ThumbsUp className="h-3.5 w-3.5" />
        </button>
      ) : null}
      {onFeedbackDown ? (
        <button
          type="button"
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground",
            feedback === "down" && "bg-muted text-foreground",
          )}
          onClick={onFeedbackDown}
          title={t("messageActions.badResponse")}
          aria-label={t("messageActions.badResponse")}
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </div>
  );
}

function formatMessageTime(value: string | undefined, language: string): string {
  const ms = parseMessageTime(value);
  if (!ms) {
    return "";
  }
  const now = new Date();
  const date = new Date(ms);
  const sameDay = now.toDateString() === date.toDateString();
  if (sameDay) {
    return new Intl.DateTimeFormat(language || undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
  }
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (yesterday.toDateString() === date.toDateString()) {
    const normalizedLanguage = language.toLowerCase();
    if (normalizedLanguage.startsWith("zh")) {
      return "昨天";
    }
    if (normalizedLanguage.startsWith("ja")) {
      return "昨日";
    }
    return "yesterday";
  }
  return new Intl.DateTimeFormat(language || undefined, { month: "short", day: "numeric" }).format(date);
}

function parseMessageTime(value: string | undefined): number {
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(value);
  if (Number.isFinite(parsed)) {
    return parsed;
  }
  const match = value.match(/(?:^|[^0-9])([0-9]{13})(?:[^0-9]|$)/);
  if (!match) {
    return 0;
  }
  const timestamp = Number(match[1]);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

type AgentTimelineKey = "plan" | "reasoning" | "vision" | "shell" | "skill" | "approval" | "shellError";

type AgentTimelineOrder = Record<AgentTimelineKey, number>;

const DEFAULT_AGENT_TIMELINE_ORDER: AgentTimelineOrder = {
  plan: 10,
  reasoning: 20,
  vision: 30,
  shell: 40,
  skill: 50,
  approval: 60,
  shellError: 70,
};

function buildAgentTimelineOrder(response: AgentRuntimeResponse): AgentTimelineOrder {
  const order: AgentTimelineOrder = { ...DEFAULT_AGENT_TIMELINE_ORDER };
  const assigned = new Set<AgentTimelineKey>();
  const steps = [...(response.steps || [])].sort((left, right) => {
    const leftIndex = typeof left.index === "number" ? left.index : Number.MAX_SAFE_INTEGER;
    const rightIndex = typeof right.index === "number" ? right.index : Number.MAX_SAFE_INTEGER;
    if (leftIndex !== rightIndex) {
      return leftIndex - rightIndex;
    }
    return 0;
  });
  let nextOrder = 10;
  for (const step of steps) {
    const key = agentTimelineKeyForStep(step.kind, step.tool);
    if (!key || assigned.has(key)) {
      continue;
    }
    order[key] = nextOrder;
    assigned.add(key);
    nextOrder += 10;
  }
  return order;
}

function agentTimelineKeyForStep(kind?: string, tool?: string): AgentTimelineKey | undefined {
  const normalizedKind = (kind || "").toLowerCase();
  const normalizedTool = (tool || "").toLowerCase();
  if (normalizedKind.includes("vision")) {
    return "vision";
  }
  if (normalizedKind.includes("approval")) {
    return "approval";
  }
  if (normalizedKind.includes("shell") || normalizedTool.includes("shell")) {
    return "shell";
  }
  if (normalizedKind.includes("skill") || normalizedKind.includes("tool")) {
    return "skill";
  }
  if (normalizedKind.includes("plan")) {
    return "plan";
  }
  return undefined;
}

function ReasoningTracePanel({
  trace,
  fallbackLabel,
  elapsedSeconds,
  timelineOrder,
}: {
  trace?: AgentReasoningTrace;
  fallbackLabel: string;
  elapsedSeconds?: number;
  timelineOrder?: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const items = (trace?.items || []).filter((item) => (item.text || "").trim() || item.opaque);
  if (!items.length) {
    return null;
  }
  const status = thinkingTraceLabel(trace?.provider || trace?.providerLabel || fallbackLabel, trace?.model || "");
  const provider = trace?.providerLabel || trace?.provider || fallbackLabel || "model";
  const model = trace?.model || "";
  const title = model ? `${status} · ${provider} · ${model}` : `${status} · ${provider}`;
  return (
    <div className="text-muted-foreground" style={timelineOrder !== undefined ? { order: timelineOrder } : undefined}>
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
        <Sparkles className="h-3.5 w-3.5 shrink-0" />
        <span className="min-w-0 truncate text-xs">{title}</span>
        {elapsedSeconds ? <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{formatDuration(elapsedSeconds)}</span> : null}
        <span className={cn("shrink-0 text-xs", trace?.redacted ? "text-amber-600" : "text-muted-foreground")}>
          {items.length}
        </span>
      </button>
      {open ? (
        <div className="ml-6 mt-1 space-y-2 rounded-lg bg-muted/40 px-3 py-2 text-xs">
          <DataLine label={t("thinking.provider")} value={provider} />
          {model ? <DataLine label={t("thinking.model")} value={model} mono /> : null}
          {trace?.source ? <DataLine label={t("thinking.source")} value={trace.source} mono /> : null}
          {items.map((item, index) => (
            <OutputBlock
              key={`${item.title || item.kind || "reasoning"}-${index}`}
              label={item.title || item.kind || t("thinking.reasoning")}
              value={item.opaque ? t("thinking.opaqueRetained") : t("thinking.hiddenSummary")}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function RunRow({
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

function InlineApprovalCard({
  approval,
  action,
  onApprove,
  onReject,
  onModify,
}: {
  approval: AgentApproval;
  action?: ApprovalActionState;
  onApprove?: (approvalId: string) => void;
  onReject?: (approvalId: string) => void;
  onModify?: (approval: AgentApproval) => void;
}) {
  const { t } = useTranslation();
  const busy = Boolean(action);
  const title = approval.targetTool || approval.preview?.command || t("approval.requestTitle");
  const detail = approval.paramsSummary || approval.arguments || approval.preview;
  return (
    <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-sm">
      <div className="flex min-w-0 items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate font-medium">{t("approval.needsApproval")}</span>
            <Badge tone="warn" className="shrink-0">
              {approval.riskLevel || "write"}
            </Badge>
          </div>
          <div className="mt-1 truncate font-mono text-xs text-foreground">{title}</div>
          {approval.reason ? <div className="mt-1 text-xs text-muted-foreground">{approval.reason}</div> : null}
        </div>
      </div>
      {detail ? (
        <details className="mt-2 rounded-md border border-amber-500/20 bg-background/70 px-2 py-1.5 text-xs">
          <summary className="cursor-pointer text-muted-foreground">{t("approval.viewParameters")}</summary>
          <pre className="app-scrollbar mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px]">
            {formatPayload(detail)}
          </pre>
        </details>
      ) : null}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <Button variant="outline" className="h-8 px-3 text-xs" disabled={busy} onClick={() => onModify?.(approval)}>
          {action === "modify" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pencil className="h-3.5 w-3.5" />}
          {action === "modify" ? t("approval.modifying") : t("approval.modify")}
        </Button>
        <Button variant="outline" className="h-8 px-3 text-xs" disabled={busy} onClick={() => onReject?.(approval.id)}>
          {action === "reject" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
          {action === "reject" ? t("approval.rejecting") : t("approval.reject")}
        </Button>
        <Button className="h-8 px-3 text-xs" disabled={busy} onClick={() => onApprove?.(approval.id)}>
          {action === "approve" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          {action === "approve" ? t("approval.executing") : t("approval.approve")}
        </Button>
      </div>
    </div>
  );
}

function subAgentRoleLabel(role: string): string {
  switch (role) {
    case "project_index_review":
      return i18n.t("subagent.roles.projectIndexReview");
    case "outfit_package_inspection":
      return i18n.t("subagent.roles.outfitPackageInspection");
    case "validation_triage":
      return i18n.t("subagent.roles.validationTriage");
    case "selected_context_review":
      return i18n.t("contextMenu.askInNewSession");
    case "package_install_diagnosis":
      return i18n.t("subagent.roles.packageInstallDiagnosis");
    case "outfit_import_plan_review":
      return i18n.t("subagent.roles.outfitImportPlanReview");
    default:
      return role || i18n.t("subagent.roles.fallback");
  }
}

function subAgentStatusTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "completed") return "ok";
  if (status === "failed") return "danger";
  if (status === "queued" || status === "running" || status === "cancelling") return "warn";
  return "muted";
}

function displaySubAgentStatus(status: string): string {
  switch (status) {
    case "queued":
      return i18n.t("subagent.statusQueued");
    case "running":
      return i18n.t("subagent.statusRunningOne");
    case "cancelling":
      return i18n.t("subagent.statusCancelling");
    case "completed":
      return i18n.t("subagent.statusCompleted");
    case "failed":
      return i18n.t("subagent.statusFailed");
    default:
      return status || i18n.t("subagent.statusFallback");
  }
}

function displayPlanner(planner: string): string {
  if (planner === "deterministic-local") return i18n.t("planner.local");
  if (planner === "llm") return i18n.t("planner.ai");
  return planner || i18n.t("planner.fallback");
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

function formatPayload(value: unknown): string {
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
