import type { TFunction } from "i18next";
import type {
  AgentContextUsage,
  AgentMessageAttachment,
  AgentRuntimeResponse,
  ChatHistoryEntry,
  ProviderModelInfo,
} from "./api";
import { formatAttachmentSize } from "./chat-format";
import type { ChatAttachment, ContextUsage, ConversationItem } from "./chat-types";
import { SELECTED_TEXT_ATTACHMENT_NAME } from "./chat-types";
import { subAgentAdoptedHistoryText } from "./subagent-merge";
import { formatCount } from "./utils";

const COMPACT_ENTRY_MAX_CHARS = 400;
const COMPACT_HEAD_ENTRIES = 2;
const COMPACT_TAIL_ENTRIES = 8;
const CONTEXT_AUTO_COMPACT_RATIO = 0.92;
const CONTEXT_TOKEN_LIMIT_ESTIMATE = 128000;
const MAX_ATTACHMENT_PAYLOAD_BYTES = 4 * 1024 * 1024;
const MAX_TEXT_ATTACHMENT_BYTES = 512 * 1024;

type ContextLimitResolution = {
  limit: number;
  known: boolean;
  source: "provider" | "known" | "estimated";
};

const KNOWN_MODEL_CONTEXT_LIMITS: Record<string, number> = {
  "deepseek:deepseek-v4-pro": 1_000_000,
  "gemini:gemini-2.5-flash": 1_048_576,
  "gemini:gemini-2.5-pro": 1_048_576,
  "gemini:gemini-2.5-flash-lite": 1_048_576,
  "gemini:gemini-3.5-flash": 1_048_576,
};

export function formatPayload(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function normalizeProviderForContext(provider: string): string {
  const key = provider.trim().toLowerCase();
  if (key.includes("gemini") || key.includes("google") || key.includes("vertex")) {
    return "gemini";
  }
  if (key.includes("anthropic") || key.includes("claude")) {
    return "anthropic";
  }
  if (key.includes("deepseek")) {
    return "deepseek";
  }
  if (key.includes("openai")) {
    return "openai";
  }
  return key || "unknown";
}

export function findProviderModelInfo(models: ProviderModelInfo[], model: string): ProviderModelInfo | undefined {
  const target = normalizeModelId(model);
  return models.find((item) => normalizeModelId(item.id) === target);
}

export function buildContextUsageFromRuntime(
  usage: AgentContextUsage | undefined,
  provider = "",
  model = "",
  modelInfo: ProviderModelInfo | undefined,
  t: TFunction,
): ContextUsage | undefined {
  if (!usage) {
    return undefined;
  }
  const contextLimit = contextLimitForProviderModel(provider, model, modelInfo);
  const limit = contextLimit.limit;
  const used = numberOrNull(usage.inputTokens) ?? numberOrNull(usage.totalTokens);
  if (!usage.exact || used === null) {
    return {
      used: 0,
      limit,
      limitKnown: contextLimit.known,
      source: "unavailable",
      exact: false,
      ratio: 0,
      label: t("chat.contextUsageUnavailable"),
      title: t("chat.contextUsageUnavailableTitle", {
        model: usage.model || model || provider || t("chat.currentModel"),
      }),
      warning: false,
    };
  }

  const ratio = contextLimit.known ? Math.min(1, used / limit) : 0;
  const percent = Math.round(ratio * 100);
  const limitLabel = contextLimit.known ? formatCount(limit) : t("chat.contextLimitUnknown");
  return {
    used,
    limit,
    limitKnown: contextLimit.known,
    source: "provider_usage",
    exact: true,
    ratio,
    label: contextLimit.known ? `${formatCount(used)} / ${limitLabel} (${percent}%)` : `${formatCount(used)} / ${limitLabel}`,
    title: t("chat.contextUsageActualTitle", {
      input: formatCount(numberOrNull(usage.inputTokens) ?? 0),
      output: formatCount(numberOrNull(usage.outputTokens) ?? 0),
      total: formatCount(numberOrNull(usage.totalTokens) ?? used),
      requests: formatCount(numberOrNull(usage.requestCount) ?? 1),
      history: formatCount(numberOrNull(usage.sentHistoryEntryCount) ?? 0),
      chars: formatCount(numberOrNull(usage.promptCharacterCount) ?? 0),
      limit: limitLabel,
      model: usage.model || model || provider || t("chat.currentModel"),
    }),
    warning: contextLimit.known && ratio >= CONTEXT_AUTO_COMPACT_RATIO,
  };
}

export function latestAgentContextUsage(items: ConversationItem[]): AgentContextUsage | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.type === "agent" && item.response.contextUsage) {
      return item.response.contextUsage;
    }
  }
  return undefined;
}

