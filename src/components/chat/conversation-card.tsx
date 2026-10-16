import {
  Check,
  ChevronRight,
  Copy,
  FilePlus2,
  Loader2,
  MessageSquare,
  Pencil,
  Save,
  RotateCcw,
  Wrench,
  X,
} from "lucide-react";
import { isValidElement, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import type { AgentApproval } from "../../lib/api";
import { resolveAttachmentPayloadReferences, type AttachmentPayloadVault } from "../../lib/attachment-payloads";
import { projectRuntimeResponseForDisplay } from "../../lib/chat-timeline-presentation";
import type { ApprovalActionState, ChatAttachment, ConversationItem } from "../../lib/chat-types";
import { elapsedSecondsSince, providerWaitingWindow, type AgentRuntimePhase } from "../../lib/chat-streaming";
import { copyableAgentDialogueText } from "../../lib/conversation-utils";
import type { PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { cn, formatCount } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";
import { ChatMarkdown } from "./chat-markdown";
import { AttachmentStrip } from "./composer";
import { RunRow, buildAgentTimelineRows, buildDurableTimelineRows, displayPlanner, formatPayload, formatRuntimeModelLine } from "./conversation-timeline";

export function ConversationCard({
  item,
  attachmentPayloads,
  approval,
  approvalAction,
  canRetry,
  canEdit,
  editing,
  editingText,
  editSaveDisabledReason,
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
  saveOperationSummary,
  saveOperationTool,
  onSaveOperationAsSkill,
}: {
  item: Exclude<ConversationItem, { type: "handoff_card" }>;
  attachmentPayloads?: AttachmentPayloadVault;
  approval?: AgentApproval | null;
  approvalAction?: ApprovalActionState;
  editing?: boolean;
  editingText?: string;
  editSaveDisabledReason?: string;
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
  onModifyApproval?: (approval: AgentApproval, revisionReason?: string, denyReasonCode?: string) => void;
  onImportAttachment?: (attachment: ChatAttachment) => void;
  onOpenDoctor?: () => void;
  saveOperationSummary?: PathToSkillOperationSummary;
  saveOperationTool?: string;
  onSaveOperationAsSkill?: (summary: PathToSkillOperationSummary) => void;
}) {
  const { t } = useTranslation();
  const storedAttachments = item.type === "user" ? item.attachments : undefined;
  const restoredAttachments = useMemo(
    () => resolveAttachmentPayloadReferences(storedAttachments || [], attachmentPayloads),
    [storedAttachments, attachmentPayloads],
  );
  const streamingCreatedAt = item.type === "streaming" ? item.createdAt : undefined;
  const [streamingNowMs, setStreamingNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!streamingCreatedAt) return undefined;
    setStreamingNowMs(Date.now());
    const timer = window.setInterval(() => setStreamingNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [streamingCreatedAt]);
  if (item.type === "user") {
    const attachments = restoredAttachments;
    const displayedText = editing ? editingText : item.text;
    const draftAttachments = editing ? editingAttachments || attachments : attachments;
    const imageAttachments = draftAttachments.filter((attachment) => attachment.dataUrl && attachment.type.startsWith("image/"));
    const otherAttachments = draftAttachments.filter((attachment) => !attachment.dataUrl || !attachment.type.startsWith("image/"));
    const hasEditingState = Boolean(editing);

    if (hasEditingState) {
      return (
        <div className="group flex min-w-0 justify-end">
          <div className="relative flex min-w-0 w-full max-w-[78%] flex-col items-end gap-2">
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
                disabled={Boolean(editSaveDisabledReason)}
                title={editSaveDisabledReason || t("chat.saveEdit")}
                aria-label={t("chat.saveEdit")}
              >
                <Save className="h-3.5 w-3.5" />
              </button>
              {editSaveDisabledReason ? <span role="status" className="text-xs text-muted-foreground">{editSaveDisabledReason}</span> : null}
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
      <div className="group flex min-w-0 justify-end">
        <div className="relative flex min-w-0 max-w-[78%] flex-col items-end gap-2">
          {item.queuedFrom || item.queueStatus ? (
            <div className="flex items-center gap-1 rounded-full bg-muted/70 px-2 py-1 text-[11px] text-muted-foreground">
              <MessageSquare className="h-3 w-3" />
              {item.queueStatus === "steering" ? t("chat.steering") : item.queueStatus === "waiting_for_resources" ? t("chat.waitingForResources") : item.queueStatus === "delivery_unverified" ? t("chat.deliveryUnverified") : item.queueStatus === "paused" ? t("chat.paused") : item.queueStatus === "cancelled" ? t("chat.cancelled") : t("chat.queued")}
            </div>
          ) : null}
          {imageAttachments.length ? <UserImageAttachments messageId={item.id} attachments={imageAttachments} onImport={onImportAttachment} /> : null}
          {item.text ? (
            <div className="min-w-0 max-w-full rounded-2xl bg-muted px-4 py-2.5 text-sm text-foreground [&_.chat-markdown]:[overflow-wrap:anywhere]" data-user-message-bubble>
              <UserMessageText text={item.text} />
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
          onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
        />
      </div>
    );
  }

  if (item.type === "streaming") {
    const runtimeModelLabel = formatRuntimeModelLine(item.providerLabel, item.model);
    const streamingElapsed = elapsedSecondsSince(item.createdAt, streamingNowMs);
    // Keep public assistant updates at their durable timeline position. The
    // trailing event is suppressed only when it is the same text as the live
    // buffer, so progress updates remain visible without duplicating the tail.
    const timelineRows = buildDurableTimelineRows(item.timeline, undefined, "", [], {
      live: true,
      renderElapsed: false,
      processTitleElapsed: streamingElapsed,
      providerLine: runtimeModelLabel,
      suppressTrailingAssistantText: item.text,
    });
    const hasTimelineProcessRows = timelineRows.some((row) => (
      isValidElement(row)
      && Boolean((row.props as { "data-agent-turn-process-group"?: boolean })["data-agent-turn-process-group"])
    ));
    return (
      <div className="group flex justify-start" data-conversation-streaming-turn={item.clientTurnId}>
        <div className="relative flex w-full max-w-[85%] flex-col gap-3 px-1 text-sm leading-relaxed">
          <StreamingPhaseStatus item={item} nowMs={streamingNowMs} showElapsed={false} />
          {timelineRows.length ? <div className="space-y-3" data-vrcforge-live-runtime-timeline>{timelineRows}</div> : null}
          {item.text ? <div className="leading-relaxed"><ChatMarkdown text={item.text} /></div> : null}
          {(!hasTimelineProcessRows && streamingElapsed !== undefined) || runtimeModelLabel ? (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-border/60 pt-2 text-[10px] leading-relaxed text-muted-foreground">
              {!hasTimelineProcessRows && streamingElapsed !== undefined ? (
                <span>{t("agent.turnDuration", { duration: `${streamingElapsed}s` })}</span>
              ) : null}
              {runtimeModelLabel ? <span className="text-muted-foreground/75">{runtimeModelLabel}</span> : null}
            </div>
          ) : null}
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
          <p className="mt-1">{t("approval.revisionRetrying")}</p>
          {item.reason ? <p className="mt-1" data-approval-revision-reason>{t("approval.revisionReasonLabel")}: {item.reason}</p> : null}
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
    // Keep older transcript records intact; subagent details live in the sidebar.
    return null;
  }

  if (item.type === "timeline_event") {
    const timelineRows = buildDurableTimelineRows([item.event], undefined, "", [], { live: true });
    if (!timelineRows.length) return null;
    return (
      <div className="group flex justify-start" data-vrcforge-timeline-event={item.event.kind}>
        <div className="relative w-full max-w-[85%] space-y-1.5">
          {timelineRows}
        </div>
      </div>
    );
  }

  const response = projectRuntimeResponseForDisplay(item.response, (key) => t(key));
  const shell = response.shell;
  const skill = response.skill;
  const vision = response.vision;
  const write = response.write;
  const copyableReply = copyableAgentDialogueText(response);
  const providerLine = formatRuntimeModelLine(item.providerLabel, item.model);
  const replySource = providerLine;
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
    <div className="group flex justify-start" data-vrcforge-agent-client-turn={response.clientTurnId || undefined}>
      <div className="relative flex w-full max-w-[85%] flex-col gap-3 text-sm leading-relaxed">
        {timelineRows}
        <MessageActions
          onCopy={copyableReply ? () => onCopyItem?.({ ...item, response }) : undefined}
          onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
          onEdit={canEdit ? () => onEditItem?.(item.id) : undefined}
          onSaveAsSkill={saveOperationSummary ? () => onSaveOperationAsSkill?.(saveOperationSummary) : undefined}
          saveOperationTool={saveOperationTool}
          saveOperationClientTurnId={response.clientTurnId}
        />
      </div>
    </div>
  );
}

export function UserImageAttachments({
  messageId,
  attachments,
  onImport,
}: {
  messageId?: string;
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
  useEffect(() => {
    const openRequestedAttachment = (event: Event) => {
      const detail = (event as CustomEvent<{ messageId?: string; attachmentId?: string }>).detail;
      if (!detail || detail.messageId !== messageId) {
        return;
      }
      const attachment = attachments.find((item) => item.id === detail.attachmentId && Boolean(item.dataUrl));
      if (attachment) {
        setPreview(attachment);
      }
    };
    window.addEventListener("vrcforge-open-chat-attachment", openRequestedAttachment);
    return () => window.removeEventListener("vrcforge-open-chat-attachment", openRequestedAttachment);
  }, [attachments, messageId]);
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

function StreamingPhaseStatus({ item, nowMs, showElapsed }: { item: Extract<ConversationItem, { type: "streaming" }>; nowMs: number; showElapsed: boolean }) {
  const { t } = useTranslation();
  const elapsedSeconds = elapsedSecondsSince(item.createdAt, nowMs);
  const reconnectAttempt = item.providerRetry?.attempt;
  const waitingWindow = !reconnectAttempt && item.phase === "waiting_for_model"
    ? providerWaitingWindow(item.providerLastActivityAt, nowMs)
    : undefined;
  const label = reconnectAttempt
    ? t("chat.runtimePhase.reconnecting", { attempt: reconnectAttempt, max: item.providerRetry?.max || 5 })
    : waitingWindow
      ? t("chat.runtimePhase.waitingForModelProgress", { window: waitingWindow, max: 5 })
    : streamingPhaseLabel(item.phase, t);
  const reconnecting = reconnectAttempt !== undefined;
  const waiting = waitingWindow !== undefined;
  const reconnectDetail = reconnectAttempt === item.providerRetry?.max
    ? t("chat.runtimePhase.reconnectingRetry")
    : waitingWindow !== undefined
      ? waitingWindow === 5
        ? t("chat.runtimePhase.waitingForModelTimeout")
        : t("chat.runtimePhase.waitingForModelDetail")
      : t("chat.runtimePhase.reconnectingDetail");
  return (
    <div className="text-muted-foreground" data-vrcforge-streaming-phase={item.phase || "unknown"}>
      {reconnecting || waiting ? (
        <details className="group">
          <summary className="group flex cursor-pointer list-none items-center gap-2 text-left [&::-webkit-details-marker]:hidden">
            <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <span>{label}</span>
            {showElapsed && elapsedSeconds !== undefined ? <span>{t("agent.elapsed", { time: `${elapsedSeconds}s` })}</span> : null}
          </summary>
          <p className="mt-1 pl-5 text-xs text-muted-foreground/80">{reconnectDetail}</p>
        </details>
      ) : (
        <div className="flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span>{label}</span>
          {showElapsed && elapsedSeconds !== undefined ? <span>{t("agent.elapsed", { time: `${elapsedSeconds}s` })}</span> : null}
        </div>
      )}
    </div>
  );
}

function UserMessageText({ text }: { text: string }) {
  const { t } = useTranslation();
  // Presentation only: the original item remains the source for copy/edit/history.
  const longMessage = text.length > 600 || text.split("\n").length > 8;
  if (!longMessage) return <ChatMarkdown text={text} />;
  return (
    <details className="group/message min-w-0" data-user-message-disclosure>
      <summary className="cursor-pointer list-none rounded-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <span className="mb-2 line-clamp-6 whitespace-pre-wrap leading-relaxed [overflow-wrap:anywhere] group-open/message:hidden">
          {text.slice(0, 600)}{text.length > 600 ? "…" : ""}
        </span>
        <span className="flex items-center gap-1 text-xs text-muted-foreground">
          <ChevronRight className="h-3.5 w-3.5 shrink-0 transition-transform group-open/message:rotate-90" aria-hidden="true" />
          <span className="group-open/message:hidden">{t("common.expand")}</span>
          <span className="hidden group-open/message:inline">{t("common.collapse")}</span>
        </span>
      </summary>
      <div className="mt-2 min-w-0"><ChatMarkdown text={text} /></div>
    </details>
  );
}

function streamingPhaseLabel(phase: AgentRuntimePhase | undefined, t: typeof i18n.t): string {
  switch (phase) {
    case "preparing":
      return t("chat.runtimePhase.preparing");
    case "waiting_for_model":
      return t("chat.runtimePhase.waitingForModel");
    case "receiving_response":
      return t("chat.runtimePhase.receivingResponse");
    case "running_tool":
      return t("chat.runtimePhase.runningTool");
    case "waiting_for_approval":
      return t("chat.runtimePhase.waitingForApproval");
    case "verifying":
      return t("chat.runtimePhase.verifying");
    default:
      return t("chat.executingHint");
  }
}

function MessageActions({
  align = "left",
  createdAt,
  onCopy,
  onRetry,
  onEdit,
  onSaveAsSkill,
  saveOperationTool,
  saveOperationClientTurnId,
}: {
  align?: "left" | "right";
  createdAt?: string;
  onCopy?: () => void;
  onRetry?: () => void;
  onEdit?: () => void;
  onSaveAsSkill?: () => void;
  saveOperationTool?: string;
  saveOperationClientTurnId?: string;
}) {
  const { t } = useTranslation();
  const timeLabel = formatMessageTime(createdAt, i18n.language);
  const hasActions = Boolean(timeLabel || onCopy || onRetry || onEdit || onSaveAsSkill);
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
      {onSaveAsSkill ? (
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={onSaveAsSkill}
          title={t("workspace.saveOperationAsSkill")}
          aria-label={t("workspace.saveOperationAsSkill")}
          data-vrcforge-save-operation-as-skill
          data-vrcforge-save-operation-tool={saveOperationTool || ""}
          data-vrcforge-save-operation-client-turn={saveOperationClientTurnId || ""}
        >
          <FilePlus2 className="h-3.5 w-3.5" />
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
