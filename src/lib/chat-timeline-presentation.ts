import type { AgentRuntimeResponse } from "./api/types";
import type { ChatTimelineEvent, ConversationItem } from "./chat-types";

export type TimelineBatchKind = "command" | "tool" | "file_edit" | "subagent" | "process";

export type TimelineInvocation = {
  id: string;
  actionId?: string;
  kind: TimelineBatchKind;
  label: string;
  summary: string;
  status: string;
  startedAt: string;
  completedAt: string;
};

export type TimelinePresentationEntry =
  | {
      type: "batch";
      id: string;
      kind: TimelineBatchKind;
      startedAt: string;
      invocations: TimelineInvocation[];
    }
  | {
      type: "assistant";
      id: string;
      startedAt: string;
      text: string;
      status: string;
    };

export type TimelinePresentation = {
  elapsedSeconds?: number;
  entries: TimelinePresentationEntry[];
};

/**
 * Keep full persisted tool results outside the bounded safe timeline summary.
 * Queues preserve occurrence order when a semantic action id is reused after
 * an intervening observation (for example A -> B -> A).
 */
export function buildRuntimeStepResultQueues(
  steps: AgentRuntimeResponse["steps"] = [],
): Map<string, unknown[]> {
  const queues = new Map<string, unknown[]>();
  for (const step of steps || []) {
    const actionId = String(step.actionId || "").trim();
    if (!actionId || !Object.prototype.hasOwnProperty.call(step, "result")) continue;
    queues.set(actionId, [...(queues.get(actionId) || []), step.result]);
  }
  return queues;
}

const GENERAL_AGENT_TOOL_IDS = new Set([
  "vrcforge_list_directory",
  "vrcforge_read_text_file",
  "vrcforge_find_files",
  "vrcforge_search_text",
  "vrcforge_edit_file",
  "vrcforge_write_file",
  "vrcforge_delete_path",
  "vrcforge_move_path",
  "vrcforge_apply_patch",
  "vrcforge_web_fetch",
  "vrcforge_web_search",
  "vrcforge_agent_desktop_action",
  "vrcforge_progress_list",
  "vrcforge_progress_replace",
  "vrcforge_progress_create",
  "vrcforge_progress_update",
  "vrcforge_progress_delete",
  "vrcforge_ask_user",
  "vrcforge_delegate_subagent",
  "vrcforge_classify_shell",
  "vrcforge_execute_shell",
  "vrcforge_shell_process",
  "vrcforge_inspect_chat_attachment",
  "vrcforge_vision_audit",
  "vrcforge_vision_audit_multi",
]);

export function timelineInvocationDisplayLabel(label: string): string {
  if (GENERAL_AGENT_TOOL_IDS.has(label)) return label.slice("vrcforge_".length);
  if (!label.startsWith("vrcforge_")) return label;
  const unityName = label.slice("vrcforge_".length);
  return unityName.startsWith("unity_") ? unityName : `unity_${unityName}`;
}

type RuntimePresentationTranslator = (key: string) => string;

const RUNTIME_FAILURE_MESSAGE_KEYS: Record<string, string> = {
  planner_no_progress: "chat.runtimeTerminal.noProgressMessage",
  provider_timeout: "chat.providerResponseTimedOut",
};

/** Resolve an explicit terminal label from structured Runtime state, never assistant prose. */
export function runtimeTerminalStatusKey(failureCode = ""): string | undefined {
  if (failureCode === "planner_no_progress") {
    return "chat.runtimeTerminal.noProgressStatus";
  }
  return undefined;
}

/**
 * Replace app-owned terminal prose using the structured failure code. The final
 * assistant event is the visible/copyable projection; earlier execution evidence
 * remains untouched and raw provider/model text is never used to classify it.
 */
export function projectRuntimeResponseForDisplay(
  response: AgentRuntimeResponse,
  translate: RuntimePresentationTranslator,
): AgentRuntimeResponse {
  const failureCode = String(response.plan.plannerFailure?.code || "");
  const messageKey = RUNTIME_FAILURE_MESSAGE_KEYS[failureCode];
  if (!messageKey) return response;

  const displayText = translate(messageKey);
  const timeline = response.timeline;
  let finalAssistantIndex = -1;
  if (Array.isArray(timeline)) {
    for (let index = timeline.length - 1; index >= 0; index -= 1) {
      if (timeline[index]?.kind === "assistant") {
        finalAssistantIndex = index;
        break;
      }
    }
  }

  return {
    ...response,
    plan: {
      ...response.plan,
      reply: displayText,
    },
    ...(Array.isArray(timeline) && finalAssistantIndex >= 0
      ? {
          timeline: timeline.map((event, index) => index === finalAssistantIndex
            ? {
                ...event,
                payload: {
                  ...(event.payload || {}),
                  summary: displayText,
                },
              }
            : event),
        }
      : {}),
  };
}

