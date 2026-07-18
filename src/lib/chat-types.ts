import type { AgentContextUsage, AgentRuntimeResponse, AgentShellResult, SubAgentTask } from "./api";

export const SELECTED_TEXT_ATTACHMENT_NAME = "Selected text";

export type ChatAttachment = {
  id: string;
  name: string;
  size: number;
  type: string;
  dataUrl?: string;
  text?: string;
  payloadKind?: "data_url" | "text" | "metadata";
  truncated?: boolean;
  error?: string;
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

export type ConversationItem =
  | { id: string; type: "user"; text: string; attachments?: ChatAttachment[]; queuedFrom?: boolean; createdAt?: string }
  | { id: string; type: "streaming"; clientTurnId: string; text: string; providerLabel?: string; model?: string; createdAt?: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse; elapsedSeconds?: number; providerLabel?: string; model?: string; createdAt?: string }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string; createdAt?: string }
  | { id: string; type: "error"; text: string; createdAt?: string }
  | { id: string; type: "compact"; text: string; detail?: string; status?: "running" | "completed"; entryCount?: number; beforeTokens?: number; afterTokens?: number; contextLimit?: number; createdAt?: string }
  | { id: string; type: "subagent"; task: SubAgentTask };

export type ChatThread = {
  id: string;
  sessionId: string;
  title: string;
  projectPath: string;
  createdAt?: string;
  updatedAt?: string;
  agentName?: string;
  pinned?: boolean;
  archived?: boolean;
  revision?: number;
  compaction?: ChatCompactionState;
  contextUsageCache?: AgentContextUsage;
  items: ConversationItem[];
};

export type ApprovalActionState = "approve" | "reject" | "modify";
export type MessageFeedback = "up" | "down";