export function buildChatHistory(items: ConversationItem[], t: TFunction): ChatHistoryEntry[] {
  const history: ChatHistoryEntry[] = [];
  for (const item of items) {
    if (item.type === "user") {
      const text = appendAttachmentSummary(item.text, item.attachments || [], t).trim();
      if (text) {
        history.push({ role: "user", text });
      }
    } else if (item.type === "agent") {
      const text = visibleAgentDialogueText(item.response).trim();
      if (text) {
        history.push({ role: "agent", text });
      }
    } else if (item.type === "compact") {
      const text = (item.detail || item.text).trim();
      if (text) {
        history.push({ role: "agent", text });
      }
    } else if (item.type === "subagent") {
      const text = subAgentAdoptedHistoryText(item.task).trim();
      if (text) {
        history.push({ role: "agent", text });
      }
    }
  }
  return history;
}

export function visibleAgentDialogueText(response: AgentRuntimeResponse): string {
  return String(response.plan?.reply || response.plan?.summary || "");
}

export function appendAttachmentSummary(text: string, attachments: ChatAttachment[], t: TFunction): string {
  if (!attachments.length) {
    return text;
  }
  const summary = attachments
    .map((attachment) => {
      const payload =
        attachment.payloadKind === "data_url"
          ? t("attachments.payloadAttached")
          : attachment.payloadKind === "text"
            ? t("attachments.textAttached")
            : attachment.truncated
              ? t("attachments.metadataOnlyLarge")
              : t("attachments.metadataOnly");
      return `- ${attachment.name} (${attachment.type || t("attachments.fileTypeFallback")}, ${formatAttachmentSize(attachment.size)}, ${payload})`;
    })
    .join("\n");
  return [text.trim(), `${t("attachments.summaryHeader")}:\n${summary}`].filter(Boolean).join("\n\n");
}

export function selectedTextAttachment(text: string): ChatAttachment {
  const normalized = text.trim();
  return {
    id: `selection-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: SELECTED_TEXT_ATTACHMENT_NAME,
    size: new Blob([normalized]).size,
    type: "text/plain",
    text: normalized,
    payloadKind: "text",
  };
}

export function textContextAttachment(name: string, text: string): ChatAttachment {
  const normalized = text.trim();
  return {
    id: `context-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name,
    size: new Blob([normalized]).size,
    type: "text/plain",
    text: normalized,
    payloadKind: "text",
  };
}

