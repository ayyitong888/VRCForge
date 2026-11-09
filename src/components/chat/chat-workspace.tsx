import { AlertTriangle, ArrowDown, ArrowUp, HelpCircle, ListEnd, Loader2, MoreHorizontal, Paperclip, Pause, Pencil, Play, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent, type Ref } from "react";
import { useTranslation } from "react-i18next";
import { updateAgentGoal } from "../../lib/api";
import type { AgentApproval, AgentGoal, AgentGoalBackgroundAcknowledgement, AgentGoalDelivery, AgentGoalProviderWarning, AgentGoalRenderedRecap, AgentQuestion, AgentRuntimeResponse, AgentRuntimeRun, PermissionState } from "../../lib/api";
import type { RunningEnterAction } from "../../lib/app-preferences";
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
import { ChatMarkdown } from "./chat-markdown";
import { SessionHandoffCard } from "./session-handoff-card";
import { SessionHandoffSend, type SessionHandoffTargetChat } from "./session-handoff-send";
import { ScopedPendingApprovalCard } from "../approvals/scoped-pending-approval-card";
import { Button } from "../ui/button";
import { matchPathToSkillRuntimeOperation, type PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { mergeConversationTimelineItems } from "../../lib/chat-thread";
import { projectConversationSteeringMessages } from "../../lib/chat-steering-projection";
import { agentReplyPreviewText, conversationItemRenderKey, copyableAgentDialogueText, includesQuestionText } from "../../lib/conversation-utils";
import type { AttachmentPayloadVault } from "../../lib/attachment-payloads";
import { conversationRoundDateSeparators } from "../../lib/message-time";
import { projectConversationTasks } from "../../lib/chat-task-projection";
import { capturePreviewsForQuestion } from "../../lib/chat-timeline-presentation";
import { ChatPositionRail, type ChatPositionMarker } from "./chat-position-rail";
import { isExternalRuntimeStream } from "../../lib/runtime-stream-owner";
import { runtimeTurnActivityStatus } from "../../lib/runtime-run-status";

function formatGoalElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.round(totalSeconds));
  if (seconds < 60) return String(seconds) + "s";
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return String(minutes) + "m " + String(rest) + "s";
}

function questionAppearsInTurn(question: string, item: ConversationItem | undefined): boolean {
  if (!question.trim()) return false;
  const includesPrompt = (value: string) => includesQuestionText(value, question);
  if (item?.type === "agent") {
    return includesPrompt(copyableAgentDialogueText(item.response)) || includesPrompt(agentReplyPreviewText(item.response));
  }
  return item?.type === "streaming" && (includesPrompt(item.text) || Boolean(item.timeline?.some(
    (event) => event.kind === "assistant" && includesPrompt(event.payload?.summary || ""),
  )));
}

export type QueuedChatTurn = {
  id: string;
  text: string;
  attachments: ChatAttachment[];
  queueStatus?: "steering" | "queued" | "waiting_for_resources" | "delivery_unverified" | "paused" | "cancelled";
  queueTransition?: "enqueue" | "dispatch";
  composerQueued?: boolean;
};

const QUEUE_VISIBLE_ROWS = 5;