const TIMELINE_KINDS = new Set<ChatTimelineEvent["kind"]>([
  "phase",
  "planner",
  "tool_call",
  "tool_result",
  "file_edit",
  "command",
  "subagent",
  "assistant",
]);

const EXECUTION_KINDS = new Set<ChatTimelineEvent["kind"]>([
  "tool_call",
  "tool_result",
  "file_edit",
  "command",
  "subagent",
]);

function timestampValue(timestamp: string): number {
  const parsed = Date.parse(timestamp);
  return Number.isFinite(parsed) ? parsed : Number.MAX_SAFE_INTEGER;
}

function compareTimelineEvents(left: ChatTimelineEvent, right: ChatTimelineEvent): number {
  return timestampValue(left.timestamp) - timestampValue(right.timestamp)
    || left.sequence - right.sequence
    || left.id.localeCompare(right.id);
}

export function sortAndDedupeTimelineEvents(events: ChatTimelineEvent[] = []): ChatTimelineEvent[] {
  const byId = new Map<string, ChatTimelineEvent>();
  for (const event of events) {
    if (!event?.id || !TIMELINE_KINDS.has(event.kind)) continue;
    const previous = byId.get(event.id);
    byId.set(event.id, previous ? { ...previous, ...event, payload: { ...previous.payload, ...event.payload } } : event);
  }
  return [...byId.values()].sort(compareTimelineEvents);
}

export function hasDurableExecutionEvents(events: ChatTimelineEvent[] = []): boolean {
  return events.some((event) => EXECUTION_KINDS.has(event.kind));
}

function batchKind(event: ChatTimelineEvent): TimelineBatchKind {
  if (event.kind === "command") return "command";
  if (event.kind === "file_edit") return "file_edit";
  if (event.kind === "subagent") return "subagent";
  if (event.kind === "tool_call" || event.kind === "tool_result") return "tool";
  return "process";
}

function invocationIdentity(event: ChatTimelineEvent): string {
  const actionId = String(event.payload?.actionId || "").trim();
  if (actionId) return `${batchKind(event)}:${actionId}`;
  if (event.kind === "subagent") {
    const lifecycleMatch = event.id.match(/^(subagent-event-.+)-\d+$/);
    if (lifecycleMatch) return `subagent:${lifecycleMatch[1]}`;
  }
  return `${batchKind(event)}:${event.id}`;
}

function materializeInvocations(events: ChatTimelineEvent[]): TimelineInvocation[] {
  const invocations: Array<{ first: ChatTimelineEvent; last: ChatTimelineEvent }> = [];
  const openToolCalls = new Map<string, number[]>();
  const openSubagents = new Map<string, number>();
  for (const event of events) {
    if (event.kind === "assistant") continue;
    const identity = invocationIdentity(event);

    if (event.kind === "tool_call") {
      const index = invocations.push({ first: event, last: event }) - 1;
      openToolCalls.set(identity, [...(openToolCalls.get(identity) || []), index]);
      continue;
    }

    if (event.kind === "tool_result") {
      const pending = openToolCalls.get(identity) || [];
      const index = pending.shift();
      if (pending.length) openToolCalls.set(identity, pending);
      else openToolCalls.delete(identity);
      if (index !== undefined) invocations[index].last = event;
      else invocations.push({ first: event, last: event });
      continue;
    }

    if (event.kind === "subagent") {
      const currentIndex = openSubagents.get(identity);
      const index = currentIndex === undefined
        ? invocations.push({ first: event, last: event }) - 1
        : currentIndex;
      invocations[index].last = event;
      const lifecycle = String(event.payload?.subagentStatus || event.payload?.status || "").toLowerCase();
      if (["completed", "failed", "cancelled", "canceled"].includes(lifecycle)) openSubagents.delete(identity);
      else openSubagents.set(identity, index);
      continue;
    }

    invocations.push({ first: event, last: event });
  }
  return invocations
    .sort((left, right) => compareTimelineEvents(left.first, right.first))
    .map(({ first, last }) => {
      const firstPayload = first.payload || {};
      const lastPayload = last.payload || {};
      return {
        id: first.id,
        ...(firstPayload.actionId ? { actionId: firstPayload.actionId } : {}),
        kind: batchKind(first),
        label: firstPayload.label || firstPayload.tool || firstPayload.phase || first.kind,
        summary: lastPayload.summary || firstPayload.summary || "",
        status: lastPayload.status || firstPayload.status || last.kind,
        startedAt: first.timestamp,
        completedAt: last.timestamp,
      };
    });
}