export function cloneChatAttachments(attachments: ChatAttachment[]): ChatAttachment[] {
  return attachments.map((attachment) => ({
    ...attachment,
    id: `att-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  }));
}

export function conversationItemText(item: ConversationItem, t: TFunction): string {
  if (item.type === "user") {
    return appendAttachmentSummary(item.text, item.attachments || [], t);
  }
  if (item.type === "agent") {
    const parts = [
      item.response.plan?.reply || item.response.plan?.summary || "",
      item.response.write ? `Write:\n${formatPayload(item.response.write)}` : "",
      item.response.skill ? `Tool:\n${formatPayload(item.response.skill)}` : "",
      item.response.shell ? `Command:\n${formatPayload(item.response.shell)}` : "",
    ];
    return parts.filter((part) => part.trim()).join("\n\n");
  }
  if (item.type === "result") {
    return [item.result ? formatPayload(item.result) : "", item.error || ""].filter(Boolean).join("\n\n");
  }
  if (item.type === "error") {
    return item.text;
  }
  if (item.type === "compact") {
    return item.text;
  }
  if (item.type === "subagent") {
    return formatPayload(item.task);
  }
  return "";
}

export function latestConversationItemId(
  items: ConversationItem[],
  predicate: (item: ConversationItem) => boolean,
): string {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (predicate(item)) {
      return item.id;
    }
  }
  return "";
}

export function isRetryableConversationItem(item: ConversationItem): boolean {
  return item.type === "user" || item.type === "agent" || item.type === "result" || item.type === "error" || item.type === "subagent";
}

export function serializeChatAttachments(attachments: ChatAttachment[]): AgentMessageAttachment[] {
  return attachments.map((attachment) => ({
    id: attachment.id,
    name: attachment.name,
    size: attachment.size,
    type: attachment.type,
    dataUrl: attachment.dataUrl,
    text: attachment.text,
    payloadKind: attachment.payloadKind || (attachment.dataUrl ? "data_url" : attachment.text ? "text" : "metadata"),
    truncated: Boolean(attachment.truncated),
    error: attachment.error || "",
  }));
}

export function readChatAttachment(file: File, t: TFunction): Promise<ChatAttachment> {
  const base: ChatAttachment = {
    id: `att-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    name: file.name,
    size: file.size,
    type: file.type || "application/octet-stream",
  };
  if (file.size > MAX_ATTACHMENT_PAYLOAD_BYTES) {
    return Promise.resolve({
      ...base,
      payloadKind: "metadata",
      truncated: true,
      error: t("attachments.payloadLimitError", { limit: formatAttachmentSize(MAX_ATTACHMENT_PAYLOAD_BYTES) }),
    });
  }
  if (file.type.startsWith("text/") && file.size <= MAX_TEXT_ATTACHMENT_BYTES) {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve({ ...base, text: typeof reader.result === "string" ? reader.result : "", payloadKind: "text" });
      reader.onerror = () => resolve({ ...base, payloadKind: "metadata", error: t("attachments.readTextFailed") });
      reader.readAsText(file);
    });
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () =>
      resolve({
        ...base,
        dataUrl: typeof reader.result === "string" ? reader.result : undefined,
        payloadKind: typeof reader.result === "string" ? "data_url" : "metadata",
      });
    reader.onerror = () => resolve({ ...base, payloadKind: "metadata", error: t("attachments.readFileFailed") });
    reader.readAsDataURL(file);
  });
}

export function buildCompactSummary(items: ConversationItem[], t: TFunction): string {
  const entries = buildChatHistory(items, t).map(
    (entry) => `${entry.role === "user" ? t("compact.user") : t("compact.assistant")}: ${clipText(entry.text.replace(/\s+/g, " ").trim(), COMPACT_ENTRY_MAX_CHARS)}`,
  );
  let lines = entries;
  if (entries.length > COMPACT_HEAD_ENTRIES + COMPACT_TAIL_ENTRIES) {
    const omitted = entries.length - COMPACT_HEAD_ENTRIES - COMPACT_TAIL_ENTRIES;
    lines = [
      ...entries.slice(0, COMPACT_HEAD_ENTRIES),
      t("compact.omitted", { count: omitted }),
      ...entries.slice(entries.length - COMPACT_TAIL_ENTRIES),
    ];
  }
  return `${t("compact.summary", { count: entries.length })}\n${lines.join("\n")}`;
}

function clipText(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit)}\u2026` : text;
}

function normalizeModelId(model: string): string {
  const value = model.trim().toLowerCase();
  const modelsPathIndex = value.lastIndexOf("/models/");
  if (modelsPathIndex >= 0) {
    return value.slice(modelsPathIndex + "/models/".length);
  }
  return value.replace(/^models\//, "");
}

function readProviderModelContextLimit(modelInfo?: ProviderModelInfo): number | null {
  const candidates = [
    modelInfo?.inputTokenLimit,
    modelInfo?.contextWindow,
    modelInfo?.maxInputTokens,
  ];
  for (const value of candidates) {
    if (typeof value === "number" && Number.isFinite(value) && value > 0) {
      return value;
    }
  }
  return null;
}

function contextLimitForProviderModel(provider: string, model: string, modelInfo?: ProviderModelInfo): ContextLimitResolution {
  const providerLimit = readProviderModelContextLimit(modelInfo);
  if (providerLimit) {
    return { limit: providerLimit, known: true, source: "provider" };
  }
  const providerKey = normalizeProviderForContext(provider);
  const modelKey = normalizeModelId(model);
  const knownLimit = KNOWN_MODEL_CONTEXT_LIMITS[`${providerKey}:${modelKey}`];
  if (knownLimit) {
    return { limit: knownLimit, known: true, source: "known" };
  }
  return { limit: CONTEXT_TOKEN_LIMIT_ESTIMATE, known: false, source: "estimated" };
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}
