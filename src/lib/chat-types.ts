import type { AgentRuntimeResponse, AgentShellResult, SubAgentTask } from "./api";

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
  ratio: number;
  label: string;
  title: string;
  warning: boolean;
};

export type ConversationItem =
  | { id: string; type: "user"; text: string; attachments?: ChatAttachment[]; queuedFrom?: boolean }
  | { id: string; type: "streaming"; clientTurnId: string; text: string; providerLabel?: string; model?: string }
  | { id: string; type: "agent"; response: AgentRuntimeResponse; elapsedSeconds?: number; providerLabel?: string; model?: string }
  | { id: string; type: "result"; approvalId: string; result?: AgentShellResult; error?: string }
  | { id: string; type: "error"; text: string }
  | { id: string; type: "compact"; text: string; detail?: string; status?: "running" | "completed"; entryCount?: number; createdAt?: string }
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
  items: ConversationItem[];
};

export type ApprovalActionState = "approve" | "reject" | "modify";
export type MessageFeedback = "up" | "down";