function contiguousExecutionBatches(invocations: TimelineInvocation[]): TimelinePresentationEntry[] {
  const entries: TimelinePresentationEntry[] = [];
  for (const invocation of invocations) {
    const previous = entries.at(-1);
    if (previous?.type === "batch" && previous.kind === invocation.kind) {
      previous.invocations.push(invocation);
      continue;
    }
    entries.push({
      type: "batch",
      id: `timeline-batch-${invocation.kind}-${invocation.id}`,
      kind: invocation.kind,
      startedAt: invocation.startedAt,
      invocations: [invocation],
    });
  }
  return entries;
}

/**
 * Build a Codex-style turn projection: one elapsed marker, one final answer,
 * and expandable semantic batches whose invocation rows retain real order.
 * Planner/phase updates are safe summaries, never raw model reasoning.
 */
export function buildTimelinePresentation(events: ChatTimelineEvent[] = [], elapsedSeconds?: number): TimelinePresentation {
  const ordered = sortAndDedupeTimelineEvents(events);
  if (!ordered.length) return { entries: [] };
  const invocations = materializeInvocations(ordered);
  const entries = contiguousExecutionBatches(invocations);
  const finalAssistant = [...ordered].reverse().find((event) => event.kind === "assistant" && event.payload?.summary);
  if (finalAssistant) {
    entries.push({
      type: "assistant",
      id: finalAssistant.id,
      startedAt: finalAssistant.timestamp,
      text: finalAssistant.payload.summary || "",
      status: finalAssistant.payload.status || "done",
    });
  }
  entries.sort((left, right) => timestampValue(left.startedAt) - timestampValue(right.startedAt) || left.id.localeCompare(right.id));
  const firstTime = timestampValue(ordered[0].timestamp);
  const lastTime = timestampValue(ordered.at(-1)?.timestamp || "");
  const derivedElapsed = Number.isFinite(firstTime) && Number.isFinite(lastTime) && lastTime >= firstTime
    ? (lastTime - firstTime) / 1000
    : undefined;
  return {
    ...(invocations.length && Number.isFinite(elapsedSeconds)
      ? { elapsedSeconds }
      : invocations.length && derivedElapsed !== undefined
        ? { elapsedSeconds: derivedElapsed }
        : {}),
    entries,
  };
}

/** Convert a backend response projection into a stable, safe durable timeline. */
export function materializeRuntimeTimeline(response: AgentRuntimeResponse, fallbackTimestamp = new Date().toISOString()): ChatTimelineEvent[] {
  const source = Array.isArray(response.timeline) ? response.timeline : [];
  const projected = source.map((raw, index) => {
    const kind = TIMELINE_KINDS.has(raw.kind as ChatTimelineEvent["kind"])
      ? raw.kind as ChatTimelineEvent["kind"]
      : "assistant";
    const payload = raw.payload && typeof raw.payload === "object" ? raw.payload : {};
    return {
      id: typeof raw.id === "string" && raw.id ? raw.id : `timeline-${response.clientTurnId || response.turnId || response.turn_id || "turn"}-${index}`,
      sequence: Number.isFinite(raw.sequence) ? Number(raw.sequence) : index,
      timestamp: typeof raw.timestamp === "string" && raw.timestamp ? raw.timestamp : fallbackTimestamp,
      kind,
      payload: {
        ...(typeof payload.label === "string" ? { label: payload.label.slice(0, 160) } : {}),
        ...(typeof payload.summary === "string" ? { summary: payload.summary.slice(0, 1000) } : {}),
        ...(typeof payload.status === "string" ? { status: payload.status.slice(0, 80) } : {}),
        ...(typeof payload.tool === "string" ? { tool: payload.tool.slice(0, 160) } : {}),
        ...(typeof payload.phase === "string" ? { phase: payload.phase.slice(0, 80) } : {}),
        ...(typeof payload.actionId === "string" ? { actionId: payload.actionId.slice(0, 96) } : {}),
        ...(payload.subagentStatus ? { subagentStatus: payload.subagentStatus } : {}),
      },
    } satisfies ChatTimelineEvent;
  });
  return sortAndDedupeTimelineEvents(projected);
}

