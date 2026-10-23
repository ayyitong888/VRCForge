import { AlertTriangle, ArrowDown, ArrowUp, Loader2, MoreHorizontal, Pause, Play, X } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent, type Ref } from "react";
import { useTranslation } from "react-i18next";
import { updateAgentGoal } from "../../lib/api";
import type { AgentApproval, AgentGoal, AgentGoalBackgroundAcknowledgement, AgentGoalDelivery, AgentGoalProviderWarning, AgentGoalRenderedRecap, AgentQuestion, AgentRuntimeResponse, AgentRuntimeRun, PermissionState } from "../../lib/api";
import type {
  ApprovalActionState,
  ChatAttachment,
  ComposerAction,
  ComposerActionId,
  ContextUsage,
  ChatCompactionState,
  ConversationItem,
} from "../../lib/chat-types";
import { Composer } from "./composer";
import { AgentQuestionCard } from "./agent-question-card";
import { projectAgentQuestionContinuation, questionContinuationCancelTarget, questionContinuationDisplay } from "../../hooks/use-runtime-turn-continuation";
import { BackgroundGoalCatchUpCard } from "./background-goal-catch-up-card";
import { ConversationCard, MessageDateSeparator, StreamingPhaseStatus } from "./conversation-card";
import { SessionHandoffCard } from "./session-handoff-card";
import { SessionHandoffSend, type SessionHandoffTargetChat } from "./session-handoff-send";
import { ScopedPendingApprovalCard } from "../approvals/scoped-pending-approval-card";
import { Button } from "../ui/button";
import { matchPathToSkillRuntimeOperation, type PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { mergeConversationTimelineItems } from "../../lib/chat-thread";
import { projectConversationSteeringMessages } from "../../lib/chat-steering-projection";
import { conversationItemRenderKey } from "../../lib/conversation-utils";
import type { AttachmentPayloadVault } from "../../lib/attachment-payloads";
import { conversationItemTimestamp, shouldShowMessageDateSeparator } from "../../lib/message-time";

function formatGoalElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return String(seconds) + "s";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return String(minutes) + "m " + String(rest) + "s";
}

export type QueuedChatTurn = {
  id: string;
  text: string;
  attachments: ChatAttachment[];
  queueStatus?: "steering" | "queued" | "waiting_for_resources" | "delivery_unverified" | "paused" | "cancelled";
  composerQueued?: boolean;
};

