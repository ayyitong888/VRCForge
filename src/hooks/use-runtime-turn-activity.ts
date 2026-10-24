import { useCallback, useEffect, useRef } from "react";
import type { AgentRuntimeResponse, AgentRuntimeRun, AgentTurnResponseRecovery } from "../lib/api/types";
import { fetchAgentRunTimeline, fetchAgentTurnResponse, type AgentRunTimelinePage } from "../lib/api/agent-runtime";
import type { ChatThread } from "../lib/chat-types";
import { applyAgentRuntimeDeltaToStreamingItem, type AgentRuntimeDeltaEvent } from "../lib/chat-streaming";
import { mergeRuntimeTimelines, materializeRuntimeTimeline } from "../lib/chat-timeline-presentation";
import { normalizeAgentRuntimeTimelineEvent } from "../lib/chat-streaming";
import { type RuntimeTurnEvent } from "./use-runtime-turn-continuation";
import { coalesceRuntimeTurnStreams, isExternalRuntimeStream, RUNTIME_ACTIVITY_STREAM_PREFIX } from "../lib/runtime-stream-owner";
import { isActiveRuntimeRunStatus } from "../lib/runtime-run-status";

type Identity = { sessionId: string; clientTurnId: string; turnId?: string };
type UpdateChat = (chatId: string, updater: (chat: ChatThread) => ChatThread) => boolean;
type Activity = Identity & { chatId: string; nextSequence: number; stamp: string; controller: AbortController; reading: boolean; caughtUp: boolean; receiptRecovered?: boolean; timer?: number };

function identityKey(identity: Identity): string {
  return JSON.stringify([identity.sessionId, identity.clientTurnId]);
}

function responseMatchesIdentity(response: AgentRuntimeResponse, identity: Identity): boolean {
  const sessionId = response.sessionId || response.session_id;
  if (sessionId && sessionId !== identity.sessionId) return false;
  const turnId = response.turnId || response.turn_id;
  // Older local transport placeholders used the CID as a synthetic backend
  // turn id. Exact CID ownership still permits their authoritative readback.
  const localPlaceholder = needsRuntimeReceipt(response)
    && (response.continuationSource === "foreground_interrupted" || turnId === response.clientTurnId);
  if (response.clientTurnId) return response.clientTurnId === identity.clientTurnId
    && (!identity.turnId || !turnId || turnId === identity.turnId || localPlaceholder);
  // An exact backend turn from this run is authoritative even on older reply
  // records that omitted their client-turn alias. Never guess from text/time.
  return Boolean(identity.turnId && turnId === identity.turnId);
}

/** A duplicated session is ambiguous unless the exact CID identifies one owner. */
export function runtimeActivityOwner(chats: ChatThread[], identity: Identity): ChatThread | undefined {
  if (!identity.sessionId || !identity.clientTurnId) return undefined;
  const candidates = chats.filter((chat) => chat.sessionId === identity.sessionId);
  if (candidates.length === 1) return candidates[0];
  const exact = candidates.filter((chat) => chat.items.some((item) =>
    ((item.type === "user" || item.type === "streaming") && (item.clientTurnId === identity.clientTurnId
      || (item.type === "user" && item.id === `user-${identity.clientTurnId}`)))
    || (item.type === "agent" && responseMatchesIdentity(item.response, identity))));
  return exact.length === 1 ? exact[0] : undefined;
}

function hasReply(chat: ChatThread, identity: Identity): boolean {
  return chat.items.some((item) => item.type === "agent" && responseMatchesIdentity(item.response, identity));
}

function needsRuntimeReceipt(response: AgentRuntimeResponse): boolean {
  if (response.continuationSource === "foreground_interrupted") return true;
  if (!["cancelled", "canceled", "failed", "error", "interrupted"].includes(String(response.status || "").toLowerCase())) return false;
  const turnId = response.turnId || response.turn_id;
  return !turnId || turnId === response.clientTurnId;
}

function hasAuthoritativeReply(chat: ChatThread, identity: Identity): boolean {
  return chat.items.some((item) => item.type === "agent"
    && responseMatchesIdentity(item.response, identity) && !needsRuntimeReceipt(item.response));
}

/** Only this observer's rows are managed here; local sends keep their own lifecycle. */
export function projectRuntimeTurnActivity(chat: ChatThread, run: AgentRuntimeRun): ChatThread {
  const sessionId = String(run.sessionId || "").trim();
  const clientTurnId = String(run.clientTurnId || "").trim();
  if (!sessionId || !clientTurnId || chat.sessionId !== sessionId || hasReply(chat, { sessionId, clientTurnId, turnId: run.turnId })) return chat;
  if (chat.items.some((item) => item.type === "streaming" && item.clientTurnId === clientTurnId)) return coalesceRuntimeTurnStreams(chat, clientTurnId);
  if (!isActiveRuntimeRunStatus(run.status)) return chat;
  return { ...chat, items: [...chat.items, {
    id: `${RUNTIME_ACTIVITY_STREAM_PREFIX}${clientTurnId}`, type: "streaming", clientTurnId,
    text: "", createdAt: run.startedAt, model: run.model, providerLabel: run.providerLabel,
  }] };
}