/** Merge safe live phases with the server projection by occurrence time, then resequence durably. */
export function mergeRuntimeTimelines(...sources: ChatTimelineEvent[][]): ChatTimelineEvent[] {
  return sortAndDedupeTimelineEvents(sources.flat()).map((event, sequence) => ({ ...event, sequence }));
}

function eventTime(item: Extract<ConversationItem, { type: "timeline_event" }>): number {
  return timestampValue(item.event.timestamp || item.createdAt || "");
}

/** Keep Runtime and registry facts inside their owning turn card. */
export function mergeConversationTimelineItems(items: ConversationItem[]): ConversationItem[] {
  const lifecycleItems = items
    .map((item, index) => ({ item, index }))
    .filter((entry): entry is { item: Extract<ConversationItem, { type: "timeline_event" }>; index: number } => entry.item.type === "timeline_event");
  const claimedLifecycleIds = new Set<string>();
  const lifecycleByTurnIndex = new Map<number, ChatTimelineEvent[]>();

  // Completed turns have an authoritative bounded interval. An event may be
  // claimed once only, even if corrupted/legacy turn intervals overlap.
  for (const { item: lifecycle } of lifecycleItems) {
    const timestamp = eventTime(lifecycle);
    const candidates: Array<{ index: number; width: number }> = [];
    for (const [index, item] of items.entries()) {
      if (item.type !== "agent") continue;
      const timeline = mergeRuntimeTimelines(materializeRuntimeTimeline(item.response), item.timeline || []);
      const times = timeline.map((event) => timestampValue(event.timestamp)).filter(Number.isFinite);
      if (!times.length) continue;
      const start = Math.min(...times);
      const end = Math.max(...times);
      if (timestamp >= start && timestamp <= end) candidates.push({ index, width: end - start });
    }
    const owner = candidates.sort((left, right) => left.width - right.width || left.index - right.index)[0];
    if (!owner) continue;
    lifecycleByTurnIndex.set(owner.index, [...(lifecycleByTurnIndex.get(owner.index) || []), lifecycle.event]);
    claimedLifecycleIds.add(lifecycle.event.id);
  }

  // While a turn is still active, registry events occur after its latest live
  // event. Attach each to the nearest preceding streaming turn until that turn
  // is replaced by its durable Agent card.
  for (const { item: lifecycle, index: lifecycleIndex } of lifecycleItems) {
    if (claimedLifecycleIds.has(lifecycle.event.id)) continue;
    for (let index = lifecycleIndex - 1; index >= 0; index -= 1) {
      const candidate = items[index];
      if (candidate?.type === "agent") break;
      if (candidate?.type !== "streaming") continue;
      const timeline = candidate.timeline || [];
      const start = timeline.length
        ? Math.min(...timeline.map((event) => timestampValue(event.timestamp)))
        : timestampValue(candidate.createdAt || "");
      if (eventTime(lifecycle) >= start) {
        lifecycleByTurnIndex.set(index, [...(lifecycleByTurnIndex.get(index) || []), lifecycle.event]);
        claimedLifecycleIds.add(lifecycle.event.id);
      }
      break;
    }
  }

  return items.reduce<ConversationItem[]>((merged, item, index) => {
    if (item.type === "timeline_event") {
      if (!claimedLifecycleIds.has(item.event.id)) merged.push(item);
      return merged;
    }
    const lifecycle = lifecycleByTurnIndex.get(index) || [];
    if (item.type === "streaming") {
      const timeline = mergeRuntimeTimelines(item.timeline || [], lifecycle);
      merged.push(timeline.length ? { ...item, timeline } : item);
      return merged;
    }
    if (item.type === "agent") {
      const timeline = mergeRuntimeTimelines(materializeRuntimeTimeline(item.response), item.timeline || [], lifecycle);
      merged.push(timeline.length ? { ...item, response: { ...item.response, timeline }, timeline } : item);
      return merged;
    }
    merged.push(item);
    return merged;
  }, []);
}
