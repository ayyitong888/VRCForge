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
import {
  CONTEXT_AUTO_COMPACT_RATIO,
  normalizeContextProvider,
  resolveContextInputTokens,
  resolveContextLimit,
  mergeCompactionRecovery,
  latestContextWindowUsage,
} from "./context-compaction";
import { subAgentAdoptedHistoryText } from "./subagent-merge";
import { formatCount } from "./utils";
import type { CompactionRecoveryEntry } from "./api/types";
import { buildTimelinePresentation, materializeRuntimeTimeline, isFinalAssistantPresentationEntry, timelineInvocationDisplayLabel, isCancellationMarkerAssistantText } from "./chat-timeline-presentation";

const CONTEXT_TOKEN_LIMIT_DISPLAY_ESTIMATE = 128000;
export const MAX_ATTACHMENT_PAYLOAD_BYTES = 4 * 1024 * 1024;
const MAX_TEXT_ATTACHMENT_BYTES = 512 * 1024;

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

/**
 * Build a React key for a conversation item without changing its persisted id.
 * Runtime identities keep their key when other items are inserted; duplicate
 * legacy ids receive an occurrence suffix only within the current chat scope.
 */
export function conversationItemRenderKey(
  item: ConversationItem,
  scope: string,
  occurrences: Map<string, number>,
): string {
  const identity = item.type === "agent"
    ? item.response.clientTurnId || item.response.turnId || item.response.turn_id
    : item.type === "streaming"
      ? item.clientTurnId
      : item.type === "user"
        ? item.clientTurnId
        : item.type === "timeline_event"
          ? item.event.clientTurnId || item.event.turnId || item.event.id
          : item.type === "result" || item.type === "approval_revision"
            ? item.approvalId
            : item.id;
  const base = `${scope}:${item.type}:${identity || item.id}`;
  const occurrence = occurrences.get(base) || 0;
  occurrences.set(base, occurrence + 1);
  return occurrence === 0 ? base : `${base}#${occurrence + 1}`;
}

