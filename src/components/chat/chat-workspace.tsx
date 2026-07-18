import { AlertTriangle, Loader2, MonitorUp, X } from "lucide-react";
import { useMemo, type FormEvent, type Ref } from "react";
import { useTranslation } from "react-i18next";
import type { AgentApproval, AgentQuestion, AgentRuntimeResponse, PermissionState } from "../../lib/api";
import type {
  ApprovalActionState,
  ChatAttachment,
  ComposerAction,
  ComposerActionId,
  ContextUsage,
  ChatCompactionState,
  ConversationItem,
  MessageFeedback,
} from "../../lib/chat-types";
import { AttachmentStrip, Composer } from "./composer";
import { AgentQuestionCard } from "./agent-question-card";
import { ConversationCard, UserImageAttachments } from "./conversation-card";

export type QueuedChatTurn = {
  id: string;
  text: string;
  attachments: ChatAttachment[];
  computerUseRequested?: boolean;
};

export function ChatWorkspace({
  projectPromptTitle,
  input,
  setInput,
  sending,
  permission,
  onSubmit,
  onStop,
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
  editing,
  onCancelEdit,
  projects,
  onBindProject,
  conversation,
  queued,
  agentQuestions,
  onAnswerQuestion,
  conversationEndRef,
  onConversationMouseUp,
  onConversationScroll,
  pendingApprovalForResponse,
  approvalActions,
  messageFeedback,
  latestRetryableItemId,
  latestEditableUserItemId,
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
  projectPromptTitle: string;
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  permission?: PermissionState;
  onSubmit: (event?: FormEvent) => void;
  onStop?: () => void;
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
  editing: boolean;
  onCancelEdit: () => void;
  projects: Array<{ key: string; name: string }>;
  onBindProject: (path: string) => void;
  conversation: ConversationItem[];
  queued: QueuedChatTurn[];
  agentQuestions: AgentQuestion[];
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
  conversationEndRef: Ref<HTMLDivElement>;
  onConversationMouseUp: () => void;
  onConversationScroll: () => void;
  pendingApprovalForResponse: (response: AgentRuntimeResponse) => AgentApproval | null;
  approvalActions: Record<string, ApprovalActionState>;
  messageFeedback: Record<string, MessageFeedback>;
  latestRetryableItemId: string;
  latestEditableUserItemId: string;
  onCopyItem: (item: ConversationItem) => void;
  onRetryItem: (itemId: string) => void;
  onEditItem: (itemId: string) => void;
  onFeedbackItem: (itemId: string, value: MessageFeedback) => void;
  onApprove: (approvalId: string) => void;
  onReject: (approvalId: string) => void;
  onModifyApproval: (approval: AgentApproval) => void;
  onOpenSettings: () => void;
  onOpenDoctor: () => void;
}) {
  const { t } = useTranslation();
  const pendingAgentQuestions = useMemo(
    () =>
      agentQuestions.filter(
        (question) => (question.status || "pending").toLowerCase() === "pending" && (question.options || []).filter((option) => option.label).length >= 2,
      ),
    [agentQuestions],
  );
  const composer = (compact = false) => (
    <Composer
      input={input}
      setInput={setInput}
      sending={sending}
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
      editing={editing}
      onCancelEdit={onCancelEdit}
      projects={projects}
      onBindProject={onBindProject}
    />
  );

  if (conversation.length === 0) {
    return (
      <div className="flex min-h-0 flex-1 items-center justify-center p-5 md:p-8">
        <div className="w-full max-w-3xl">
          {projectPromptTitle ? <h1 className="mb-5 text-center text-2xl font-semibold tracking-normal">{projectPromptTitle}</h1> : null}
          {pendingAgentQuestions.length ? (
            <div className="mb-3">
              <AgentQuestionCard questions={pendingAgentQuestions} onAnswerQuestion={onAnswerQuestion} />
            </div>
          ) : null}
          <CompactionStatus state={compaction} onCancel={onCancelCompaction} />
          {composer(false)}
        </div>
      </div>
    );
  }

  return (
    <>
      <div
        className="min-h-0 flex-1 overflow-auto px-4 py-6 md:px-6 md:py-8"
        onMouseUp={onConversationMouseUp}
        onScroll={onConversationScroll}
      >
        <div className="mx-auto max-w-3xl space-y-7">
          {conversation.map((item) => {
            const approval = item.type === "agent" ? pendingApprovalForResponse(item.response) : null;
            return (
              <ConversationCard
                key={item.id}
                item={item}
                approval={approval}
                approvalAction={approval ? approvalActions[approval.id] : undefined}
                feedback={messageFeedback[item.id]}
                canRetry={!sending && item.id === latestRetryableItemId}
                canEdit={!sending && queued.length === 0 && item.id === latestEditableUserItemId}
                onCopyItem={onCopyItem}
                onRetryItem={onRetryItem}
                onEditItem={onEditItem}
                onFeedbackItem={onFeedbackItem}
                onApprove={onApprove}
                onReject={onReject}
                onModifyApproval={onModifyApproval}
                onOpenSettings={onOpenSettings}
                onOpenDoctor={onOpenDoctor}
              />
            );
          })}
          {queued.map((turn, index) => {
            const imageAttachments = turn.attachments.filter((attachment) => attachment.dataUrl && attachment.type.startsWith("image/"));
            const otherAttachments = turn.attachments.filter((attachment) => !attachment.dataUrl || !attachment.type.startsWith("image/"));
            return (
              <div key={turn.id} className="flex justify-end opacity-65">
                <div className="flex max-w-[72%] flex-col items-end gap-2 text-sm text-foreground">
                  <div className="flex items-center gap-1 rounded-full bg-muted/70 px-2 py-1 text-[10px] text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    {t("chat.queued")} {index + 1}
                  </div>
                  {turn.computerUseRequested ? (
                    <div className="flex items-center gap-1 text-[10px] font-medium text-primary">
                      <MonitorUp className="h-3 w-3" />
                      {t("composerAction.desktop")}
                    </div>
                  ) : null}
                  {imageAttachments.length ? <UserImageAttachments attachments={imageAttachments} /> : null}
                  <p className="rounded-2xl bg-muted px-4 py-2.5 whitespace-pre-wrap break-words">{turn.text || t("attachments.fallbackTitle")}</p>
                  {otherAttachments.length ? (
                    <div className="max-w-full rounded-xl bg-muted/70 px-3 py-2">
                      <AttachmentStrip attachments={otherAttachments} compact />
                    </div>
                  ) : null}
                </div>
              </div>
            );
          })}
          <div ref={conversationEndRef} />
        </div>
      </div>
      <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2">
        <div className="mx-auto max-w-3xl">
          {pendingAgentQuestions.length ? (
            <div className="mb-3">
              <AgentQuestionCard questions={pendingAgentQuestions} onAnswerQuestion={onAnswerQuestion} />
            </div>
          ) : null}
          <CompactionStatus state={compaction} onCancel={onCancelCompaction} />
          {composer(true)}
        </div>
      </div>
    </>
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
