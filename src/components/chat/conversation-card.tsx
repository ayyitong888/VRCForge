import {
  AlertTriangle,
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  FilePlus2,
  Loader2,
  Pencil,
  Save,
  SkipForward,
  RotateCcw,
  X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import type { AgentApproval } from "../../lib/api";
import { resolveAttachmentPayloadReferences, type AttachmentPayloadVault } from "../../lib/attachment-payloads";
import { projectRuntimeResponseForDisplay } from "../../lib/chat-timeline-presentation";
import type { ApprovalActionState, ChatAttachment, ConversationItem } from "../../lib/chat-types";
import { elapsedSecondsSince, providerWaitingWindow, type AgentRuntimePhase } from "../../lib/chat-streaming";
import { copyableAgentDialogueText } from "../../lib/conversation-utils";
import { projectVisionFailureNotice } from "../../lib/vision-failure-notice";
import type { PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { cn, formatCount } from "../../lib/utils";
import { formatLocalMessageTime, formatMessageDateSeparator } from "../../lib/message-time";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { ChatImagePreview } from "./chat-image-preview";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";
import { ChatMarkdown, ChatResourceImageContext } from "./chat-markdown";
import type { ChatResourceImageContext as ChatResourceImageContextValue } from "../../lib/api/chat-resource-image";
import { AttachmentStrip } from "./composer";
import { RunRow, buildAgentTimelineRows, buildDurableTimelineRows, formatPayload, formatRuntimeModelLine } from "./conversation-timeline";

export function ConversationCard({
  item,
  interleavedUserMessages = [],
  steeringNavAnchorKeys,
  steeringRevealSequence,
  initialUserContent,
  chatResourceImageContext,
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
  onOpenDoctor,
  saveOperationSummary,
  saveOperationTool,
  onSaveOperationAsSkill,
  questionAlreadyShown,
  onOpenChatById,
  runtimeActive,
}: {
  item: Exclude<ConversationItem, { type: "handoff_card" }>;
  interleavedUserMessages?: Extract<ConversationItem, { type: "user" }>[];
  steeringNavAnchorKeys?: ReadonlyMap<string, string>;
  steeringRevealSequence?: number;
  initialUserContent?: ReactNode;
  chatResourceImageContext?: Pick<ChatResourceImageContextValue, "endpoint" | "chatId" | "projectPath">;
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
  onOpenDoctor?: () => void;
  saveOperationSummary?: PathToSkillOperationSummary;
  saveOperationTool?: string;
  onSaveOperationAsSkill?: (summary: PathToSkillOperationSummary) => void;
  questionAlreadyShown?: boolean;
  /** Opens another conversation in this project scope by chat id. */
  onOpenChatById?: (chatId: string) => void;
  /** Exact session/CID activity from the current runtime ledger, when available. */
  runtimeActive?: boolean;
}) {
  const { t } = useTranslation();
  const storedAttachments = item.type === "user" || item.type === "question_answer" ? item.attachments : undefined;
  const restoredAttachments = useMemo(
    () => resolveAttachmentPayloadReferences(storedAttachments || [], attachmentPayloads),
    [storedAttachments, attachmentPayloads],
  );
  const streamingCreatedAt = item.type === "streaming" ? item.createdAt : undefined;
  const [streamingNowMs, setStreamingNowMs] = useState(() => Date.now());
  const [interleavedUsersExpanded, setInterleavedUsersExpanded] = useState(item.type === "streaming");
  useLayoutEffect(() => {
    if (steeringRevealSequence !== undefined) setInterleavedUsersExpanded(true);
  }, [steeringRevealSequence]);
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
    const deliveryLabel = item.queueStatus === "steering" ? t("chat.steering")
      : item.queueStatus === "waiting_for_resources" ? t("chat.waitingForResources")
      : item.queueStatus === "delivery_unverified" ? t("chat.deliveryUnverified")
      : item.queueStatus === "paused" ? t("chat.paused")
      : item.queueStatus === "cancelled" ? t("chat.cancelled") : t("chat.queued");
    const deliveryMetadata = item.queueStatus ? (
      <span
        className="inline-flex items-center gap-1 text-[11px] text-muted-foreground/80"
        data-user-message-status={item.queueStatus}
        role="status"
        title={deliveryLabel}
        aria-label={deliveryLabel}
      >
        {item.queueStatus === "steering" ? <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : null}
        {item.queueStatus === "delivery_unverified" ? t("chat.deliveryUnverifiedShort") : deliveryLabel}
      </span>
    ) : undefined;

    if (hasEditingState) {
      return (
        <div className="group flex min-w-0 justify-end">
          <div className="relative flex min-w-0 w-full max-w-[78%] flex-col items-end gap-2">
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
              {deliveryMetadata}
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

    const sourceChatId = item.forwardedFrom?.sourceChatId?.trim() || "";
    return (
      <div className="group flex min-w-0 justify-end" data-forwarded-by-agent={item.forwardedByAgent ? "true" : undefined}>
        <div className="relative flex min-w-0 max-w-[78%] flex-col items-end gap-2">
          {item.forwardedByAgent ? (
            <div className="flex w-full flex-wrap items-center justify-end gap-1.5 text-[11px] text-muted-foreground/80" data-forwarded-agent-attribution>
              <span>{t("chat.forwardedByAgent")}</span>
              {sourceChatId && onOpenChatById ? (
                <button
                  type="button"
                  className="inline-flex items-center gap-1 underline-offset-2 hover:text-foreground hover:underline"
                  data-open-conversation={sourceChatId}
                  onClick={() => onOpenChatById(sourceChatId)}
                >
                  <ExternalLink className="h-3 w-3" aria-hidden="true" />
                  {t("chat.openSourceConversation")}
                </button>
              ) : null}
            </div>
          ) : null}
          {imageAttachments.length ? <UserImageAttachments messageId={item.id} attachments={imageAttachments} /> : null}
          {item.text ? (
            <div className="min-w-0 max-w-full rounded-2xl bg-primary/[0.14] px-4 py-2.5 text-sm text-foreground dark:bg-primary/20 [&_.chat-markdown]:[overflow-wrap:anywhere]" data-user-message-bubble data-chat-quote-content>
              <UserMessageText text={item.text} />
            </div>
          ) : null}
          {otherAttachments.length ? (
            <div className="max-w-full rounded-xl bg-muted/70 px-3 py-2 text-sm">
              <AttachmentStrip attachments={otherAttachments} compact />
            </div>
          ) : null}
          <MessageActions
            align="right"
            createdAt={item.createdAt}
            metadata={deliveryMetadata}
            onCopy={() => onCopyItem?.(item)}
            onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
            onEdit={canEdit ? () => onEditItem?.(item.id) : undefined}
          />
        </div>
      </div>
    );
  }

  if (item.type === "question_answer") {
    const attachments = resolveAttachmentPayloadReferences(item.attachments || [], attachmentPayloads);
    const skipped = item.selectedOptionId === "skip" && !attachments.length;
    if (skipped) {
      return (
        <div className="min-w-0 max-w-2xl border-l border-border px-3 py-1 text-xs text-muted-foreground"
          data-question-answer={item.questionId} data-question-continuation-input data-question-skipped>
          {item.question ? (
            <details>
              <summary className="w-fit cursor-pointer rounded-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("questionCard.skippedConfirmation")}</summary>
              <div className="mt-1.5 border-l border-border pl-3 [&_.chat-markdown]:[overflow-wrap:anywhere]" data-chat-quote-content>
                <ChatMarkdown text={item.question} />
              </div>
            </details>
          ) : (
            <span className="inline-flex items-center gap-1.5 font-medium">
              <SkipForward className="h-3.5 w-3.5" aria-hidden="true" />
              {t("questionCard.skippedConfirmation")}
            </span>
          )}
        </div>
      );
    }
    const images = attachments.filter((attachment) => attachment.dataUrl && attachment.type.startsWith("image/"));
    const otherAttachments = attachments.filter((attachment) => !attachment.dataUrl || !attachment.type.startsWith("image/"));
    return (
      <div className="min-w-0 space-y-3" data-question-answer={item.questionId} data-question-continuation-input>
        {item.question && !questionAlreadyShown ? (
          <div className="text-sm [&_.chat-markdown]:[overflow-wrap:anywhere]" data-chat-question-prompt={item.questionId} data-chat-quote-content>
            <ChatMarkdown text={item.question} />
          </div>
        ) : null}
        <div className="ml-auto flex w-fit min-w-0 max-w-[min(92%,42rem)] flex-col items-end gap-2">
          {item.question || item.text ? (
            <div className="min-w-0 max-w-full space-y-1 rounded-2xl bg-primary/[0.14] px-4 py-2.5 text-sm text-foreground dark:bg-primary/20 [&_.chat-markdown]:[overflow-wrap:anywhere]" data-user-message-bubble>
              {item.question ? (
                <blockquote className="[&_.chat-markdown]:text-muted-foreground" data-question-answer-question>
                  <ChatMarkdown text={item.question} />
                </blockquote>
              ) : null}
              {item.text ? (
                <div data-question-answer-text data-question-answer-value data-chat-quote-content>
                  <ChatMarkdown text={item.text} />
                </div>
              ) : null}
            </div>
          ) : null}
          {images.length ? <UserImageAttachments messageId={item.id} attachments={images} /> : null}
          {otherAttachments.length ? <AttachmentStrip attachments={otherAttachments} compact /> : null}
        </div>
      </div>
    );
  }

  if (item.type === "error") {
    // Render by stable code first (localized copy); the bounded schema message
    // is the fallback and legacy backends only ever supplied that text.
    const codeKey = item.code ? `errors.runtime.${item.code}` : "";
    const runtimeUnreachable = item.text === "VRCForge runtime is still starting."
      || item.text === "Could not connect to the VRCForge runtime.";
    const codeText = runtimeUnreachable
      ? t("errors.runtime.runtime_unreachable")
      : codeKey && t(codeKey) !== codeKey ? t(codeKey) : item.text;
    return (
      <div className="px-1 py-1 text-xs text-muted-foreground" data-chat-turn-error>
        <div className="flex items-start gap-1.5" role={canRetry ? "alert" : undefined}>
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="break-words">{codeText}</span>
        </div>
        {runtimeUnreachable && canRetry && onOpenDoctor ? (
          <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onOpenDoctor}>
            {t("sidebar.doctor")}
          </Button>
        ) : null}
        <MessageActions
          onRetry={canRetry ? () => onRetryItem?.(item.id) : undefined}
        />
      </div>
    );
  }

  if (item.type === "streaming") {
    const captureEvents = (item.timeline || []).filter(event => event.kind === "tool_result" && event.payload.captures?.length);
    const firstUri = captureEvents[0]?.payload.captures?.[0]?.preview.resourceUri;
    let captureSessionId = "";
    try { captureSessionId = new URL(firstUri || "").searchParams.get("context")?.replace(/^runtime:/, "") || ""; } catch { /* No valid capture yet. */ }
    const liveImageContext = chatResourceImageContext && captureSessionId
      ? { ...chatResourceImageContext, sessionId: captureSessionId, clientTurnId: item.clientTurnId } : null;

    if (item.id.startsWith("question-continuation:") && !item.text && !item.timeline?.length) return null;
    const streamingElapsed = elapsedSecondsSince(item.createdAt, streamingNowMs);
    // Keep public assistant updates at their durable timeline position. The
    // live buffer is omitted once the same text has become a durable event.
    const lastAssistantText = [...(item.timeline || [])].reverse()
      .find(event => event.kind === "assistant" && event.payload.summary)?.payload.summary;
    const showLiveText = Boolean(item.text && item.text.trim() !== lastAssistantText?.trim());
    const timelineRows = buildDurableTimelineRows(item.timeline, undefined, "", [], {
      live: runtimeActive ?? true,
      renderElapsed: false,
      processTitleElapsed: streamingElapsed,
      providerLine: "",
      interleavedUserMessages,
      interleavedUsersExpanded,
      onInterleavedUsersExpandedChange: setInterleavedUsersExpanded,
      renderInterleavedUserMessage: (message) => renderInterleavedUserMessage(message, attachmentPayloads, steeringNavAnchorKeys?.get(message.id)),
      onOpenChatById,
    });
    return (
      <ChatResourceImageContext.Provider value={liveImageContext}>
      <div className="group flex justify-start" data-conversation-streaming-turn={item.clientTurnId}>
        <div className="relative flex w-full flex-col gap-3 text-sm leading-relaxed">
          {initialUserContent}
          {timelineRows.length ? <div className="space-y-3" data-vrcforge-live-runtime-timeline>{timelineRows}</div> : null}
          {showLiveText ? <div className="leading-relaxed" data-chat-quote-content><ChatMarkdown text={item.text} /></div> : null}
        </div>
      </div>
      </ChatResourceImageContext.Provider>
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
            createdAt={item.createdAt}
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
          <MessageActions createdAt={item.createdAt} />
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
  const sessionId = response.sessionId || response.session_id;
  const clientTurnId = response.clientTurnId || "";
  const resourceImageContext = chatResourceImageContext && sessionId && clientTurnId
    ? { ...chatResourceImageContext, sessionId, clientTurnId }
    : null;
  const shell = response.shell;
  const skill = response.skill;
  const vision = response.vision;
  const visionFailure = projectVisionFailureNotice(response, (key) => t(key));
  const write = response.write;
  const copyableReply = copyableAgentDialogueText(response);
  // The requested turn model is not proof of the model that answered. Keep
  // historical labels tied to response metadata and omit them when the
  // backend does not report an actual model.
  const providerLine = formatRuntimeModelLine("", response.model);
  const replySource = providerLine;
  const awaitingApproval = shell?.status === "pending_approval";
  const timelineRows = buildAgentTimelineRows({
    response,
    shell,
    vision,
    skill,
    write,
    approval,
    approvalAction,
    onModifyApproval,
    planLabel: replySource,
    providerLine,
    awaitingApproval,
    elapsedSeconds: item.elapsedSeconds,
    interleavedUserMessages,
    interleavedUsersExpanded,
    onInterleavedUsersExpandedChange: setInterleavedUsersExpanded,
    renderInterleavedUserMessage: (message) => renderInterleavedUserMessage(message, attachmentPayloads, steeringNavAnchorKeys?.get(message.id)),
    onOpenChatById,
    live: runtimeActive ?? false,
    t,
  });

  return (
    <ChatResourceImageContext.Provider value={resourceImageContext}>
      <div className="group flex justify-start" data-vrcforge-agent-client-turn={response.clientTurnId || undefined}>
        <div className="relative flex w-full flex-col gap-3 text-sm leading-relaxed">
          {initialUserContent}
          {timelineRows}
          {visionFailure ? (
            <p className="flex items-start gap-1.5 text-xs text-muted-foreground" data-chat-vision-error>
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span className="break-words">{visionFailure.message}</span>
            </p>
          ) : null}
          <MessageActions
            createdAt={item.completedAt || response.completedAt}
            onCopy={copyableReply ? () => onCopyItem?.({ ...item, response }) : undefined}
            onRetry={canRetry && response.continuationSource !== "foreground_interrupted" ? () => onRetryItem?.(item.id) : undefined}
            onEdit={canEdit ? () => onEditItem?.(item.id) : undefined}
            onSaveAsSkill={saveOperationSummary ? () => onSaveOperationAsSkill?.(saveOperationSummary) : undefined}
            saveOperationTool={saveOperationTool}
            saveOperationClientTurnId={response.clientTurnId}
          />
        </div>
      </div>
    </ChatResourceImageContext.Provider>
  );
}

function renderInterleavedUserMessage(
  message: Extract<ConversationItem, { type: "user" }>,
  attachmentPayloads?: AttachmentPayloadVault,
  navigationAnchor?: string,
) {
  const attachments = resolveAttachmentPayloadReferences(message.attachments || [], attachmentPayloads);
  const images = attachments.filter((attachment) => attachment.dataUrl && attachment.type.startsWith("image/"));
  const otherAttachments = attachments.filter((attachment) => !attachment.dataUrl || !attachment.type.startsWith("image/"));
  return (
    <div key={`steering-${message.id}`} className="group flex min-w-0 justify-end" data-chat-steering-message={message.id} data-chat-nav-anchor={navigationAnchor}>
      <div className="relative flex min-w-0 max-w-[78%] flex-col items-end gap-2">
        {images.length ? <UserImageAttachments messageId={message.id} attachments={images} /> : null}
        {message.text ? (
          <div className="min-w-0 max-w-full rounded-2xl bg-primary/[0.14] px-4 py-2.5 text-sm text-foreground dark:bg-primary/20 [&_.chat-markdown]:[overflow-wrap:anywhere]" data-user-message-bubble data-chat-quote-content>
            <UserMessageText text={message.text} />
          </div>
        ) : null}
        {otherAttachments.length ? (
          <div className="max-w-full rounded-xl bg-muted/70 px-3 py-2 text-sm">
            <AttachmentStrip attachments={otherAttachments} compact />
          </div>
        ) : null}
        <MessageActions createdAt={message.createdAt} />
      </div>
    </div>
  );
}

export function UserImageAttachments({
  messageId,
  attachments,
}: {
  messageId?: string;
  attachments: ChatAttachment[];
}) {
  const [preview, setPreview] = useState<ChatAttachment | null>(null);
  const { t } = useTranslation();
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
              className="block cursor-zoom-in overflow-hidden rounded-lg border border-border bg-muted/70 transition hover:border-foreground/30 focus:outline-none focus:ring-2 focus:ring-ring"
              onClick={() => setPreview(attachment)}
              aria-haspopup="dialog"
              aria-label={t("chat.imagePreview")}
              title={attachment.name}
            >
              <img src={attachment.dataUrl} alt={attachment.name} className="h-20 w-28 object-contain" />
            </button>
          </div>
        ))}
      </div>
      {preview?.dataUrl ? (
        <ChatImagePreview src={preview.dataUrl} alt={preview.name} onClose={() => setPreview(null)} />
      ) : null}
    </>
  );
}

