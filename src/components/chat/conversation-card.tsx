import {
  Bot,
  Check,
  Copy,
  Loader2,
  MessageSquare,
  Pencil,
  Save,
  RotateCcw,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import type { AgentApproval } from "../../lib/api";
import type { ApprovalActionState, ChatAttachment, ConversationItem } from "../../lib/chat-types";
import { copyableAgentDialogueText } from "../../lib/conversation-utils";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "../../lib/subagent-ui";
import { cn, formatCount } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";
import { ChatMarkdown } from "./chat-markdown";
import { AttachmentStrip } from "./composer";
import { RunRow, buildAgentTimelineRows, displayPlanner, formatPayload } from "./conversation-timeline";

export function ConversationCard({
  item,
  approval,
  approvalAction,
  canRetry,
  canEdit,
  editing,
  editingText,
  editingAttachments,
  onEditTextChange,
  onEditAttachmentRemove,
  onEditItemSave,
  onEditItemCancel,
  onCopyItem,
  onRetryItem,
  onEditItem,
  onModifyApproval,
  onImportAttachment,
  onOpenDoctor,
}: {
  item: ConversationItem;
  approval?: AgentApproval | null;
  approvalAction?: ApprovalActionState;
  editing?: boolean;
  editingText?: string;
  editingAttachments?: ChatAttachment[];
  onEditTextChange?: (value: string) => void;
  onEditAttachmentRemove?: (attachmentId: string) => void;
  onEditItemSave?: () => void;
  onEditItemCancel?: () => void;
  canRetry?: boolean;
  canEdit?: boolean;
  onCopyItem?: (item: ConversationItem) => void;
  onRetryItem?: (itemId: string) => void;
  onEditItem?: (itemId: string) => void;
  onApprove?: (approvalId: string) => void;
  onReject?: (approvalId: string) => void;
  onModifyApproval?: (approval: AgentApproval) => void;
  onImportAttachment?: (attachment: ChatAttachment) => void;
  onOpenDoctor?: () => void;
}) {
  const { t } = useTranslation();
  if (item.type === "user") {
    const attachments = item.attachments || [];
    const displayedText = editing ? editingText : item.text;
    const draftAttachments = editingAttachments || attachments;
    const imageAttachments = draftAttachments.filter((attachment) => attachment.dataUrl && attachment.type.startsWith("image/"));
    const otherAttachments = draftAttachments.filter((attachment) => !attachment.dataUrl || !attachment.type.startsWith("image/"));
    const hasEditingState = Boolean(editing);

    if (hasEditingState) {
      return (
        <div className="group flex justify-end">
          <div className="relative flex max-w-[78%] flex-col items-end gap-2">
            {item.queuedFrom ? (
              <div className="flex items-center gap-1 rounded-full bg-muted/70 px-2 py-1 text-[11px] text-muted-foreground">
                <MessageSquare className="h-3 w-3" />
                {t("chat.queuedSent")}
              </div>
            ) : null}
            <textarea
              value={displayedText}
              onChange={(event) => onEditTextChange?.(event.target.value)}
              className="min-h-[90px] w-full resize-none rounded-2xl border border-muted bg-muted px-3 py-2 text-sm outline-none"
            />
            {imageAttachments.length ? (
              <div className="flex max-w-full flex-wrap justify-end gap-2">
                {imageAttachments.map((attachment) => (
                  <div key={attachment.id} className="relative">
                    <button
                      type="button"
                      className="block overflow-hidden rounded-lg border border-border bg-muted/70"
                      title={attachment.name}
                    >
                      <img src={attachment.dataUrl} alt={attachment.name} className="h-20 w-28 object-cover" />
                    </button>
                    <button
                      type="button"
                      className="absolute right-1 top-1 rounded-md border border-border bg-background/90 p-1 text-xs text-foreground"
                      onClick={() => onEditAttachmentRemove?.(attachment.id)}
                      title={t("chat.removeAttachment")}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}
            {otherAttachments.length ? (
              <div className="max-w-full rounded-xl bg-muted/70 px-3 py-2 text-sm">
                <AttachmentStrip
                  attachments={otherAttachments}
                  compact
                  onRemove={onEditAttachmentRemove}
                />
              </div>
            ) : null}
            <div className="flex items-center gap-1 pt-1">
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={onEditItemSave}
                title={t("chat.saveEdit")}
                aria-label={t("chat.saveEdit")}
              >
                <Save className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={onEditItemCancel}
                title={t("chat.cancelEdit")}
                aria-label={t("chat.cancelEdit")}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="group flex justify-end">
        <div className="relative flex max-w-[78%] flex-col items-end gap-2">
          {item.queuedFrom ? (
            <div className="flex items-center gap-1 rounded-full bg-muted/70 px-2 py-1 text-[11px] text-muted-foreground">
              <MessageSquare className="h-3 w-3" />
              {t("chat.queuedSent")}
            </div>
          ) : null}
          {imageAttachments.length ? <UserImageAttachments attachments={imageAttachments} onImport={onImportAttachment} /> : null}
          {item.text ? (
            <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm text-foreground">
              <ChatMarkdown text={item.text} />
            </div>
          ) : null}
          {otherAttachments.length ? (
            <div className="max-w-full rounded-xl bg-muted/70 px-3 py-2 text-sm">
              <AttachmentStrip attachments={otherAttachments} onImport={onImportAttachment} compact />
            </div>
          ) : null}
          <MessageActions
            align="right"
            createdAt={item.createdAt || item.id}
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
            onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          />
        </div>
      </div>
    );
  }

  if (item.type === "approval_revision") {
    return (
      <div className="group flex justify-start" data-approval-revision={item.approvalId}>
        <div className="relative w-full max-w-[85%] rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-muted-foreground">
          <div className="font-medium text-foreground">{t("approval.revisionRequestedTitle")}</div>
          <p className="mt-1">{t("approval.revisionRequestedDescription", { target: item.targetTool || t("approval.thisApproval") })}</p>
          <p className="mt-1">{t("approval.revisionAwaitingUserInput")}</p>
          <MessageActions createdAt={item.createdAt || item.id} />
        </div>
      </div>
    );
  }

  if (item.type === "compact") {
    const running = item.status === "running";
    const usageChange = typeof item.beforeTokens === "number" && typeof item.afterTokens === "number"
      ? t("compact.usageChange", { before: formatCount(item.beforeTokens), after: formatCount(item.afterTokens) })
      : "";
    return (
      <div className="group flex max-w-[85%] items-center gap-3 py-1 text-xs text-muted-foreground">
        <div className="h-px flex-1 bg-border/70" />
        <div className="flex shrink-0 items-center gap-1.5">
          {running ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
          <span>{item.text}{usageChange ? ` · ${usageChange}` : ""}</span>
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
  const write = response.write;
  const copyableReply = copyableAgentDialogueText(response);
  const providerLine = item.providerLabel || response.plan.plannerLabel || displayPlanner(response.plan.planner);
  const replySource = item.model ? `${providerLine} · ${item.model}` : providerLine;
  const awaitingApproval = shell?.status === "pending_approval";
  const nextStep = response.plan.nextStep || "";
  const showIntent = Boolean(nextStep) && nextStep !== "await_user_instruction" && nextStep !== "done";
  const timelineRows = buildAgentTimelineRows({
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
    planLabel: replySource,
    providerLine,
    awaitingApproval,
    elapsedSeconds: item.elapsedSeconds,
    t,
  });

  return (
    <div className="group flex justify-start">
      <div className="relative flex w-full max-w-[85%] flex-col gap-1.5">
        {timelineRows}
        <MessageActions
          createdAt={item.createdAt || item.id}
          onCopy={copyableReply ? () => onCopyItem?.(item) : undefined}
          onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          onEdit={canEdit ? () => onEditItem?.(item.id) : undefined}
        />
      </div>
    </div>
  );
}

export function UserImageAttachments({
  attachments,
  onImport,
}: {
  attachments: ChatAttachment[];
  onImport?: (attachment: ChatAttachment) => void;
}) {
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
          <div key={attachment.id} className="relative">
            <button
              type="button"
              className="group/image block overflow-hidden rounded-lg border border-border bg-muted/70 transition hover:border-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
              onClick={() => setPreview(attachment)}
              aria-label={t("chat.imagePreview")}
              title={attachment.name}
            >
              <img src={attachment.dataUrl} alt={attachment.name} className="h-20 w-28 object-cover transition group-hover/image:scale-[1.02]" />
            </button>
            {attachment.vaultPayloadHash && onImport ? (
              <button
                type="button"
                className="absolute bottom-1 left-1 rounded border border-border bg-background/90 px-1.5 py-0.5 text-[10px] text-foreground"
                onClick={() => onImport(attachment)}
                title={t("attachments.importToProject")}
              >
                {t("attachments.importToProject")}
              </button>
            ) : null}
          </div>
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
  onCopy,
  onRetry,
  onEdit,
}: {
  align?: "left" | "right";
  createdAt?: string;
  onCopy?: () => void;
  onRetry?: () => void;
  onEdit?: () => void;
}) {
  const { t } = useTranslation();
  const timeLabel = formatMessageTime(createdAt, i18n.language);
  const hasActions = Boolean(timeLabel || onCopy || onRetry || onEdit);
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