export function applyRuntimeActivityDelta(chat: ChatThread, delta: AgentRuntimeDeltaEvent): ChatThread {
  if (!delta.sessionId || chat.sessionId !== delta.sessionId || !delta.clientTurnId) return chat;
  chat = coalesceRuntimeTurnStreams(chat, delta.clientTurnId);
  let changed = false;
  const items = chat.items.map((item) => {
    // A terminal notification may race the first history page. Continue filling
    // that exact receipt so earlier accepted dialogue is never lost.
    if (item.type === "agent" && responseMatchesIdentity(item.response, { sessionId: delta.sessionId!, clientTurnId: delta.clientTurnId!, turnId: delta.turnId })) {
      const event = normalizeAgentRuntimeTimelineEvent(delta.timelineEvent);
      if (!event) return item;
      changed = true;
      return { ...item, timeline: mergeRuntimeTimelines(materializeRuntimeTimeline(item.response), item.timeline || [], [event]) };
    }
    if (item.type !== "streaming" || item.clientTurnId !== delta.clientTurnId
      || !isExternalRuntimeStream(item)) return item;
    changed = true;
    return applyAgentRuntimeDeltaToStreamingItem(item, delta);
  });
  return changed ? { ...chat, items } : chat;
}

export function runtimeEventFromExactResponse(state: AgentTurnResponseRecovery, identity: Identity): RuntimeTurnEvent | undefined {
  if (!state.ok || state.sessionId !== identity.sessionId || state.clientTurnId !== identity.clientTurnId
    || !["completed", "failed"].includes(state.status) || !state.response) return undefined;
  const response = state.response;
  if (!responseMatchesIdentity(response, identity)) return undefined;
  const turnId = response.turnId || response.turn_id;
  if (!turnId || (identity.turnId && turnId !== identity.turnId)) return undefined;
  return {
    schema: "vrcforge.runtime_turn_event.v1", continuationSource: "foreground_completed",
    sessionId: identity.sessionId, clientTurnId: identity.clientTurnId, turnId,
    plan: response.plan, timeline: response.timeline, contextUsage: response.contextUsage,
    consumedSteerInputIds: response.consumedSteerInputIds, model: response.model,
    completedAt: response.completedAt, startedAt: response.startedAt,
  };
}

type Params = {
  endpoint: string;
  runtimeConnected: boolean;
  chats: ChatThread[];
  runs: AgentRuntimeRun[];
  updateChat: UpdateChat;
  deliver: (event: unknown) => boolean;
  recoverySignal?: unknown;
};

