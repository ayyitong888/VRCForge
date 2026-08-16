import { AlertTriangle, ArrowDown, Loader2, Pause, Play, X } from "lucide-react";
import { useMemo, useState, type FormEvent, type Ref } from "react";
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
import { BackgroundGoalCatchUpCard } from "./background-goal-catch-up-card";
import { ConversationCard } from "./conversation-card";
import { SessionHandoffCard } from "./session-handoff-card";
import { SessionHandoffSend, type SessionHandoffTargetChat } from "./session-handoff-send";
import { ScopedPendingApprovalCard } from "../approvals/scoped-pending-approval-card";
import { Button } from "../ui/button";
import { matchPathToSkillRuntimeOperation, type PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { mergeConversationTimelineItems } from "../../lib/chat-thread";

export type QueuedChatTurn = {
  id: string;
  text: string;
  attachments: ChatAttachment[];
  queueStatus?: "steering" | "queued" | "waiting_for_resources" | "delivery_unverified" | "paused" | "cancelled";
};

export function ChatWorkspace({
  projectPromptTitle,
  input,
  setInput,
  sending,
  queueAllowed,
  permission,
  onSubmit,
  onStop,
  onResumeQueue,
  onCancelQueue,
  onSwitchMode,
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
  activeGoal,
  onGoalChanged,
  goalEndpoint,
  projects,
  onBindProject,
  conversation,
  queued,
  agentQuestions,
  backgroundGoalDeliveries,
  backgroundGoalProviderWarnings,
  onBackgroundGoalCatchUpRendered,
  onBackgroundGoalProviderWarningsRendered,
  onBackgroundGoalCatchUpDismiss,
  onAnswerQuestion,
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
  onStop?: () => void;
  onResumeQueue?: () => void;
  onCancelQueue?: () => void;
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
  activeGoal: AgentGoal | null;
  onGoalChanged: (goal: AgentGoal) => void;
  goalEndpoint: string;
  projects: Array<{ key: string; name: string }>;
  onBindProject: (path: string) => void;
  conversation: ConversationItem[];
  queued: QueuedChatTurn[];
  agentQuestions: AgentQuestion[];
  backgroundGoalDeliveries: AgentGoalDelivery[];
  backgroundGoalProviderWarnings: AgentGoalProviderWarning[];
  onBackgroundGoalCatchUpRendered: (recaps: AgentGoalRenderedRecap[]) => void;
  onBackgroundGoalProviderWarningsRendered: (warnings: AgentGoalBackgroundAcknowledgement[]) => void;
  onBackgroundGoalCatchUpDismiss: () => void;
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
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
  onModifyApproval: (approval: AgentApproval) => void;
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
}) {
  const { t } = useTranslation();
  const pendingAgentQuestions = useMemo(
    () =>
      agentQuestions.filter(
        (question) => (question.status || "pending").toLowerCase() === "pending" && (question.options || []).filter((option) => option.label).length >= 2,
      ),
    [agentQuestions],
  );
  const conversationItems = mergeConversationTimelineItems(conversation);
  const [goalActionBusy, setGoalActionBusy] = useState(false);
  const [goalActionError, setGoalActionError] = useState("");
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
      sending={sending}
      queueAllowed={queueAllowed}
      permission={permission}
      onSubmit={onSubmit}
      onStop={onStop}
      onSwitchMode={onSwitchMode}
      commands={commands}
      actions={actions}
      onAction={onAction}
      compact={compact}
      disabledReason={disabledReason}
      attachments={attachments}
      onAttachFiles={onAttachFiles}
      onRemoveAttachment={onRemoveAttachment}
      contextUsage={contextUsage}
      providerLabel={providerLabel}
      model={model}
      goalEndpoint={goalEndpoint}
      activeGoal={activeGoal}
      projects={projects}
      onBindProject={onBindProject}
    />
  );
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
      {goalActionError ? <span className="shrink-0 text-xs text-destructive">{goalActionError}</span> : null}
      <Button
        type="button"
        variant="ghost"
        className="h-8 w-8 shrink-0 p-0"
        disabled={goalActionBusy}
        onClick={() => void setActiveGoalStatus(activeGoal.status === "paused" ? "active" : "paused")}
        aria-label={activeGoal.status === "paused" ? t("goal.resume") : t("goal.pause")}
        title={activeGoal.status === "paused" ? t("goal.resume") : t("goal.pause")}
        data-chat-active-goal-toggle
      >
        {activeGoal.status === "paused" ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
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
            {pendingAgentQuestions.length ? (
              <div className="mb-3">
                <AgentQuestionCard questions={pendingAgentQuestions} onAnswerQuestion={onAnswerQuestion} />
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
            {queueControls}
            <CompactionStatus state={compaction} onCancel={onCancelCompaction} />
            {!approvalComposer ? <>{activeGoalBar}{composer(false)}</> : null}
          </div>
        </div>
        {approvalComposer ? (
          <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2" data-chat-composer-dock>
            <div className="mx-auto max-w-3xl">{activeGoalBar}{approvalComposer}</div>
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="relative min-h-0 flex-1">
        <div
          className="h-full overflow-auto px-4 py-6 md:px-6 md:py-8"
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
          {conversationItems.map((item) => {
            if (item.type === "handoff_card") {
              return (
                <SessionHandoffCard
                  key={item.id}
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
              );
            }
            const approval = item.type === "agent" ? pendingApprovalForResponse(item.response) : null;
            const capturedOperation = item.type === "agent"
              ? matchPathToSkillRuntimeOperation(item.response, runtimeRuns)
              : null;
            return (
              <div key={item.id} data-conversation-item-id={item.id}>
                <ConversationCard
                item={item}
                approval={approval}
                approvalAction={approval ? approvalActions[approval.id] : undefined}
                canRetry={!sending && queued.length === 0 && item.id === latestRetryableItemId}
                canEdit={!sending && queued.length === 0 && item.id === latestEditableUserItemId}
                editing={editingItemId === item.id}
                editingText={editingText}
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
              </div>
            );
          })}
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
          {pendingAgentQuestions.length ? (
            <div className="mb-3">
              <AgentQuestionCard questions={pendingAgentQuestions} onAnswerQuestion={onAnswerQuestion} />
            </div>
          ) : null}
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
          {approvalComposer || composer(true)}
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