function PendingQueuedTurnCard({
  turn,
  sending,
  onEdit,
  onRemove,
  onSteer,
}: {
  turn: QueuedChatTurn;
  sending: boolean;
  onEdit?: (turnId: string, patch: { text?: string; attachments?: ChatAttachment[] }) => void | Promise<unknown>;
  onRemove?: (turnId: string) => void;
  onSteer?: (turnId: string) => void;
}) {
  const { t } = useTranslation();
  const [draftText, setDraftText] = useState(turn.text);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => setDraftText(turn.text), [turn.id, turn.text]);
  const dirty = draftText !== turn.text;
  async function save(patch: { text?: string; attachments?: ChatAttachment[] }) {
    if (!onEdit || saving) return;
    setSaving(true);
    try {
      await onEdit(turn.id, patch);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="relative flex items-start gap-2 rounded-lg border border-border/60 bg-background/80 p-2" data-chat-pending-turn={turn.id}>
      <div className="min-w-0 flex-1 space-y-1.5">
        {editing ? (
          <textarea
            className="app-scrollbar min-h-10 w-full resize-y rounded-md bg-transparent px-1 py-1 text-sm outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={draftText}
            aria-label={t("chat.editQueued")}
            onChange={(event) => setDraftText(event.currentTarget.value)}
            disabled={saving}
            data-chat-pending-turn-text
          />
        ) : (
          <p className="whitespace-pre-wrap break-words px-1 py-1 text-sm" data-chat-pending-turn-preview>{turn.text || t("chat.pendingQueue")}</p>
        )}
        {turn.attachments.length ? (
          <div className="flex flex-wrap gap-1.5">
            {turn.attachments.map((attachment) => (
              <span key={attachment.id} className="inline-flex max-w-full items-center gap-1 rounded-md bg-muted px-2 py-1 text-xs" data-chat-pending-turn-attachment={attachment.id}>
                <span className="max-w-48 truncate">{attachment.name}</span>
                  {editing ? <button
                  type="button"
                  className="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
                  aria-label={t("attachments.remove")}
                  title={t("attachments.remove")}
                  disabled={dirty || saving}
                  onClick={() => void save({ text: draftText, attachments: turn.attachments.filter((item) => item.id !== attachment.id) })}
                >
                  <X className="h-3 w-3" />
                  </button> : null}
              </span>
            ))}
          </div>
        ) : null}
      </div>
        {editing && dirty ? (
          <Button type="button" variant="outline" className="h-8 shrink-0 px-2 text-xs" disabled={saving} onClick={() => void save({ text: draftText })} data-chat-pending-turn-save={turn.id}>
            {t("chat.saveQueued")}
          </Button>
        ) : null}
        {sending ? (
          <Button type="button" variant="outline" className="h-8 shrink-0 px-2 text-xs" disabled={editing && dirty} onClick={() => onSteer?.(turn.id)} data-chat-pending-turn-steer={turn.id}>
            <ArrowUp className="mr-1 h-3 w-3" />
            {t("chat.guideNow")}
          </Button>
        ) : null}
        <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" aria-label={t("chat.removeQueued")} title={t("chat.removeQueued")} onClick={() => onRemove?.(turn.id)} data-chat-pending-turn-remove={turn.id}>
          <X className="h-4 w-4" />
        </Button>
        <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" aria-label={t("chat.moreQueued")} title={t("chat.moreQueued")} aria-expanded={menuOpen} onClick={() => setMenuOpen((value) => !value)} data-chat-pending-turn-more={turn.id}>
          <MoreHorizontal className="h-4 w-4" />
        </Button>
        {menuOpen ? (
          <div role="menu" className="absolute right-2 top-11 z-20 min-w-36 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md" data-chat-pending-turn-menu={turn.id}>
            <button type="button" role="menuitem" className="w-full rounded-sm px-2 py-1.5 text-left text-sm hover:bg-accent" onClick={() => { setEditing(true); setMenuOpen(false); }} data-chat-pending-turn-edit={turn.id}>
              {t("chat.editQueuedMessage")}
            </button>
          </div>
        ) : null}
        {editing ? (
          <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" onClick={() => { setDraftText(turn.text); setEditing(false); }} data-chat-pending-turn-edit-cancel={turn.id}>
            {t("common.cancel")}
          </Button>
        ) : null}
    </div>
  );
}

