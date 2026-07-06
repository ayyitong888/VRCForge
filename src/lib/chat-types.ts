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
  ratio: number;
  label: string;
  title: string;
  warning: boolean;
};