export function StreamingPhaseStatus({ item, nowMs, showElapsed, runtimeActive }: { item: Extract<ConversationItem, { type: "streaming" }>; nowMs: number; showElapsed: boolean; runtimeActive?: boolean }) {
  const { t } = useTranslation();
  if (runtimeActive === false) return null;
  const elapsedSeconds = elapsedSecondsSince(item.createdAt, nowMs);
  const reconnectAttempt = item.providerRetry?.attempt;
  const waitingWindow = !reconnectAttempt && item.phase === "waiting_for_model"
    ? providerWaitingWindow(item.providerLastActivityAt, nowMs)
    : undefined;
  const phaseLabel = reconnectAttempt
    ? t("chat.runtimePhase.reconnecting", { attempt: reconnectAttempt, max: item.providerRetry?.max || 5 })
    : waitingWindow
      ? t("chat.runtimePhase.waitingForModelProgress", { window: waitingWindow, max: 5 })
    : streamingPhaseLabel(item.phase, t);
  const activeOperation = item.phase === "running_tool" ? currentStreamingOperation(item.timeline || []) : undefined;
  const label = activeOperation
    ? t("agent.timeline.runningTool", { tool: activeOperation })
    : phaseLabel;
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
    <div className="chat-current-activity mt-5 text-muted-foreground" data-chat-current-activity data-vrcforge-streaming-phase={item.phase || "unknown"} role="status" aria-live="polite" aria-atomic="true">
      {reconnecting || waiting ? (
        <details className="group">
          <summary className="group flex cursor-pointer list-none items-center gap-2 text-left [&::-webkit-details-marker]:hidden">
            <ChevronRight className="h-3.5 w-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
            <span className="chat-current-activity__label">{label}</span>
            {showElapsed && elapsedSeconds !== undefined ? <span>{t("agent.elapsed", { time: `${elapsedSeconds}s` })}</span> : null}
          </summary>
          <p className="mt-1 pl-5 text-xs text-muted-foreground/80">{reconnectDetail}</p>
        </details>
      ) : (
        <div className="flex items-center gap-2">
          <span className="chat-current-activity__label">{label}</span>
          {showElapsed && elapsedSeconds !== undefined ? <span>{t("agent.elapsed", { time: `${elapsedSeconds}s` })}</span> : null}
        </div>
      )}
    </div>
  );
}

