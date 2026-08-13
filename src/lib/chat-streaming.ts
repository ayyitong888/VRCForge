import type { ChatTimelineEvent, ChatTimelineEventKind, ConversationItem } from "./chat-types";

export type AgentRuntimeDeltaEvent = {
  type: "agentRuntimeDelta";
  sessionId?: string;
  turnId?: string;
  clientTurnId?: string;
  textDelta?: string;
  phase?: string;
  timelineEvent?: unknown;
  activity?: boolean;
  done?: boolean;
};

export type AgentRuntimePhase =
  | "preparing"
  | "waiting_for_model"
  | "receiving_response"
  | "running_tool"
  | "waiting_for_approval"
  | "verifying";

const AGENT_RUNTIME_PHASES = new Set<AgentRuntimePhase>([
  "preparing",
  "waiting_for_model",
  "receiving_response",
  "running_tool",
  "waiting_for_approval",
  "verifying",
]);

export function normalizeAgentRuntimePhase(value: unknown): AgentRuntimePhase | undefined {
  const normalized = typeof value === "string" ? value.trim().toLowerCase() : "";
  return AGENT_RUNTIME_PHASES.has(normalized as AgentRuntimePhase)
    ? normalized as AgentRuntimePhase
    : undefined;
}

const AGENT_RUNTIME_TIMELINE_KINDS = new Set<ChatTimelineEventKind>([
  "phase",
  "planner",
  "tool_call",
  "tool_result",
  "file_edit",
  "command",
  "subagent",
  "assistant",
]);

const SUBAGENT_STATUSES = new Set(["created", "started", "completed", "failed"] as const);

function boundedTimelineText(value: unknown, limit: number): string | undefined {
  if (typeof value !== "string") return undefined;
  const bounded = value.trim().slice(0, limit);
  return bounded || undefined;
}

/** Accept only the server's bounded, non-CoT timeline projection. */
export function normalizeAgentRuntimeTimelineEvent(value: unknown): ChatTimelineEvent | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const source = value as Record<string, unknown>;
  const id = boundedTimelineText(source.id, 180);
  const timestamp = boundedTimelineText(source.timestamp, 80);
  const kind = boundedTimelineText(source.kind, 40) as ChatTimelineEventKind | undefined;
  const sequence = Number(source.sequence);
  if (!id || !timestamp || !kind || !AGENT_RUNTIME_TIMELINE_KINDS.has(kind)
    || !Number.isFinite(sequence) || sequence < 0) return undefined;
  const rawPayload = source.payload && typeof source.payload === "object" && !Array.isArray(source.payload)
    ? source.payload as Record<string, unknown>
    : {};
  const subagentStatus = boundedTimelineText(rawPayload.subagentStatus, 40);
  return {
    id,
    sequence,
    timestamp,
    kind,
    payload: {
      ...(boundedTimelineText(rawPayload.label, 160) ? { label: boundedTimelineText(rawPayload.label, 160) } : {}),
      ...(boundedTimelineText(rawPayload.summary, 1000) ? { summary: boundedTimelineText(rawPayload.summary, 1000) } : {}),
      ...(boundedTimelineText(rawPayload.status, 80) ? { status: boundedTimelineText(rawPayload.status, 80) } : {}),
      ...(boundedTimelineText(rawPayload.tool, 160) ? { tool: boundedTimelineText(rawPayload.tool, 160) } : {}),
      ...(boundedTimelineText(rawPayload.phase, 80) ? { phase: boundedTimelineText(rawPayload.phase, 80) } : {}),
      ...(boundedTimelineText(rawPayload.actionId, 96) ? { actionId: boundedTimelineText(rawPayload.actionId, 96) } : {}),
      ...(subagentStatus && SUBAGENT_STATUSES.has(subagentStatus as "created" | "started" | "completed" | "failed")
        ? { subagentStatus: subagentStatus as "created" | "started" | "completed" | "failed" }
        : {}),
    },
  };
}

type StreamingConversationItem = Extract<ConversationItem, { type: "streaming" }>;
type AgentConversationItem = Extract<ConversationItem, { type: "agent" }>;

export type CancelledStreamingTurnOptions = {
  sessionId?: string;
  cancelledText: string;
  elapsedSeconds: number;
  completedAt?: string;
};

const PROVIDER_RECONNECT_WINDOW_MS = 60_000;
const PROVIDER_RECONNECT_WINDOW_COUNT = 5;

/** Display-only progress for one 300 second Provider inactivity window. */
export function providerReconnectAttempt(startedAt: string | undefined, nowMs = Date.now()): number | undefined {
  const startedMs = startedAt ? Date.parse(startedAt) : Number.NaN;
  if (!Number.isFinite(startedMs)) return undefined;
  const completedWindows = Math.floor(Math.max(0, nowMs - startedMs) / PROVIDER_RECONNECT_WINDOW_MS);
  if (completedWindows < 1) return undefined;
  return Math.min(PROVIDER_RECONNECT_WINDOW_COUNT, completedWindows);
}

/**
 * Apply one bounded Runtime delta to its owning transient Agent card.
 * Provider reasoning never enters this projection. Same-id events update in
 * place; call/result occurrence merging remains the timeline presenter's job.
 */