export function ChatWorkspace({
  projectPromptTitle,
  input,
  setInput,
  sending,
  queueAllowed,
  permission,
  onSubmit,
  onQueue,
  onSteerNow,
  onStop,
  onResumeQueue,
  onCancelQueue,
  onEditQueuedTurn,
  onRemoveQueuedTurn,
  onSteerQueuedTurn,
  onSwitchMode,
  planMode = false,
  onPlanModeChange,
  commands,
  actions,
  onAction,
  disabledReason,
  attachments,
  onAttachFiles,
  onRemoveAttachment,
  contextUsage,
  compaction,
  onCancelCompaction,
  providerLabel,
  model,
  providerReasoningEndpoint,
  providerReasoningLevel,
  onProviderReasoningActivated,
  activeGoal,
  onGoalChanged,
  goalEndpoint,
  resourceImageEndpoint,
  resourceChatId,
  resourceProjectPath,
  projects,
  onBindProject,
  conversation,
  attachmentPayloads,
  queued,
  agentQuestions,
  backgroundGoalDeliveries,
  backgroundGoalProviderWarnings,
  onBackgroundGoalCatchUpRendered,
  onBackgroundGoalProviderWarningsRendered,
  onBackgroundGoalCatchUpDismiss,
  onAnswerQuestion,
  onReplyInChat,
  focusInput = false,
  onCancelReply,
  pendingReplyQuestionId = "",
  onStopQuestionContinuation,
  conversationEndRef,
  onConversationMouseUp,
  onConversationScroll,
  showScrollToBottom,
  onScrollToBottom,
  pendingApprovalForResponse,
  scopedPendingApprovals,
  approvalActions,
  latestRetryableItemId,
  latestEditableUserItemId,
  editingItemId,
  editingText,
  editSaveDisabledReason,
  editingAttachments,
  onEditItemChangeText,
  onEditItemRemoveAttachment,
  onCopyItem,
  onRetryItem,
  onEditItem,
  onEditItemSave,
  onEditItemCancel,
  onApprove,
  onReject,
  onModifyApproval,
  onImportAttachment,
  onOpenDoctor,
  runtimeRuns,
  onSaveOperationAsSkill,
  onAcceptHandoff,
  onDismissHandoff,
  onPauseHandoff,
  onResumeHandoff,
  onReplyHandoff,
  handoffBusyId,
  sessionHandoffEndpoint,
  sessionHandoffSourceChatId,
  sessionHandoffTargetChats,
  handoffSendOpen,
  onHandoffSendOpenChange,
}: {
  projectPromptTitle: string;
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  queueAllowed: boolean;
  permission?: PermissionState;
  onSubmit: (event?: FormEvent) => void;
  onQueue?: (event?: FormEvent) => void;
  onSteerNow?: (event?: FormEvent) => void;
  onStop?: () => void;
  onResumeQueue?: () => void;
  onCancelQueue?: () => void;
  onEditQueuedTurn?: (turnId: string, patch: { text?: string; attachments?: ChatAttachment[] }) => void | Promise<unknown>;
  onRemoveQueuedTurn?: (turnId: string) => void;
  onSteerQueuedTurn?: (turnId: string) => void;
  planMode?: boolean;
  onPlanModeChange?: (enabled: boolean) => void;
  onSwitchMode: (mode: PermissionState["executionMode"]) => void;
  commands: Array<{ name: string; title: string }>;
  actions: ComposerAction[];
  onAction: (action: ComposerActionId) => void | Promise<void>;
  disabledReason: string;
  attachments: ChatAttachment[];
  onAttachFiles: (files: FileList | File[] | null) => void;
  onRemoveAttachment: (id: string) => void;
  contextUsage?: ContextUsage;
  compaction?: ChatCompactionState;
  onCancelCompaction?: () => void;
  providerLabel: string;
  model: string;
  providerReasoningEndpoint?: string;
  providerReasoningLevel?: string;
  onProviderReasoningActivated?: () => void | Promise<void>;
  activeGoal: AgentGoal | null;
  onGoalChanged: (goal: AgentGoal) => void;
  goalEndpoint: string;
  resourceImageEndpoint?: string;
  resourceChatId?: string;
  resourceProjectPath?: string;
  projects: Array<{ key: string; name: string }>;
  onBindProject: (path: string) => void;
  conversation: ConversationItem[];
  attachmentPayloads?: AttachmentPayloadVault;
  queued: QueuedChatTurn[];
  agentQuestions: AgentQuestion[];
  backgroundGoalDeliveries: AgentGoalDelivery[];
  backgroundGoalProviderWarnings: AgentGoalProviderWarning[];
  onBackgroundGoalCatchUpRendered: (recaps: AgentGoalRenderedRecap[]) => void;
  onBackgroundGoalProviderWarningsRendered: (warnings: AgentGoalBackgroundAcknowledgement[]) => void;
  onBackgroundGoalCatchUpDismiss: () => void;
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
  onReplyInChat?: (questionId: string) => void;
  focusInput?: boolean;
  onCancelReply?: () => void;
  pendingReplyQuestionId?: string;
  onStopQuestionContinuation?: (question: AgentQuestion) => void | Promise<void>;
  conversationEndRef: Ref<HTMLDivElement>;
  onConversationMouseUp: () => void;
  onConversationScroll: (scrollElement: HTMLDivElement) => void;
  showScrollToBottom: boolean;
  onScrollToBottom: () => void;
  pendingApprovalForResponse: (response: AgentRuntimeResponse) => AgentApproval | null;
  approvalActions: Record<string, ApprovalActionState>;
  latestRetryableItemId: string;
  latestEditableUserItemId: string;
  editingItemId: string;
  editingText: string;
  editSaveDisabledReason?: string;
  editingAttachments: ChatAttachment[];
  onEditItemChangeText: (value: string) => void;
  onEditItemRemoveAttachment: (attachmentId: string) => void;
  onCopyItem: (item: ConversationItem) => void;
  onRetryItem: (itemId: string) => void;
  onEditItem: (itemId: string) => void;
  onEditItemSave: () => void;
  onEditItemCancel: () => void;
  scopedPendingApprovals: AgentApproval[];
  onApprove: (approvalId: string, allowFutureCategory?: boolean) => void;
  onReject: (approvalId: string) => void;
  onModifyApproval: (approval: AgentApproval, revisionReason?: string, denyReasonCode?: string) => void;
  onImportAttachment?: (attachment: ChatAttachment) => void;
  onOpenDoctor: () => void;
  runtimeRuns: AgentRuntimeRun[];
  onSaveOperationAsSkill: (summary: PathToSkillOperationSummary) => void;
  onAcceptHandoff?: (handoffId: string) => void;
  onDismissHandoff?: (handoffId: string) => void;
  onPauseHandoff?: (handoffId: string) => void;
  onResumeHandoff?: (handoffId: string) => void;
  onReplyHandoff?: (handoffId: string, text: string) => void;
  handoffBusyId?: string;
  sessionHandoffEndpoint: string;
  sessionHandoffSourceChatId: string;
  sessionHandoffTargetChats: SessionHandoffTargetChat[];
  handoffSendOpen: boolean;
  onHandoffSendOpenChange: (open: boolean) => void;
  developerOptionsEnabled?: boolean;
}) {
  const { t } = useTranslation();
  const pendingAgentQuestions = useMemo(
    () =>
      agentQuestions.map((question) => projectAgentQuestionContinuation(question, runtimeRuns)).filter(
        (question) => questionContinuationDisplay(question) !== "settled",
      ),
    [agentQuestions, runtimeRuns],
  );
  const activeQuestionContinuation = pendingAgentQuestions.find(
    (question) => questionContinuationDisplay(question) === "active",
  );
  const questionContinuationCanStop = Boolean(activeQuestionContinuation && onStopQuestionContinuation);
  const composerSending = sending || questionContinuationCanStop;
  const composerStopDisabled = Boolean(!sending && activeQuestionContinuation && (
    String(activeQuestionContinuation.runtimeContinuation?.status || "").toLowerCase() === "cancelling"
    || !questionContinuationCancelTarget(activeQuestionContinuation)
  ));
  const composerStop = !sending && activeQuestionContinuation && onStopQuestionContinuation
    ? () => void onStopQuestionContinuation(activeQuestionContinuation)
    : onStop;
  const pendingQuestionPrompts = pendingAgentQuestions.filter(
    (question) => questionContinuationDisplay(question) === "pending",
  );
  const pendingReplyQuestion = pendingQuestionPrompts.find((question) => question.questionId === pendingReplyQuestionId);
  const replyBanner = pendingReplyQuestion ? (
    <div className="mb-2 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-2.5 py-2 text-xs" data-question-reply-mode>
      <span className="min-w-0 flex-1 truncate">{t("questionCard.replyInChat")}: {pendingReplyQuestion.header || pendingReplyQuestion.question || pendingReplyQuestion.questionId}</span>
      <button type="button" className="shrink-0 rounded border border-border px-2 py-1 hover:bg-muted" onClick={onCancelReply}>{t("common.cancel")}</button>
    </div>
  ) : null;
  const steeringProjection = projectConversationSteeringMessages(mergeConversationTimelineItems(conversation.filter(
    (item) => item.type !== "user" || item.composerQueued !== true,
  )));
  const conversationItems = steeringProjection.items;
  const activeStreamingItems = conversationItems.filter((item): item is Extract<ConversationItem, { type: "streaming" }> => item.type === "streaming");
  const [activityNowMs, setActivityNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!activeStreamingItems.length) return undefined;
    const timer = window.setInterval(() => setActivityNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [activeStreamingItems.length]);
  const conversationItemKeyOccurrences = new Map<string, number>();
  const conversationRenderScope = sessionHandoffSourceChatId || "chat";
  const [goalActionBusy, setGoalActionBusy] = useState(false);
  const [goalActionError, setGoalActionError] = useState("");
  const [goalLocalTick, setGoalLocalTick] = useState(0);
  useEffect(() => {
    setGoalLocalTick(0);
  }, [activeGoal?.goalId, activeGoal?.status, activeGoal?.elapsedSeconds]);
  useEffect(() => {
    if (!activeGoal || activeGoal.status !== "active") return;
    const timer = window.setInterval(() => setGoalLocalTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [activeGoal?.goalId, activeGoal?.status]);
  const goalElapsedSeconds = (activeGoal?.elapsedSeconds ?? 0) + (activeGoal?.status === "active" ? goalLocalTick : 0);
  async function setActiveGoalStatus(status: "active" | "paused" | "cancelled") {
    if (!activeGoal || goalActionBusy) return;
    setGoalActionBusy(true);
    setGoalActionError("");
    try {
      const payload = await updateAgentGoal(goalEndpoint, activeGoal.goalId, {
        status,
        sessionId: activeGoal.sessionId,
        chatId: activeGoal.chatId,
        projectRoot: activeGoal.projectRoot,
      });
      onGoalChanged(payload.goal);
    } catch {
      setGoalActionError(t("goal.updateFailed"));
    } finally {
      setGoalActionBusy(false);
    }
  }
  const composer = (compact = false) => (
    <Composer
      input={input}
      setInput={setInput}
      sending={composerSending}
      queueAllowed={queueAllowed}
      permission={permission}
      onSubmit={onSubmit}
      onQueue={onQueue}
      onSteerNow={onSteerNow}
      onStop={composerStop}
      stopDisabled={composerStopDisabled}
      onSwitchMode={onSwitchMode}
      planMode={planMode}
      onPlanModeChange={onPlanModeChange}
      commands={commands}
      actions={actions}
      onAction={onAction}
      compact={compact}
      questionPromptVisible={pendingQuestionPrompts.length > 0 && !pendingReplyQuestion}
      disabledReason={disabledReason}
      attachments={attachments}
      onAttachFiles={onAttachFiles}
      onRemoveAttachment={onRemoveAttachment}
      contextUsage={contextUsage}
      providerLabel={providerLabel}
      model={model}
      providerReasoningEndpoint={providerReasoningEndpoint}
      providerReasoningLevel={providerReasoningLevel}
      onProviderReasoningActivated={onProviderReasoningActivated}
      goalEndpoint={goalEndpoint}
      activeGoal={activeGoal}
      projects={projects}
      onBindProject={onBindProject}
      focusInput={focusInput}
      onCancelReply={onCancelReply}
    />
  );
  const pendingQueue = queued.length ? (
    <section className="mb-3 space-y-2 rounded-xl border border-border/70 bg-muted/30 p-2.5" aria-label={t("chat.pendingQueue")} data-chat-pending-queue>
      <div className="flex items-center gap-2 px-1 text-xs font-medium text-muted-foreground">
        <span>{t("chat.pendingQueue")}</span>
        <span aria-label={String(queued.length)} className="rounded-full bg-muted px-1.5 py-0.5">{queued.length}</span>
      </div>
      {queued.map((turn) => (
        <PendingQueuedTurnCard
          key={turn.id}
          turn={turn}
          sending={sending}
          onEdit={onEditQueuedTurn}
          onRemove={onRemoveQueuedTurn}
          onSteer={onSteerQueuedTurn}
        />
      ))}
    </section>
  ) : null;
  const approvalComposer = scopedPendingApprovals.length ? (
    <ScopedPendingApprovalCard
      approvals={scopedPendingApprovals}
      actions={approvalActions}
      disabled={sending}
      onApprove={onApprove}
      onReject={onReject}
      onModifyApproval={onModifyApproval}
    />
  ) : null;
  const queueControls = queued.length && !sending ? (
    <div className="mb-3 flex items-center justify-between gap-3 rounded-xl border border-border/70 bg-muted/40 px-3 py-2 text-xs" data-chat-followup-controls>
      <div className="flex min-w-0 items-center gap-2 text-muted-foreground">
        <Pause className="h-3.5 w-3.5 shrink-0" />
        <span className="truncate">{t("chat.followupsPaused", { count: queued.length })}</span>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <Button type="button" variant="outline" className="h-7 px-2 text-xs" onClick={onResumeQueue} data-chat-resume-followups>
          <Play className="mr-1 h-3 w-3" />
          {t("chat.resumeFollowups")}
        </Button>
        <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onCancelQueue} data-chat-cancel-followups>
          {t("chat.cancelFollowups")}
        </Button>
      </div>
    </div>
  ) : null;
  const activeGoalBar = activeGoal ? (
    <div
      className="mb-1 flex min-h-11 items-center gap-2 rounded-xl border border-border/80 bg-background/95 px-3 py-2 text-sm shadow-sm"
      data-chat-active-goal
    >
      <span className="shrink-0 text-muted-foreground" aria-hidden="true">◎</span>
      <span className="shrink-0 font-medium">{t("goal.inProgress")}</span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground" title={activeGoal.title || activeGoal.summary || activeGoal.goalId}>
        {activeGoal.title || activeGoal.summary || activeGoal.goalId}
      </span>
      <span className="shrink-0 text-xs text-muted-foreground" data-chat-active-goal-elapsed>
        {t("goal.elapsed", { time: formatGoalElapsed(goalElapsedSeconds) })}
      </span>
      {goalActionError ? <span className="shrink-0 text-xs text-destructive">{goalActionError}</span> : null}
      <Button
        type="button"
        variant="ghost"
        className="h-8 w-8 shrink-0 p-0"
        disabled={goalActionBusy}
        onClick={() => void setActiveGoalStatus(activeGoal.status === "paused" || activeGoal.status === "blocked" ? "active" : "paused")}
        aria-label={activeGoal.status === "paused" || activeGoal.status === "blocked" ? t("goal.resume") : t("goal.pause")}
        title={activeGoal.status === "paused" || activeGoal.status === "blocked" ? t("goal.resume") : t("goal.pause")}
        data-chat-active-goal-toggle
      >
        {activeGoal.status === "paused" || activeGoal.status === "blocked" ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
      </Button>
      <Button
        type="button"
        variant="ghost"
        className="h-8 w-8 shrink-0 p-0"
        disabled={goalActionBusy}
        onClick={() => void setActiveGoalStatus("cancelled")}
        aria-label={t("goal.cancel")}
        title={t("goal.cancel")}
        data-chat-active-goal-cancel
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  ) : null;

  if (conversation.length === 0) {
    return (
      <div className="relative flex min-h-0 flex-1 flex-col">
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-5 md:p-8" data-empty-chat-content>
          <div className="w-full max-w-3xl">
            {projectPromptTitle ? <h1 className="mb-5 text-center text-2xl font-semibold tracking-normal">{projectPromptTitle}</h1> : null}
            {handoffSendOpen ? (
              <div className="mb-3">
                <SessionHandoffSend
                  endpoint={sessionHandoffEndpoint}
                  sourceChatId={sessionHandoffSourceChatId}
                  targetChats={sessionHandoffTargetChats}
                  open={handoffSendOpen}
                  onOpenChange={onHandoffSendOpenChange}
                />
              </div>
            ) : null}
            <div className="mb-3">
              <BackgroundGoalCatchUpCard
                deliveries={backgroundGoalDeliveries}
                providerWarnings={backgroundGoalProviderWarnings}
                onRendered={onBackgroundGoalCatchUpRendered}
                onProviderWarningsRendered={onBackgroundGoalProviderWarningsRendered}
                onDismiss={onBackgroundGoalCatchUpDismiss}
              />
            </div>
          </div>
        </div>
        <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2" data-chat-composer-dock>
          <div className="mx-auto max-w-3xl">
            {replyBanner}
            {pendingQuestionPrompts.length && !pendingReplyQuestion ? (
              <div className="mb-3">
                <AgentQuestionCard questions={pendingQuestionPrompts} onAnswerQuestion={onAnswerQuestion} onReplyInChat={onReplyInChat} />
              </div>
            ) : null}
            {pendingQueue}
            {queueControls}
            <CompactionStatus state={compaction} onCancel={onCancelCompaction} />
            {activeGoalBar}
            {pendingReplyQuestion ? composer(false) : approvalComposer || composer(false)}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="relative min-h-0 flex-1">
        <div
          className="app-scrollbar h-full overflow-auto px-4 py-6 md:px-6 md:py-8"
          data-chat-history-scroll
          onMouseUp={onConversationMouseUp}
          onScroll={(event) => onConversationScroll(event.currentTarget)}
        >
          <div className="mx-auto max-w-3xl space-y-7">
          <BackgroundGoalCatchUpCard
            deliveries={backgroundGoalDeliveries}
            providerWarnings={backgroundGoalProviderWarnings}
            onRendered={onBackgroundGoalCatchUpRendered}
            onProviderWarningsRendered={onBackgroundGoalProviderWarningsRendered}
            onDismiss={onBackgroundGoalCatchUpDismiss}
          />
          {(() => {
            let previousMessageAt: number | undefined;
            return conversationItems.map((item) => {
            const currentMessageAt = conversationItemTimestamp(item);
            const showDateSeparator = shouldShowMessageDateSeparator(previousMessageAt, currentMessageAt);
            if (currentMessageAt !== undefined) previousMessageAt = currentMessageAt;
            if (item.type === "handoff_card") {
              return (
                <div key={item.id}>
                {showDateSeparator ? <MessageDateSeparator timestamp={currentMessageAt} /> : null}
                <SessionHandoffCard
                  handoff={{
                    id: item.handoffId,
                    status: item.status || "pending_review",
                    kind: item.kind,
                    source_chat_id: item.sourceChatId || "",
                    target_chat_id: item.targetChatId || "",
                    source_revision: item.sourceRevision || 0,
                    target_revision: item.targetRevision || 0,
                    revision: 0,
                    payloadDigest: item.payloadDigest,
                    summary: typeof item.summary?.goal === "string" ? item.summary.goal : undefined,
                  }}
                  onAccept={() => onAcceptHandoff?.(item.handoffId)}
                  onDismiss={() => onDismissHandoff?.(item.handoffId)}
                  onPause={() => onPauseHandoff?.(item.handoffId)}
                  onResume={() => onResumeHandoff?.(item.handoffId)}
                  onReply={(text) => onReplyHandoff?.(item.handoffId, text)}
                  busy={handoffBusyId === item.handoffId}
                />
                </div>
              );
            }
            const approval = item.type === "agent" ? pendingApprovalForResponse(item.response) : null;
            const turnId = item.type === "agent" ? item.response.clientTurnId : item.type === "streaming" ? item.clientTurnId : "";
            const initialUser = turnId ? steeringProjection.initialUsersByTurnId.get(turnId) : undefined;
            const capturedOperation = item.type === "agent"
              ? matchPathToSkillRuntimeOperation(item.response, runtimeRuns)
              : null;
            return (
              <div key={conversationItemRenderKey(item, conversationRenderScope, conversationItemKeyOccurrences)} data-conversation-item-id={item.id}>
                {showDateSeparator ? <MessageDateSeparator timestamp={currentMessageAt} /> : null}
                <ConversationCard
                item={item}
                interleavedUserMessages={turnId ? steeringProjection.messagesByTurnId.get(turnId) : undefined}
                initialUserContent={initialUser ? (
                  <div data-conversation-item-id={initialUser.id} data-turn-initial-user>
                    <ConversationCard
                      item={initialUser}
                      attachmentPayloads={attachmentPayloads}
                      canRetry={!sending && queued.length === 0 && initialUser.id === latestRetryableItemId}
                      canEdit={initialUser.id === latestEditableUserItemId}
                      editing={editingItemId === initialUser.id}
                      editingText={editingText}
                      editSaveDisabledReason={editSaveDisabledReason || (sending || queued.length > 0 ? t("chat.cannotActionWhileRunning") : "")}
                      editingAttachments={editingAttachments}
                      onEditTextChange={onEditItemChangeText}
                      onEditAttachmentRemove={onEditItemRemoveAttachment}
                      onCopyItem={onCopyItem}
                      onRetryItem={onRetryItem}
                      onEditItem={onEditItem}
                      onEditItemSave={onEditItemSave}
                      onEditItemCancel={onEditItemCancel}
                      onImportAttachment={onImportAttachment}
                    />
                  </div>
                ) : undefined}
                chatResourceImageContext={resourceImageEndpoint && resourceChatId && resourceProjectPath ? {
                  endpoint: resourceImageEndpoint,
                  chatId: resourceChatId,
                  projectPath: resourceProjectPath,
                } : undefined}
                attachmentPayloads={attachmentPayloads}
                approval={approval}
                approvalAction={approval ? approvalActions[approval.id] : undefined}
                canRetry={!sending && queued.length === 0 && item.id === latestRetryableItemId}
                canEdit={item.id === latestEditableUserItemId}
                editing={editingItemId === item.id}
                editingText={editingText}
                editSaveDisabledReason={editSaveDisabledReason || (sending || queued.length > 0 ? t("chat.cannotActionWhileRunning") : "")}
                editingAttachments={editingAttachments}
                onEditTextChange={onEditItemChangeText}
                onEditAttachmentRemove={onEditItemRemoveAttachment}
                onCopyItem={onCopyItem}
                onRetryItem={onRetryItem}
                onEditItem={onEditItem}
                onEditItemSave={onEditItemSave}
                onEditItemCancel={onEditItemCancel}
                onApprove={onApprove}
                onReject={onReject}
                onModifyApproval={onModifyApproval}
                onImportAttachment={onImportAttachment}
                onOpenDoctor={onOpenDoctor}
                saveOperationSummary={capturedOperation?.summary}
                saveOperationTool={capturedOperation?.tool}
                onSaveOperationAsSkill={onSaveOperationAsSkill}
                />
                {item.type === "streaming" ? (
                  <StreamingPhaseStatus item={item} nowMs={activityNowMs} showElapsed={false} />
                ) : null}
              </div>
            );
            });
          })()}
            <div ref={conversationEndRef} />
          </div>
        </div>
        {showScrollToBottom ? (
          <Button
            type="button"
            variant="outline"
            className="absolute bottom-4 right-5 z-20 h-9 w-9 rounded-full bg-background/95 p-0 shadow-md backdrop-blur md:right-7"
            aria-label={t("chat.scrollToBottom")}
            title={t("chat.scrollToBottom")}
            onClick={onScrollToBottom}
            data-chat-scroll-to-bottom
          >
            <ArrowDown className="h-4 w-4" />
          </Button>
        ) : null}
      </div>
      <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2" data-chat-composer-dock>
        <div className="mx-auto max-w-3xl">
          {replyBanner}
          {pendingQuestionPrompts.length && !pendingReplyQuestion ? (
            <div className="mb-3">
            <AgentQuestionCard questions={pendingQuestionPrompts} onAnswerQuestion={onAnswerQuestion} onReplyInChat={onReplyInChat} />
            </div>
          ) : null}
          {pendingQueue}
          {queueControls}
          <CompactionStatus state={compaction} onCancel={onCancelCompaction} />
          {handoffSendOpen ? (
            <div className="mb-2">
              <SessionHandoffSend
                endpoint={sessionHandoffEndpoint}
                sourceChatId={sessionHandoffSourceChatId}
                targetChats={sessionHandoffTargetChats}
                open={handoffSendOpen}
                onOpenChange={onHandoffSendOpenChange}
              />
            </div>
          ) : null}
          {activeGoalBar}
          {pendingReplyQuestion ? composer(true) : approvalComposer || composer(true)}
        </div>
      </div>
    </div>
  );
}

function CompactionStatus({ state, onCancel }: { state?: ChatCompactionState; onCancel?: () => void }) {
  const { t } = useTranslation();
  if (!state || state.status === "idle" || state.status === "applied") {
    return null;
  }
  const active = state.status === "ready" || state.status === "compacting";
  const label =
    state.status === "prefire"
      ? t("compact.prefire")
      : state.status === "ready"
      ? t("compact.preparing")
      : state.status === "compacting"
        ? t("compact.running")
        : state.status === "failed"
          ? t("compact.failed")
          : state.status === "cancelled"
            ? t("compact.cancelled")
            : t("compact.suppressed");
  return (
    <div
      className="mb-3 flex items-center gap-2 rounded-lg border border-border bg-muted/45 px-3 py-2 text-xs text-muted-foreground"
      data-context-compaction-status={state.status}
      role={state.status === "failed" ? "alert" : "status"}
    >
      {active ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" /> : <AlertTriangle className="h-3.5 w-3.5 shrink-0" />}
      <span className="min-w-0 flex-1">{label}</span>
      {state.status === "compacting" && onCancel ? (
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={onCancel}
          data-context-compaction-cancel
        >
          <X className="h-3.5 w-3.5" />
          {t("compact.cancel")}
        </button>
      ) : null}
    </div>
  );
}