export function normalizeProviderForContext(provider: string): string {
  return normalizeContextProvider(provider);
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
  userContextWindow?: number,
): ContextUsage | undefined {
  const contextLimit = resolveContextLimit(provider, model, modelInfo, userContextWindow);
  const capacityOnly = () => ({
    used: 0,
    limit: contextLimit.limit,
    limitKnown: true as const,
    source: "unavailable" as const,
    exact: false,
    ratio: 0,
    label: t("chat.contextMeterCapacityOnly", { limit: formatCount(contextLimit.limit) }),
    title: t("chat.contextUsageUnavailableTitle", { model: model || provider || t("chat.currentModel") }),
    warning: false,
    cacheUsageStatus: "unknown" as const,
  });
  if (!usage) {
    if (!contextLimit.known) {
      return undefined;
    }
    return capacityOnly();
  }
  if ((usage.model && normalizeModelId(usage.model) !== normalizeModelId(model))
      || (usage.provider && normalizeContextProvider(usage.provider) !== normalizeContextProvider(provider))) {
    return contextLimit.known ? capacityOnly() : undefined;
  }
  // Cache counters are cumulative across requests; context occupancy uses the peak.
  const cacheInput = numberOrNull(usage.inputTokens);
  const cacheRead = numberOrNull(usage.cacheReadTokens);
  const cacheRequests = numberOrNull(usage.cacheUsageRequestCount);
  const requests = numberOrNull(usage.requestCount);
  const cacheComplete = usage.exact === true && usage.cacheUsageComplete === true
    && requests !== null && requests > 0 && Number.isInteger(requests) && cacheRequests === requests
    && cacheInput !== null && cacheInput > 0 && cacheRead !== null && cacheRead <= cacheInput;
  const cacheUsage: Pick<ContextUsage, "cacheHitRatio" | "cacheUsageStatus"> = {
    cacheHitRatio: cacheComplete ? cacheRead / cacheInput : undefined,
    cacheUsageStatus: cacheComplete ? "complete"
      : usage.cacheUsageComplete !== undefined || cacheRead !== null ? "incomplete" : "unknown",
  };
  const limit = contextLimit.known ? contextLimit.limit : CONTEXT_TOKEN_LIMIT_DISPLAY_ESTIMATE;
  const resolvedInput = resolveContextInputTokens(usage);
  const legacyTotal = numberOrNull(usage.totalTokens);
  const used = resolvedInput?.tokens ?? legacyTotal;
  const inputTokenSource = resolvedInput?.source ?? (legacyTotal !== null ? "legacy_total" : undefined);
  if (!usage.exact || used === null) {
    return {
      ...cacheUsage,
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
    ...cacheUsage,
    used,
    limit,
    limitKnown: contextLimit.known,
    source: "provider_usage",
    exact: true,
    inputTokenSource,
    peakInputTokens: numberOrNull(usage.peakInputTokens) ?? undefined,
    lastInputTokens: numberOrNull(usage.lastInputTokens) ?? undefined,
    cumulativeInputTokens: numberOrNull(usage.cumulativeInputTokens) ?? numberOrNull(usage.inputTokens) ?? undefined,
    ratio,
    label: contextLimit.known ? `${formatCount(used)} / ${limitLabel} (${percent}%)` : `${formatCount(used)} / ${limitLabel}`,
    title: t("chat.contextUsageActualTitle", {
      input: formatCount(used),
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
  return latestContextWindowUsage(items);
}

export function buildChatHistory(items: ConversationItem[], t: TFunction): ChatHistoryEntry[] {
  const history: ChatHistoryEntry[] = [];
  const summarizedItemIds = new Set(items.flatMap((item) => (
    item.type === "compact" ? item.summarizedItemIds || [] : []
  )));
  for (const item of items) {
    if (summarizedItemIds.has(item.id)) {
      continue;
    }
    if (item.type === "user") {
      const text = appendAttachmentSummary(item.text, item.attachments || [], t).trim();
      if (text) {
        history.push({ role: "user", text });
      }
    } else if (item.type === "question_answer") {
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

/** Recovery is sent through its dedicated request field, never ordinary history. */
export function collectCompactionRecovery(items: ConversationItem[]): CompactionRecoveryEntry[] {
  return mergeCompactionRecovery(...items.map((item) => (
    (item.type === "agent" || item.type === "compact") ? item.compactionRecovery : undefined
  )));
}

export function visibleAgentDialogueText(response: AgentRuntimeResponse): string {
  const assistantEntries = buildTimelinePresentation(materializeRuntimeTimeline(response)).entries.filter(
    (entry) => entry.type === "assistant",
  );
  const finalEntry = [...assistantEntries].reverse().find(isFinalAssistantPresentationEntry);
  const hasStructuredPhases = assistantEntries.some((entry) => entry.phase === "commentary" || entry.phase === "final_answer");
  // The runtime's cancellation placeholder (a cancelled-status event or the
  // "Request cancelled." text, phased or not) is a marker, never the model's
  // final words; a real accepted final still survives cancellation.
  const placeholderFinal = finalEntry
    ? isCancellationMarkerAssistantText(finalEntry.text)
      || ["cancelled", "canceled"].includes(finalEntry.status.toLowerCase())
    : false;
  if (finalEntry && !placeholderFinal && (finalEntry.phase === "final_answer" || hasStructuredPhases)) return finalEntry.text;
  // Cancellation cannot roll back completed effects: keep the marker, the
  // model's own words, and the executed work instead of a bare placeholder.
  if (isCancelledRuntimeResponse(response) || placeholderFinal) return cancelledAgentDialogueText(response);
  const hasUnacceptedFinal = assistantEntries.some((entry) => entry.status === "completion_unverified");
  // A durable unaccepted final must not reappear through the model-only fallback.
  if (hasUnacceptedFinal) return "";

  const publicMessages = response.plan?.publicMessages || [];
  const finalMessage = [...publicMessages].reverse().find((message) => message.phase === "final_answer");
  if (finalMessage) return String(finalMessage.text || "");

  // Match the visible timeline: progress without an explicit final is not an answer.
  if (assistantEntries.some((entry) => entry.phase === "commentary")
    || publicMessages.some((message) => message.phase === "commentary")) return "";
  return String(response.plan?.reply || response.plan?.summary || "");
}

export function isCancelledRuntimeResponse(response: AgentRuntimeResponse): boolean {
  return String(response?.status || "").trim().toLowerCase() === "cancelled"
    || String(response?.plan?.nextStep || "").trim().toLowerCase() === "cancelled";
}

/** Only model words may become a reply; cancellation evidence stays in history. */
export function cancelledAgentReplyText(response: AgentRuntimeResponse): string {
  const reply = String(response.plan?.reply || "").trim();
  if (!reply || isCancellationMarkerAssistantText(reply)) return "";
  const assistantEntries = buildTimelinePresentation(materializeRuntimeTimeline(response)).entries
    .filter((entry) => entry.type === "assistant");
  // A partial reply can survive Stop, but a proposed unaccepted final cannot
  // bypass the completion guard through this legacy field.
  if (assistantEntries.some((entry) => entry.status === "completion_unverified")) return "";
  if (assistantEntries.some((entry) => !["cancelled", "canceled"].includes(entry.status.toLowerCase())
    && entry.text.trim() === reply)) return "";
  return reply;
}

/**
 * Compose the durable dialogue text for a cancelled turn: the cancellation
 * marker, whatever the model had already said, and a bounded list of the work
 * that completed before cancellation. Next-turn history uses the full text;
 * this is model context, never the transcript's visible reply.
 */
export function cancelledAgentDialogueText(
  response: AgentRuntimeResponse,
  { includeSpoken = true }: { includeSpoken?: boolean } = {},
): string {
  const presentation = buildTimelinePresentation(materializeRuntimeTimeline(response));
  const assistantEntries = presentation.entries.filter((entry) => entry.type === "assistant");
  const hasUnacceptedFinal = assistantEntries.some((entry) => entry.status === "completion_unverified");
  const sections: string[] = [];
  const marker = String(response?.plan?.summary || "").trim();
  if (marker) sections.push(marker);
  // An unaccepted proposed final must never reappear through the reply field.
  const reply = String(response?.plan?.reply || "").trim();
  if (reply && !hasUnacceptedFinal && !isCancellationMarkerAssistantText(reply)) sections.push(reply);
  if (includeSpoken) {
    // The model's own words before cancellation. Native protocols tag them
    // phase="commentary"; the legacy JSON planner projects them as unphased
    // progress events. Phased finals, unaccepted finals, and cancellation
    // markers never reappear here.
    const spoken = assistantEntries
      .filter((entry) => (
        entry.phase !== "final_answer"
        && entry.status !== "completion_unverified"
        && !["cancelled", "canceled"].includes(entry.status.toLowerCase())
        && !isCancellationMarkerAssistantText(entry.text)
      ))
      .map((entry) => entry.text.trim().slice(0, 2_000))
      .filter(Boolean);
    const unseenSpoken = spoken.filter((text) => !sections.some((section) => section.includes(text)));
    if (unseenSpoken.length) sections.push(unseenSpoken.join("\n\n"));
  }
  const invocations = presentation.entries
    .flatMap((entry) => entry.type === "batch" ? entry.invocations : [])
    .map((invocation) => `- ${timelineInvocationDisplayLabel(invocation.label)} (${invocation.status})`);
  // Legacy responses persisted execution as steps instead of timeline items.
  // Use that evidence only when the timeline has no execution to avoid repeats.
  if (!invocations.length) {
    for (const step of response.steps || []) {
      if (("historical" in step && step.historical === true)
        || !(step.tool || /shell|command|write|skill|tool/.test(step.kind || ""))) continue;
      invocations.push(`- ${timelineInvocationDisplayLabel(step.tool || step.kind || "tool")} (${step.status || "unknown"})`);
    }
  }
  if (invocations.length) {
    const bounded = invocations.slice(0, 12).join("\n");
    const overflow = invocations.length > 12 ? `\n… ${invocations.length - 12} more` : "";
    sections.push(`Completed or in flight before cancellation:\n${bounded}${overflow}`);
  }
  return sections.join("\n\n");
}

export function appendAttachmentSummary(text: string, attachments: ChatAttachment[], t: TFunction): string {
  if (!attachments.length) {
    return text;
  }
  const summary = attachments
    .map((attachment) => {
      const payload =
        attachment.payloadKind === "data_url" && attachment.type.toLowerCase().startsWith("image/")
          ? t("attachments.payloadAttached")
          : attachment.payloadKind === "text" && typeof attachment.text === "string"
            ? t("attachments.textAttached")
            : attachment.payloadKind === "vault_file"
              ? t("attachments.vaultStored")
              : attachment.truncated
                ? t("attachments.metadataOnlyLarge")
                : t("attachments.metadataOnly");
      const degraded = attachment.error ? `; ${attachment.error}` : "";
      return `- ${attachment.name} (${attachment.type || t("attachments.fileTypeFallback")}, ${formatAttachmentSize(attachment.size)}, ${payload}${degraded})`;
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

export function copyableAgentDialogueText(response: AgentRuntimeResponse): string {
  if (isCancelledRuntimeResponse(response)) {
    const spoken = buildTimelinePresentation(materializeRuntimeTimeline(response)).entries
      .filter((entry) => entry.type === "assistant"
        && entry.status !== "completion_unverified"
        && !["cancelled", "canceled"].includes(entry.status.toLowerCase())
        && !isCancellationMarkerAssistantText(entry.text))
      .map((entry) => entry.type === "assistant" ? entry.text.trim() : "");
    return [...new Set([...spoken, cancelledAgentReplyText(response)].filter(Boolean))].join("\n\n");
  }
  const visibleTimelineProse = (response.timeline || [])
    .filter((event) => event.kind === "planner" || event.kind === "assistant")
    .map((event) => String(event.payload?.summary || "").trim())
    .filter((text, index, all) => Boolean(text) && all.indexOf(text) === index);
  if (visibleTimelineProse.length) {
    return visibleTimelineProse.join("\n\n");
  }
  const explicitReply = String(response.plan?.reply || "").trim();
  if (explicitReply) {
    return explicitReply;
  }
  return (response.steps || [])
    .filter((step) => /assistant|final|reply/i.test(step.kind || ""))
    .map((step) => String(step.summary || "").trim())
    .filter(Boolean)
    .join("\n\n");
}

export function conversationItemText(item: ConversationItem, _t: TFunction): string {
  if (item.type === "user") {
    return item.text;
  }
  return item.type === "agent" ? copyableAgentDialogueText(item.response) : "";
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
    payloadHash: attachment.payloadHash,
    ...(attachment.vaultPayloadHash ? { vaultPayloadHash: attachment.vaultPayloadHash } : {}),
    ...(attachment.vaultKind ? { vaultKind: attachment.vaultKind } : {}),
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
  const mimeType = file.type.split(";", 1)[0].trim().toLowerCase();
  const isText = mimeType.startsWith("text/")
    || /^application\/(?:json|[a-z0-9!#$&^_.+-]+\+json)$/.test(mimeType);
  if (isText && file.size > MAX_TEXT_ATTACHMENT_BYTES) {
    return Promise.resolve({
      ...base,
      payloadKind: "metadata",
      truncated: true,
      error: t("attachments.textLimitError", { limit: formatAttachmentSize(MAX_TEXT_ATTACHMENT_BYTES) }),
    });
  }
  if (!isText && !mimeType.startsWith("image/")) {
    return Promise.resolve({
      ...base,
      payloadKind: "metadata",
      error: t("attachments.inlineUnsupported"),
    });
  }
  if (isText) {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          if (!(reader.result instanceof ArrayBuffer)) throw new Error("Missing text bytes.");
          const text = new TextDecoder("utf-8", { fatal: true }).decode(reader.result);
          if (text.includes("\0")) throw new Error("Text contains NUL bytes.");
          resolve({ ...base, text, payloadKind: "text" });
        } catch {
          resolve({ ...base, payloadKind: "metadata", error: t("attachments.readTextFailed") });
        }
      };
      reader.onerror = () => resolve({ ...base, payloadKind: "metadata", error: t("attachments.readTextFailed") });
      reader.readAsArrayBuffer(file);
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

function normalizeModelId(model: string): string {
  const value = model.trim().toLowerCase();
  const modelsPathIndex = value.lastIndexOf("/models/");
  if (modelsPathIndex >= 0) {
    return value.slice(modelsPathIndex + "/models/".length);
  }
  return value.replace(/^models\//, "");
}

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}