export function applyAgentRuntimeDeltaToStreamingItem(
  item: StreamingConversationItem,
  delta: AgentRuntimeDeltaEvent,
  nowIso = new Date().toISOString(),
): StreamingConversationItem {
  const phase = normalizeAgentRuntimePhase(delta.phase);
  const timelineEvent = normalizeAgentRuntimeTimelineEvent(delta.timelineEvent);
  const timeline = [...(item.timeline || [])];
  if (timelineEvent) {
    const existing = timeline.findIndex((event) => event.id === timelineEvent.id);
    if (existing >= 0) timeline[existing] = timelineEvent;
    else timeline.push(timelineEvent);
    timeline.sort((left, right) => left.sequence - right.sequence
      || left.timestamp.localeCompare(right.timestamp)
      || left.id.localeCompare(right.id));
  }
  if (phase && item.phase !== phase) {
    const sequence = timeline.reduce((max, event) => Math.max(max, event.sequence), -1) + 1;
    timeline.push({
      id: `phase-${item.clientTurnId}-${sequence}`,
      sequence,
      timestamp: nowIso,
      kind: "phase",
      payload: { phase, label: phase.replace(/_/g, " ") },
    });
  }
  const executionEvent = timelineEvent && ["tool_call", "tool_result", "command", "file_edit", "subagent"].includes(timelineEvent.kind);
  const effectivePhase = phase || item.phase;
  const enteringProviderCall = effectivePhase === "waiting_for_model" && item.phase !== "waiting_for_model";
  const safeProviderActivity = delta.activity === true || Boolean(timelineEvent);
  const providerLastActivityAt = executionEvent
    ? undefined
    : effectivePhase === "waiting_for_model"
      ? safeProviderActivity || enteringProviderCall
        ? nowIso
        : item.providerLastActivityAt
      : phase || delta.textDelta
        ? undefined
        : item.providerLastActivityAt;
  return {
    ...item,
    text: phase === "waiting_for_model" && item.phase !== "waiting_for_model"
      ? (delta.textDelta || "")
      : `${item.text}${delta.textDelta || ""}`,
    phase: phase || (delta.textDelta ? "receiving_response" : item.phase),
    timeline,
    providerLastActivityAt,
  };
}

/**
 * Settle an aborted live turn without discarding its bounded runtime facts.
 * Partial model text is intentionally omitted: it may be incomplete planner
 * output, while the safe tool timeline remains durable and auditable.
 */
export function finalizeCancelledStreamingTurn(
  item: StreamingConversationItem,
  options: CancelledStreamingTurnOptions,
): AgentConversationItem {
  const completedAt = options.completedAt || new Date().toISOString();
  const sequence = (item.timeline || []).reduce((max, event) => Math.max(max, event.sequence), -1) + 1;
  const cancelledEvent: ChatTimelineEvent = {
    id: `assistant-cancelled-${item.clientTurnId}`,
    sequence,
    timestamp: completedAt,
    kind: "assistant",
    payload: {
      summary: options.cancelledText,
      status: "cancelled",
    },
  };
  const timeline = [...(item.timeline || []), cancelledEvent];
  const sessionId = options.sessionId || "";
  return {
    id: `cancelled-${item.clientTurnId}`,
    type: "agent",
    response: {
      ok: false,
      status: "cancelled",
      session_id: sessionId,
      sessionId,
      turn_id: item.clientTurnId,
      turnId: item.clientTurnId,
      clientTurnId: item.clientTurnId,
      observe: {},
      plan: {
        summary: options.cancelledText,
        reply: options.cancelledText,
        planner: "runtime",
        plannerLabel: item.providerLabel,
        shellNeeded: false,
        nextStep: "cancelled",
      },
      timeline,
    },
    timeline,
    elapsedSeconds: Math.max(0, options.elapsedSeconds),
    providerLabel: item.providerLabel,
    model: item.model,
    createdAt: completedAt,
  };
}

const STREAMING_DIALOGUE_FIELDS = ["reply", "summary"] as const;

export function extractReplyTextFromPartialJson(value: string): string {
  return extractDialogueTextFromPartialJson(value).text;
}

export function extractDialogueTextFromPartialJson(value: string): { field: string; text: string } {
  for (const field of STREAMING_DIALOGUE_FIELDS) {
    const text = extractJsonStringField(value, field);
    if (text) {
      return { field, text };
    }
  }
  return { field: "", text: "" };
}

function extractJsonStringField(value: string, field: string): string {
  const marker = `"${field}"`;
  let searchFrom = 0;
  let colonIndex = -1;
  while (true) {
    const markerIndex = value.indexOf(marker, searchFrom);
    if (markerIndex < 0) {
      return "";
    }
    let cursor = markerIndex + marker.length;
    while (cursor < value.length && /\s/.test(value[cursor])) {
      cursor += 1;
    }
    if (value[cursor] === ":") {
      colonIndex = cursor;
      break;
    }
    searchFrom = markerIndex + marker.length;
  }
  let quoteIndex = colonIndex + 1;
  while (quoteIndex < value.length && /\s/.test(value[quoteIndex])) {
    quoteIndex += 1;
  }
  if (value[quoteIndex] !== '"') {
    return "";
  }

  let output = "";
  let escaped = false;
  for (let index = quoteIndex + 1; index < value.length; index += 1) {
    const char = value[index];
    if (escaped) {
      output += decodeJsonStringEscape(char);
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === '"') {
      break;
    }
    output += char;
  }
  return output;
}

function decodeJsonStringEscape(char: string): string {
  switch (char) {
    case "n":
      return "\n";
    case "r":
      return "\r";
    case "t":
      return "\t";
    case '"':
      return '"';
    case "\\":
      return "\\";
    case "/":
      return "/";
    default:
      return char;
  }
}