/** Attach to app-owned runs started elsewhere, then fetch only missing public events. */
export function useRuntimeTurnActivity(params: Params) {
  const latest = useRef(params);
  latest.current = params;
  const activities = useRef(new Map<string, Activity>());
  const finished = useRef(new Set<string>());
  const scope = useRef({ endpoint: params.endpoint, connected: params.runtimeConnected });
  scope.current = { endpoint: params.endpoint, connected: params.runtimeConnected };

  const read = useCallback(async (activity: Activity) => {
    if (activity.reading || activity.controller.signal.aborted) return;
    activity.reading = true;
    const endpoint = latest.current.endpoint;
    const valid = () => !activity.controller.signal.aborted && scope.current.connected
      && scope.current.endpoint === endpoint && activities.current.get(identityKey(activity)) === activity
      && runtimeActivityOwner(latest.current.chats, activity)?.id === activity.chatId;
    try {
      // Yield after a bounded page batch; a long run never blocks the UI thread.
      for (let pageIndex = 0; pageIndex < 8 && valid(); pageIndex += 1) {
        const page: AgentRunTimelinePage = await fetchAgentRunTimeline(endpoint, activity.sessionId, activity.clientTurnId, activity.nextSequence, activity.controller.signal);
        if (!valid() || !page.ok || !page.run || page.run.sessionId !== activity.sessionId
          || page.run.clientTurnId !== activity.clientTurnId || (activity.turnId && page.run.turnId !== activity.turnId)) return;
        activity.turnId = page.run.turnId;
        latest.current.updateChat(activity.chatId, (chat) => {
          if (chat.sessionId !== activity.sessionId) return chat;
          return page.events.reduce((current, event) => applyRuntimeActivityDelta(current, {
            type: "agentRuntimeDelta", sessionId: activity.sessionId, clientTurnId: activity.clientTurnId,
            turnId: activity.turnId, timelineEvent: event,
          }), chat);
        });
        if (!Number.isInteger(page.nextSequence) || page.nextSequence < activity.nextSequence
          || (page.hasMore && page.nextSequence === activity.nextSequence)) return;
        activity.nextSequence = page.nextSequence;
        if (!page.hasMore) {
          activity.caughtUp = true;
          if (!isActiveRuntimeRunStatus(page.run.status)) {
            const state = await fetchAgentTurnResponse(endpoint, activity.sessionId, activity.clientTurnId, activity.controller.signal);
            if (!valid()) return;
            const event = runtimeEventFromExactResponse(state, activity);
            if (event && latest.current.deliver(event)) activity.receiptRecovered = true;
          }
          return;
        }
      }
      if (valid()) activity.timer = window.setTimeout(() => { activity.timer = undefined; void read(activity); }, 0);
    } catch {
      // The next runtime snapshot or explicit reconnect retries from this cursor.
    } finally {
      activity.reading = false;
    }
  }, []);

  const observe = useCallback((run: AgentRuntimeRun) => {
    const identity = { sessionId: String(run.sessionId || "").trim(), clientTurnId: String(run.clientTurnId || "").trim(), turnId: run.turnId };
    const owner = runtimeActivityOwner(latest.current.chats, identity);
    if (!owner) return;
    const stream = owner.items.find((item) => item.type === "streaming" && item.clientTurnId === identity.clientTurnId);
    // UI sends and question continuations retain their existing projection owner.
    if (stream && !isExternalRuntimeStream(stream)) return;
    const key = identityKey(identity);
    if (finished.current.has(key)) return;
    let activity = activities.current.get(key);
    if (!activity) {
      // A persisted real terminal receipt is already authoritative after a
      // reload. Existing in-flight history readers still finish their catch-up.
      if (hasAuthoritativeReply(owner, identity)) {
        finished.current.add(key);
        return;
      }
      const knownTurn = owner.items.some((item) => item.type === "user"
        && (item.clientTurnId === identity.clientTurnId || item.id === `user-${identity.clientTurnId}`))
        || owner.items.some((item) => item.type === "agent" && responseMatchesIdentity(item.response, identity));
      if (!isActiveRuntimeRunStatus(run.status) && !knownTurn) return;
      activity = { ...identity, chatId: owner.id, nextSequence: 0, stamp: "", controller: new AbortController(), reading: false, caughtUp: false };
      activities.current.set(key, activity);
      latest.current.updateChat(owner.id, (chat) => projectRuntimeTurnActivity(chat, run));
    }
    const stamp = JSON.stringify([run.turnId, run.status, run.updatedAt, run.eventCount]);
    // The ledger can settle before the response is durable. Existing runtime
    // snapshots retry that read even when no later ledger event changes stamp.
    const awaitingReceipt = !isActiveRuntimeRunStatus(run.status)
      && !activity.receiptRecovered && !hasAuthoritativeReply(owner, identity);
    if (activity.stamp !== stamp || awaitingReceipt) {
      activity.stamp = stamp;
      void read(activity);
    }
  }, [read]);

  useEffect(() => {
    if (!params.runtimeConnected) return;
    for (const run of params.runs) observe(run);
    for (const [key, activity] of activities.current) {
      const owner = runtimeActivityOwner(params.chats, activity);
      const settled = owner && (activity.receiptRecovered || hasAuthoritativeReply(owner, activity));
      if (!owner || owner.id !== activity.chatId || (settled && activity.caughtUp && !activity.reading)) {
        activity.controller.abort();
        if (activity.timer !== undefined) window.clearTimeout(activity.timer);
        activities.current.delete(key);
        if (settled) finished.current.add(key);
      }
    }
  }, [params.chats, params.runs, params.runtimeConnected, params.endpoint, observe]);

  useEffect(() => {
    if (params.runtimeConnected) for (const activity of activities.current.values()) void read(activity);
  }, [params.recoverySignal, params.runtimeConnected, read]);

  useEffect(() => {
    const recover = () => {
      if (!scope.current.connected || document.visibilityState === "hidden") return;
      for (const activity of activities.current.values()) void read(activity);
    };
    window.addEventListener("focus", recover);
    window.addEventListener("online", recover);
    document.addEventListener("visibilitychange", recover);
    return () => {
      window.removeEventListener("focus", recover);
      window.removeEventListener("online", recover);
      document.removeEventListener("visibilitychange", recover);
      for (const activity of activities.current.values()) {
        activity.controller.abort();
        if (activity.timer !== undefined) window.clearTimeout(activity.timer);
      }
      activities.current.clear();
      finished.current.clear();
    };
  }, [params.endpoint, params.runtimeConnected, read]);

  return useCallback((delta: AgentRuntimeDeltaEvent) => {
    if (!latest.current.runtimeConnected || !delta.sessionId || !delta.clientTurnId) return;
    const identity = { sessionId: delta.sessionId, clientTurnId: delta.clientTurnId, turnId: delta.turnId };
    const owner = runtimeActivityOwner(latest.current.chats, identity);
    if (!owner || hasAuthoritativeReply(owner, identity)) return;
    const activity = activities.current.get(identityKey(identity));
    if (activity?.turnId && delta.turnId && activity.turnId !== delta.turnId) return;
    if (!activity) observe({ ...identity, status: "running" });
    // The question observer applies its own live delta once; this observer
    // still owns history catch-up and the terminal receipt for that same row.
    const stream = owner.items.find((item) => item.type === "streaming" && item.clientTurnId === identity.clientTurnId);
    if (stream && !stream.id.startsWith(RUNTIME_ACTIVITY_STREAM_PREFIX)) return;
    latest.current.updateChat(owner.id, (chat) => applyRuntimeActivityDelta(chat, delta));
  }, [observe]);
}
