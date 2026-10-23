import type { ChatThread, ConversationItem } from "./chat-types";
import { mergeRuntimeTimelines } from "./chat-timeline-presentation";

export const RUNTIME_ACTIVITY_STREAM_PREFIX = "runtime-activity:";
export const QUESTION_CONTINUATION_STREAM_PREFIX = "question-continuation:";
type Stream = Extract<ConversationItem, { type: "streaming" }>;

export function isExternalRuntimeStream(item: ConversationItem): item is Stream {
  return item.type === "streaming" && (item.id.startsWith(RUNTIME_ACTIVITY_STREAM_PREFIX)
    || item.id.startsWith(QUESTION_CONTINUATION_STREAM_PREFIX));
}

/** A CID has one live owner, irrespective of which observer discovered it first. */
export function coalesceRuntimeTurnStreams(chat: ChatThread, clientTurnId: string): ChatThread {
  const streams = chat.items.filter((item): item is Stream => item.type === "streaming" && item.clientTurnId === clientTurnId);
  if (streams.length < 2) return chat;
  // Foreground sends keep their HTTP lifecycle; observers never take it over.
  const owner = streams.find((item) => !isExternalRuntimeStream(item)) || streams[0];
  let merged = owner;
  for (const item of streams) {
    if (item === owner) continue;
    const text = merged.text.startsWith(item.text) ? merged.text
      : item.text.startsWith(merged.text) ? item.text : [merged.text, item.text].filter(Boolean).join("\n");
    merged = { ...item, ...merged, text, timeline: mergeRuntimeTimelines(item.timeline || [], merged.timeline || []),
      createdAt: merged.createdAt || item.createdAt, phase: merged.phase || item.phase,
      providerLastActivityAt: merged.providerLastActivityAt || item.providerLastActivityAt,
      providerRetry: merged.providerRetry || item.providerRetry,
      providerLabel: merged.providerLabel || item.providerLabel, model: merged.model || item.model };
  }
  return { ...chat, items: chat.items.flatMap((item) => item.type === "streaming" && item.clientTurnId === clientTurnId
    ? item === owner ? [merged] : [] : [item]) };
}