function currentStreamingOperation(timeline: NonNullable<Extract<ConversationItem, { type: "streaming" }>['timeline']>): string | undefined {
  const events = [...timeline].sort((left, right) => left.sequence - right.sequence);
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (!["tool_call", "command", "file_edit", "subagent"].includes(event.kind)) continue;
    const status = String(event.payload.status || event.payload.subagentStatus || "").toLowerCase();
    if (["ok", "success", "succeeded", "done", "applied", "completed", "complete", "failed", "denied", "blocked", "cancelled", "canceled"].includes(status)) continue;
    const actionId = event.payload.actionId;
    if (actionId && events.some((candidate) => candidate.sequence > event.sequence
      && candidate.kind === "tool_result"
      && candidate.payload.actionId === actionId)) continue;
    const label = event.payload.label || event.payload.tool;
    if (label) return label;
  }
  return undefined;
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
  metadata,
  onCopy,
  onRetry,
  onEdit,
  onSaveAsSkill,
  saveOperationTool,
  saveOperationClientTurnId,
}: {
  align?: "left" | "right";
  createdAt?: string;
  metadata?: ReactNode;
  onCopy?: () => void;
  onRetry?: () => void;
  onEdit?: () => void;
  onSaveAsSkill?: () => void;
  saveOperationTool?: string;
  saveOperationClientTurnId?: string;
}) {
  const { t } = useTranslation();
  const timeLabel = formatLocalMessageTime(createdAt, i18n.language);
  const hasActions = Boolean(metadata || timeLabel || onCopy || onRetry || onEdit || onSaveAsSkill);
  if (!hasActions) {
    return null;
  }
  return (
    <div
      data-chat-message-actions
      className={cn(
        "order-last mt-1 flex items-center gap-1 px-1 text-muted-foreground",
        align === "right" ? "justify-end" : "justify-start",
      )}
    >
      {timeLabel ? <span className="px-1 text-xs text-muted-foreground/80">{timeLabel}</span> : null}
      {metadata}
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

export function MessageDateSeparator({ timestamp }: { timestamp?: string | number }) {
  const label = formatMessageDateSeparator(timestamp, i18n.language);
  if (!label) return null;
  return (
    <div className="flex items-center gap-3 py-1 text-[11px] text-muted-foreground" data-chat-date-separator>
      <div className="h-px flex-1 bg-border/60" />
      <time dateTime={typeof timestamp === "number" ? new Date(timestamp).toISOString() : timestamp} className="shrink-0 px-1" data-chat-message-date>
        {label}
      </time>
      <div className="h-px flex-1 bg-border/60" />
    </div>
  );
}