function PendingQueuedTurnCard({
  turn,
  sending,
  onEdit,
  onRemove,
  onSteer,
  onResume,
}: {
  turn: QueuedChatTurn;
  sending: boolean;
  onEdit?: (turnId: string) => void | Promise<unknown>;
  onRemove?: (turnId: string) => void;
  onSteer?: (turnId: string) => void;
  onResume?: () => void;
}) {
  const { t } = useTranslation();
  const [restoring, setRestoring] = useState(false);
  const busy = restoring || Boolean(turn.queueTransition);
  const firstAttachment = turn.attachments[0];
  const imagePreview = firstAttachment?.type.startsWith("image/") ? firstAttachment.dataUrl : undefined;
  return (
    <div className="group flex h-9 items-center gap-2 px-3" data-chat-pending-turn={turn.id}>
      <ListEnd className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" aria-hidden="true" />
      {firstAttachment ? (
        <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground" title={turn.attachments.map((attachment) => attachment.name).join(", ")} data-chat-pending-turn-attachments>
          {imagePreview ? <img src={imagePreview} alt={firstAttachment.name} className="h-6 w-6 rounded object-contain" /> : <Paperclip className="h-4 w-4" aria-hidden="true" />}
          {turn.attachments.length > 1 ? <span>{turn.attachments.length}</span> : null}
        </span>
      ) : null}
      <p className="min-w-0 flex-1 truncate text-sm" data-chat-pending-turn-preview>{turn.text || t("chat.pendingQueue")}</p>
      <div className="flex shrink-0 items-center gap-0.5 text-muted-foreground/70 group-hover:text-muted-foreground group-focus-within:text-muted-foreground">
        {sending ? (
          <Button type="button" variant="ghost" className="h-7 w-7 p-0" title={t("chat.sendQueuedNow")} aria-label={t("chat.sendQueuedNow")} disabled={busy} onClick={() => onSteer?.(turn.id)} data-chat-pending-turn-steer={turn.id}>
            <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        ) : null}
        {!sending && !turn.queueTransition && onResume ? <Button type="button" variant="ghost" className="h-7 w-7 p-0" title={t("chat.resumeFollowups")} aria-label={t("chat.resumeFollowups")} onClick={onResume} data-chat-resume-followups><Play className="h-3.5 w-3.5" aria-hidden="true" /></Button> : null}
        <Button type="button" variant="ghost" className="h-7 w-7 p-0"
          aria-label={t("chat.editQueued")} title={t("chat.editQueued")} disabled={busy}
          onClick={async () => {
            setRestoring(true);
            try {
              await onEdit?.(turn.id);
            } finally {
              setRestoring(false);
            }
          }}
          data-chat-pending-turn-edit={turn.id}
        >
          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
        <Button type="button" variant="ghost" className="h-7 w-7 p-0" aria-label={t("chat.removeQueued")} title={t("chat.removeQueued")} disabled={busy} onClick={() => onRemove?.(turn.id)} data-chat-pending-turn-remove={turn.id}>
          <X className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}

export function ChatWorkspace({
  projectPromptTitle,
  conversationPending = false,
  conversationPendingMessage,
  input,
  setInput,
  sending,
  queueAllowed,
  runningEnterAction,
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
  onRetryCompaction,
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
  runtimeSessionId,
  resourceProjectPath,
  projects,
  onBindProject,
  conversation,
  attachmentPayloads,
  queued,
  agentQuestions,
  dismissedQuestionIds,
  backgroundGoalDeliveries,
  backgroundGoalProviderWarnings,
  onBackgroundGoalCatchUpRendered,
  onBackgroundGoalProviderWarningsRendered,
  onBackgroundGoalCatchUpDismiss,
  onAnswerQuestion,
  onDismissQuestion,
  onReopenQuestion,
  reviewQuestionReveal,
  onReplyInChat,
  onOpenChatById,
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
  approvalReveal,
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
  conversationPending?: boolean;
  conversationPendingMessage?: string;
  input: string;
  setInput: (value: string) => void;
  sending: boolean;
  queueAllowed: boolean;
  runningEnterAction?: RunningEnterAction;
  permission?: PermissionState;
  onSubmit: (event?: FormEvent) => void;
  onQueue?: (event?: FormEvent) => void;
  onSteerNow?: (event?: FormEvent) => void;
  onStop?: () => void;
  onResumeQueue?: () => void;
  onCancelQueue?: () => void;
  onEditQueuedTurn?: (turnId: string) => void | Promise<unknown>;
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
  onRetryCompaction?: () => void;
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
  runtimeSessionId?: string;
  resourceProjectPath?: string;
  projects: Array<{ key: string; name: string }>;
  onBindProject: (path: string) => void;
  conversation: ConversationItem[];
  attachmentPayloads?: AttachmentPayloadVault;
  queued: QueuedChatTurn[];
  agentQuestions: AgentQuestion[];
  dismissedQuestionIds?: ReadonlySet<string>;
  backgroundGoalDeliveries: AgentGoalDelivery[];
  backgroundGoalProviderWarnings: AgentGoalProviderWarning[];
  onBackgroundGoalCatchUpRendered: (recaps: AgentGoalRenderedRecap[]) => void;
  onBackgroundGoalProviderWarningsRendered: (warnings: AgentGoalBackgroundAcknowledgement[]) => void;
  onBackgroundGoalCatchUpDismiss: () => void;
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
  onDismissQuestion: (questionId: string) => void;
  onReopenQuestion?: (questionId: string) => void;
  reviewQuestionReveal?: { questionId: string } | null;
  onReplyInChat?: (questionId: string) => void;
  /** Opens another conversation in this project scope by chat id. */
  onOpenChatById?: (chatId: string) => void;
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
  approvalReveal?: { approvalId: string } | null;
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
  const projectedAgentQuestions = useMemo(
    () =>
      agentQuestions.map((question) => projectAgentQuestionContinuation(question, runtimeRuns)),
    [agentQuestions, runtimeRuns],
  );
  const pendingAgentQuestions = projectedAgentQuestions.filter((question) => questionContinuationDisplay(question) !== "settled");
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
  const dockQuestions = pendingQuestionPrompts.filter((question) => !dismissedQuestionIds?.has(question.questionId));
  const [questionReveal, setQuestionReveal] = useState<{ questionId: string }>();
  useEffect(() => {
    if (reviewQuestionReveal) setQuestionReveal(reviewQuestionReveal);
  }, [reviewQuestionReveal]);
  const [questionCardVisible, setQuestionCardVisible] = useState(false);
  const replyBanner = pendingReplyQuestion ? (
    <div className="mb-2 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 px-2.5 py-2 text-xs" data-question-reply-mode>
      <span className="min-w-0 flex-1 truncate">{t("questionCard.replyInChat")}: {pendingReplyQuestion.header || pendingReplyQuestion.question || pendingReplyQuestion.questionId}</span>
      <button type="button" className="shrink-0 rounded border border-border px-2 py-1 hover:bg-muted" onClick={onCancelReply}>{t("common.cancel")}</button>
    </div>
  ) : null;
  const steeringProjection = projectConversationSteeringMessages(mergeConversationTimelineItems(conversation.filter(
    (item) => item.type !== "user" || !(item.composerQueued || (
      item.queuedFrom && (item.queueStatus === "queued" || item.queueStatus === "waiting_for_resources"
        || item.queueStatus === "paused" || item.queueStatus === "cancelled")
    )),
  )));
  const conversationItems = steeringProjection.items.map((item) => {
    if (item.type !== "question_answer") return item;
    if (item.question !== undefined && item.selectedOptionId !== undefined) return item;
    const receipt = projectedAgentQuestions.find((candidate) => candidate.questionId === item.questionId);
    if (!receipt) return item;
    const selectedOptionId = item.selectedOptionId ?? (item.revision !== undefined
      && receipt.questionFeedbackRevision === item.revision ? receipt.selectedOptionId : undefined);
    const question = item.question ?? receipt.question;
    return question !== item.question || selectedOptionId !== item.selectedOptionId
      ? { ...item, question, selectedOptionId }
      : item;
  });
  const questionOrigins = new Map<string, ConversationItem>();
  for (const item of conversationItems) {
    if (item.type === "agent") {
      const { clientTurnId, turnId, turn_id } = item.response;
      if (clientTurnId) questionOrigins.set(clientTurnId, item);
      const backendTurnId = turnId || turn_id;
      if (backendTurnId) questionOrigins.set(backendTurnId, item);
    } else if (item.type === "streaming") {
      questionOrigins.set(item.clientTurnId, item);
    }
  }
  const questionOrigin = (question: AgentQuestion | undefined) => {
    if (!question) return undefined;
    const clientTurnId = question.originClientTurnId || "";
    return questionOrigins.get(clientTurnId)
      || questionOrigins.get(question.originTurnId || "")
      || questionOrigins.get(clientTurnId.split(":question:", 1)[0]);
  };
  const pendingQuestionsByOrigin = new Map<ConversationItem | undefined, AgentQuestion[]>();
  for (const question of pendingQuestionPrompts) {
    const origin = questionOrigin(question);
    const questions = pendingQuestionsByOrigin.get(origin) || [];
    questions.push(question);
    pendingQuestionsByOrigin.set(origin, questions);
  }
  const renderQuestionEntry = (question: AgentQuestion, showPrompt: boolean) => (
    <div key={question.questionId} className="mt-2 space-y-2 text-sm" data-chat-question-prompt={question.questionId}>
      {question.question && showPrompt ? (
        <div data-chat-quote-content><ChatMarkdown text={question.question} /></div>
      ) : null}
      <button type="button" className="inline-flex items-center gap-1.5 rounded-full border border-border px-3 py-1.5 text-sm hover:bg-muted"
        onClick={() => {
          onReopenQuestion?.(question.questionId);
          setQuestionReveal({ questionId: question.questionId });
        }} data-chat-answer-question={question.questionId}>
        <HelpCircle className="h-4 w-4" aria-hidden="true" />
        {t("questionCard.answerQuestion")}
      </button>
    </div>
  );
  const unboundQuestionEntries = pendingQuestionsByOrigin.get(undefined)?.map((question) => renderQuestionEntry(question, true));
  const conversationRenderScope = sessionHandoffSourceChatId || "chat";
  const conversationItemKeyOccurrences = new Map<string, number>();
  const [steeringReveal, setSteeringReveal] = useState<{ ownerKey: string; sequence: number }>();
  const { navigationMarkers, steeringAnchorsByOwner } = useMemo(() => {
    const occurrences = new Map<string, number>();
    const seenTurns = new Set<string>();
    const markers: ChatPositionMarker[] = [];
    const steeringAnchorsByOwner = new Map<string, Map<string, string>>();
    const responsesByTurn = new Map<string, Extract<ConversationItem, { type: "agent" }>["response"]>();
    for (const item of conversationItems) {
      if (item.type === "agent" && item.response.clientTurnId) responsesByTurn.set(item.response.clientTurnId, item.response);
    }
    const addUser = (user: Extract<ConversationItem, { type: "user" | "question_answer" }>, key: string, reveal?: () => void) => {
      if (user.text.trim() === "<task-notification-trigger/>") return;
      const turn = user.type === "user" ? user.clientTurnId || user.id : user.id;
      if (seenTurns.has(turn)) return;
      seenTurns.add(turn);
      const preview = user.text.replace(/\s+/g, " ").trim();
      const response = user.type === "user" ? responsesByTurn.get(turn) : undefined;
      markers.push({ key, reveal, preview: preview.length > 96 ? `${preview.slice(0, 95)}…` : preview,
        getAssistantPreview: response ? () => {
          const text = agentReplyPreviewText(response).replace(/\s+/g, " ").trim();
          return text.length > 600 ? `${text.slice(0, 599)}…` : text;
        } : undefined,
      });
    };
    for (const item of conversationItems) {
      const renderKey = conversationItemRenderKey(item, conversationRenderScope, occurrences);
      if (item.type === "user" || item.type === "question_answer") {
        addUser(item, renderKey);
        continue;
      }
      const turnId = item.type === "agent" ? item.response.clientTurnId : item.type === "streaming" ? item.clientTurnId : "";
      const initialUser = turnId ? steeringProjection.initialUsersByTurnId.get(turnId) : undefined;
      if (initialUser) {
        addUser(initialUser, conversationItemRenderKey(initialUser, conversationRenderScope, occurrences));
      }
      const steers = turnId ? steeringProjection.messagesByTurnId.get(turnId) : undefined;
      if (steers?.length) {
        const anchors = new Map<string, string>();
        const reveal = () => setSteeringReveal((current) => ({ ownerKey: renderKey, sequence: (current?.sequence ?? 0) + 1 }));
        for (const message of steers) {
          const key = `${renderKey}:steer:${message.id}`;
          anchors.set(message.id, key);
          addUser(message, key, reveal);
        }
        steeringAnchorsByOwner.set(renderKey, anchors);
      }
    }
    return { navigationMarkers: markers, steeringAnchorsByOwner };
  }, [conversationItems, conversationRenderScope, steeringProjection.initialUsersByTurnId, steeringProjection.messagesByTurnId]);
  const historyScrollRef = useRef<HTMLDivElement>(null);
  const questionCapturePreviews = (question: AgentQuestion) => capturePreviewsForQuestion(question, conversationItems);
  const questionResourceImageContext = (question: AgentQuestion) => {
    const capturePreviews = questionCapturePreviews(question);
    if (!capturePreviews.length || !resourceImageEndpoint || !resourceChatId || !resourceProjectPath) return undefined;
    const ownerTurn = String(question.originClientTurnId || "").split(":question:", 1)[0] || "";
    return {
      endpoint: resourceImageEndpoint,
      chatId: resourceChatId,
      projectPath: resourceProjectPath,
      sessionId: capturePreviews[0].sessionId,
      clientTurnId: ownerTurn,
    };
  };
  const questionDock = dockQuestions.length > 0 && !pendingReplyQuestion ? (
    <AgentQuestionCard
      questions={dockQuestions}
      reveal={questionReveal}
      onAnswerQuestion={onAnswerQuestion}
      onDismissQuestion={onDismissQuestion}
      onVisibilityChange={setQuestionCardVisible}
      onReplyInChat={onReplyInChat}
      capturePreviewsForQuestion={questionCapturePreviews}
      resourceImageContextForQuestion={questionResourceImageContext}
    />
  ) : null;
  const taskProjection = projectConversationTasks(conversationItems, steeringProjection.initialUsersByTurnId, projectedAgentQuestions);
  const roundDateSeparators = conversationRoundDateSeparators(conversationItems, steeringProjection.initialUsersByTurnId, projectedAgentQuestions);
  const activeStreamingItems = conversationItems.filter((item): item is Extract<ConversationItem, { type: "streaming" }> => item.type === "streaming");
  const [activityNowMs, setActivityNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!activeStreamingItems.length) return undefined;
    const timer = window.setInterval(() => setActivityNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [activeStreamingItems.length]);
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
      runningEnterAction={runningEnterAction}
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
      questionPromptVisible={Boolean(questionDock && questionCardVisible)}
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
  const pendingQueueScrollRef = useRef<HTMLDivElement>(null);
  const previousQueueSizeRef = useRef(0);
  useEffect(() => {
    const list = pendingQueueScrollRef.current;
    if (list && queued.length > QUEUE_VISIBLE_ROWS && queued.length > previousQueueSizeRef.current) {
      list.scrollTop = list.scrollHeight;
    }
    previousQueueSizeRef.current = queued.length;
  }, [queued.length]);
  const pendingQueue = queued.length ? (
    <section className="mx-4 -mb-2 rounded-t-xl border border-b-0 border-border/70 bg-card pb-2" aria-label={t("chat.pendingQueue")} data-chat-pending-queue>
      <div
        ref={pendingQueueScrollRef}
        className="overflow-y-auto overscroll-contain focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
        style={{ maxHeight: `${QUEUE_VISIBLE_ROWS * 2.25}rem` }}
        tabIndex={queued.length > QUEUE_VISIBLE_ROWS ? 0 : undefined}
        data-chat-pending-queue-scroll
      >
        {queued.map((turn) => (
          <PendingQueuedTurnCard
            key={turn.id}
            turn={turn}
            sending={composerSending}
            onEdit={onEditQueuedTurn}
            onRemove={onRemoveQueuedTurn}
            onSteer={onSteerQueuedTurn}
            onResume={queued.length === 1 ? onResumeQueue : undefined}
          />
        ))}
      </div>
      {queued.length > 1 && !composerSending && queued.every((turn) => !turn.queueTransition) ? (
        <div className="flex justify-end gap-1 px-2 pb-1 text-muted-foreground" data-chat-followup-controls>
          <Button type="button" variant="ghost" className="h-7 w-7 p-0" title={t("chat.resumeFollowups")} aria-label={t("chat.resumeFollowups")} onClick={onResumeQueue} data-chat-resume-followups>
            <Play className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
          <Button type="button" variant="ghost" className="h-7 w-7 p-0" title={t("chat.cancelFollowups")} aria-label={t("chat.cancelFollowups")} onClick={onCancelQueue} data-chat-cancel-followups>
            <X className="h-3.5 w-3.5" aria-hidden="true" />
          </Button>
        </div>
      ) : null}
    </section>
  ) : null;
  const approvalComposer = scopedPendingApprovals.length ? (
    <ScopedPendingApprovalCard
      approvals={scopedPendingApprovals}
      actions={approvalActions}
      disabled={sending}
      reveal={approvalReveal}
      onApprove={onApprove}
      onReject={onReject}
      onModifyApproval={onModifyApproval}
    />
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
            {conversationPending ? (
              <div role="status" data-chat-transcript-loading className="mx-auto max-w-2xl space-y-4 text-sm text-muted-foreground">
                <div className="flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />{conversationPendingMessage || t("common.loading")}</div>
                <div aria-hidden="true" className="space-y-3 motion-safe:animate-pulse"><div className="h-4 w-3/4 rounded bg-muted" /><div className="h-4 w-full rounded bg-muted" /><div className="h-4 w-1/2 rounded bg-muted" /></div>
              </div>
            ) : projectPromptTitle ? <h1 className="mb-5 text-center text-2xl font-semibold tracking-normal">{projectPromptTitle}</h1> : null}
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
            {unboundQuestionEntries}
          </div>
        </div>
        <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2" data-chat-composer-dock data-chat-composer-has-queue={queued.length ? "" : undefined}>
          <div className="mx-auto max-w-3xl">
            {replyBanner}
            <CompactionStatus state={compaction} onCancel={onCancelCompaction} onRetry={onRetryCompaction} />
            {activeGoalBar}
            {questionDock}
            {pendingQueue}
            {pendingReplyQuestion ? composer(false) : approvalComposer || composer(false)}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="relative min-h-0 flex-1">
        <ChatPositionRail
          markers={navigationMarkers}
          scrollContainerRef={historyScrollRef}
          scopeKey={conversationRenderScope}
        />
        <div
          ref={historyScrollRef}
          className="app-scrollbar h-full overflow-auto pl-7 pr-4 py-6 md:pl-8 md:pr-6 md:py-8"
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
            let recentAssistantTurns: ConversationItem[] = [];
            const shownQuestionIds = new Set<string>();
            const openingMessageId = conversation.find((item) => (item.type === "user" && !item.composerQueued)
              || item.type === "agent" || item.type === "streaming" || item.type === "question_answer")?.id;
            const firstDialogueItem = conversationItems.find((item) => item.id === openingMessageId);
            return conversationItems.map((item, itemIndex) => {
            if (item.type === "user") recentAssistantTurns = [];
            if (item.type === "agent" || item.type === "streaming") recentAssistantTurns.push(item);
            const origin = item.type === "question_answer" ? questionOrigin(projectedAgentQuestions.find(
              (question) => question.questionId === item.questionId,
            )) : undefined;
            const questionAlreadyShown = item.type === "question_answer" && Boolean(item.question) && (
              shownQuestionIds.has(item.questionId)
              || pendingQuestionPrompts.some((question) => question.questionId === item.questionId)
              || questionAppearsInTurn(item.question || "", origin)
              || recentAssistantTurns.some((turn) => questionAppearsInTurn(item.question || "", turn))
            );
            const questionQuoteInitiallyOpen = item.type === "question_answer" && !shownQuestionIds.size && (
              item === firstDialogueItem
              || Boolean(firstDialogueItem && (origin ? origin === firstDialogueItem
                : recentAssistantTurns.includes(firstDialogueItem) && questionAppearsInTurn(item.question || "", firstDialogueItem)))
            );
            if (item.type === "question_answer" && item.question && item.selectedOptionId !== "skip") shownQuestionIds.add(item.questionId);
            const roundStartedAt = roundDateSeparators.get(item);
            const taskId = taskProjection.taskIdByItem.get(item);
            const previous = conversationItems[itemIndex - 1];
            const taskContinuation = previous && taskProjection.taskIdByItem.get(previous) === taskId;
            const renderKey = conversationItemRenderKey(item, conversationRenderScope, conversationItemKeyOccurrences);
            if (item.type === "handoff_card") {
              return (
                <div key={renderKey} data-chat-nav-anchor={renderKey}>
                {roundStartedAt !== undefined ? <MessageDateSeparator timestamp={roundStartedAt} /> : null}
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
            const turnSessionId = runtimeSessionId || (item.type === "agent" ? item.response.sessionId || item.response.session_id : "");
            const runtimeActive = turnId && turnSessionId ? runtimeTurnActivityStatus(runtimeRuns, turnSessionId, turnId) : undefined;
            const questionRuntime = isExternalRuntimeStream(item)
              ? projectedAgentQuestions.find((question) => question.blocking !== false
                && question.runtimeContinuation?.clientTurnId === turnId
                && (question.runtimeContinuation?.sessionId || question.sessionId) === turnSessionId)
              : undefined;
            const itemRuntimeActive = item.type === "streaming" && !isExternalRuntimeStream(item) ? undefined
              : runtimeActive ?? (questionRuntime && questionContinuationDisplay(questionRuntime) === "active");
            const questionPreparing = questionRuntime
              && (runtimeActive === undefined || questionRuntime.runtimeContinuation?.status === "queued");
            const initialUser = turnId ? steeringProjection.initialUsersByTurnId.get(turnId) : undefined;
            const capturedOperation = item.type === "agent"
              ? matchPathToSkillRuntimeOperation(item.response, runtimeRuns)
              : null;
            return (
              <div key={renderKey} data-chat-nav-anchor={renderKey} data-conversation-item-id={item.id}
                data-chat-task={taskId} data-chat-task-continuation={taskContinuation || undefined}
                style={taskContinuation ? { marginTop: "0.75rem" } : undefined}>
                {roundStartedAt !== undefined ? <MessageDateSeparator timestamp={roundStartedAt} /> : null}
                <ConversationCard
                item={item}
                runtimeActive={itemRuntimeActive}
                questionAlreadyShown={questionAlreadyShown}
                questionQuoteInitiallyOpen={questionQuoteInitiallyOpen}
                pendingQuestions={pendingQuestionsByOrigin.get(item)}
                renderQuestionEntry={renderQuestionEntry}
                onOpenChatById={onOpenChatById}
                interleavedUserMessages={turnId ? steeringProjection.messagesByTurnId.get(turnId) : undefined}
                steeringNavAnchorKeys={steeringAnchorsByOwner.get(renderKey)}
                steeringRevealSequence={steeringReveal?.ownerKey === renderKey ? steeringReveal.sequence : undefined}
                initialUserContent={initialUser ? (
                  <div data-chat-nav-anchor={conversationItemRenderKey(initialUser, conversationRenderScope, conversationItemKeyOccurrences)} data-conversation-item-id={initialUser.id} data-turn-initial-user>
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
                onOpenDoctor={onOpenDoctor}
                saveOperationSummary={capturedOperation?.summary}
                saveOperationTool={capturedOperation?.tool}
                onSaveOperationAsSkill={onSaveOperationAsSkill}
                />
                {item.type === "streaming" ? (
                  <StreamingPhaseStatus item={questionPreparing && !item.phase ? { ...item, phase: "preparing" } : item}
                    nowMs={activityNowMs} showElapsed={false} runtimeActive={itemRuntimeActive} />
                ) : null}
              </div>
            );
            });
          })()}
            {unboundQuestionEntries}
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
      <div className="shrink-0 bg-workspace/95 px-4 pb-4 pt-2 md:px-6 md:pb-5 md:pt-2" data-chat-composer-dock data-chat-composer-has-queue={queued.length ? "" : undefined}>
        <div className="mx-auto max-w-3xl">
          {replyBanner}
          <CompactionStatus state={compaction} onCancel={onCancelCompaction} onRetry={onRetryCompaction} />
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
          {questionDock}
          {pendingQueue}
          {pendingReplyQuestion ? composer(true) : approvalComposer || composer(true)}
        </div>
      </div>
    </div>
  );
}

function CompactionStatus({ state, onCancel, onRetry }: {
  state?: ChatCompactionState;
  onCancel?: () => void;
  onRetry?: () => void;
}) {
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
      {state.status === "failed" && state.trigger === "manual" && onRetry ? (
        <button type="button" className="h-7 rounded-md px-2 text-xs hover:bg-muted" onClick={onRetry}>
          {t("doctor.retry")}
        </button>
      ) : null}
    </div>
  );
}
