import type { AgentContextUsage, AgentRuntimeResponse, AgentShellResult, SubAgentTask } from "./api";
import type { AgentRuntimePhase } from "./chat-streaming";

export const SELECTED_TEXT_ATTACHMENT_NAME = "Selected text";

export type ProjectType = "general" | "unity";

export type ChatAttachment = {
  id: string;
  name: string;
  size: number;
  type: string;
  dataUrl?: string;
  text?: string;
  /**
   * "vault_file" marks a binary stored in the backend's on-disk attachment
   * vault: the message carries metadata + payloadHash only and the bytes
   * never enter a model prompt.
   */
  payloadKind?: "data_url" | "text" | "metadata" | "vault_file";
  /** Stable local reference to the payload held in the owning chat's vault. */
  payloadHash?: string;
  /** Vault reference retained alongside a verified inline image payload. */
  vaultPayloadHash?: string;
  /** Backend vault format kind (zip / unitypackage / png / ...). */
  vaultKind?: string;
  truncated?: boolean;
  error?: string;
};

/**
 * Payloads are persisted once per chat, while individual message attachments
 * retain only their metadata plus payloadHash.  This keeps transcript history
 * useful without duplicating an attachment body into every model request.
 */
export type ChatAttachmentPayload = {
  payloadHash: string;
  payloadKind: "data_url" | "text";
  text?: string;
  dataUrl?: string;
};

/**
 * A body-free attachment reference retained after its original conversation
 * turn was replaced by context compaction.  The payload remains solely in the
 * owning chat's content-addressed vault.
 */
export type CompactedAttachmentReference = {
  id: string;
  name: string;
  size: number;
  type: string;
  payloadKind: "data_url" | "text" | "vault_file";
  payloadHash: string;
  vaultPayloadHash?: string;
  vaultKind?: string;
  truncated?: boolean;
};

export type ComposerActionId = "attach" | "screenshot" | "annotation" | "browser" | "desktop";

export type ComposerAction = {
  id: ComposerActionId;
  label: string;
  description: string;
  disabled?: boolean;
  disabledReason?: string;
};

export type ComposerSlashCommand = {
  name: string;
  title: string;
  action?: ComposerAction;
};

export type ContextUsage = {
  used: number;
  limit: number;
  limitKnown: boolean;
  source: "provider_usage" | "unavailable";
  exact: boolean;
  cached?: boolean;
  inputTokenSource?: "peak" | "last" | "legacy" | "legacy_total";
  peakInputTokens?: number;
  lastInputTokens?: number;
  cumulativeInputTokens?: number;
  ratio: number;
  label: string;
  title: string;
  warning: boolean;
};

export type ChatCompactionState = {
  generation: string;
  status: "idle" | "prefire" | "ready" | "compacting" | "applied" | "failed" | "suppressed" | "cancelled";
  trigger?: "manual" | "auto";
  phase?: "standalone" | "pre_turn" | "mid_turn";
  sourceDigest?: string;
  summaryDigest?: string;
  beforeTokens?: number;
  afterTokens?: number;
  contextLimit?: number;
  minimumReductionTokens?: number;
  targetAfterTokens?: number;
  provider?: string;
  model?: string;
  entryCount?: number;
  retainedEntryCount?: number;
  fidelity?: "full" | "fitted" | "fallback";
  attempts?: number;
  prefireOutcome?: "hit" | "waste";
  latencyMs?: number;
  retainedSummaryCharacters?: number;
  suppressionReason?: string;
  startedAt?: string;
  completedAt?: string;
  failureClass?: string;
  message?: string;
};

/** Durable, non-CoT projection of one runtime occurrence in a turn. */
export type ChatTimelineEventKind =
  | "phase"
  | "planner"
  | "tool_call"
  | "tool_result"
  | "file_edit"
  | "command"
  | "subagent"
  | "assistant";

export type ChatTimelineEvent = {
  id: string;
  sequence: number;
  timestamp: string;
  kind: ChatTimelineEventKind;
  /** Safe display projection only; never raw prompt/CoT/credentials/arguments. */
  payload: {
    label?: string;
    summary?: string;
    status?: string;
    tool?: string;
    phase?: string;
    actionId?: string;
    subagentStatus?: "created" | "started" | "completed" | "failed";
  };
};

export type ConversationItem =
  | { id: string; type: "user"; text: string; attachments?: ChatAttachment[]; queuedFrom?: boolean; queueStatus?: "steering" | "queued" | "waiting_for_resources" | "delivery_unverified" | "paused" | "cancelled"; clientTurnId?: string; queueEnvelope?: { provider?: string; providerLabel?: string; model?: string; contextLimit?: number; projectPath?: string; projectType?: ProjectType; sessionId?: string; laneId?: string; computerUseRequested?: boolean; computerUseVisualTheme?: "light" | "dark"; computerUseVisualAccent?: string; queueId?: string; sequence?: number }; createdAt?: string }
  | { id: string; type: "streaming"; clientTurnId: string; text: string; phase?: AgentRuntimePhase; timeline?: ChatTimelineEvent[]; providerLastActivityAt?: string; providerLabel?: string; model?: string; createdAt?: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse; timeline?: ChatTimelineEvent[]; elapsedSeconds?: number; providerLabel?: string; model?: string; createdAt?: string }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string; createdAt?: string }
  | { id: string; type: "timeline_event"; event: ChatTimelineEvent; createdAt?: string }
  | {
      id: string;
      type: "approval_revision";
      approvalId: string;
      targetTool: string;
      requestedAt: string;
      reason: string;
      note: string;
      denyReasonCode?: string;
      status: "retrying";
      createdAt?: string;
    }
  | { id: string; type: "error"; text: string; createdAt?: string }
  | { id: string; type: "compact"; text: string; detail?: string; status?: "running" | "completed"; entryCount?: number; beforeTokens?: number; afterTokens?: number; contextLimit?: number; createdAt?: string }
  | { id: string; type: "subagent"; task: SubAgentTask }
  | { id: string; type: "handoff_card"; cardId: string; handoffId: string; kind: string; payloadDigest: string; sourceChatId?: string; targetChatId?: string; sourceRevision?: number; targetRevision?: number; summary?: Record<string, unknown>; status?: string };

export type ChatThread = {
  id: string;
  sessionId: string;
  title: string;
  projectPath: string;
  projectType?: "general" | "unity";
  createdAt?: string;
  updatedAt?: string;
  agentName?: string;
  pinned?: boolean;
  archived?: boolean;
  revision?: number;
  compaction?: ChatCompactionState;
  contextUsageCache?: AgentContextUsage;
  attachmentPayloads?: Record<string, ChatAttachmentPayload>;
  compactedAttachmentRefs?: CompactedAttachmentReference[];
  items: ConversationItem[];
};

export type ApprovalActionState = "approve" | "reject" | "modify";
