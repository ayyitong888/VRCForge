import type { ConversationItem } from "./chat-types";

type UserConversationItem = Extract<ConversationItem, { type: "user" }>;
type TurnItem = Extract<ConversationItem, { type: "agent" | "streaming" }>;

export type ConversationSteeringProjection = {
  items: ConversationItem[];
  messagesByTurnId: Map<string, UserConversationItem[]>;
  initialUsersByTurnId: Map<string, UserConversationItem>;
};

/** Attach sent steering messages to the active turn they arrived during. */
export function projectConversationSteeringMessages(items: ConversationItem[]): ConversationSteeringProjection {
  const messagesByTurnId = new Map<string, UserConversationItem[]>();
  const initialUsersByTurnId = new Map<string, UserConversationItem>();
  const consumed = new Set<string>();
  const turnItems = items
    .map((item, index) => ({ item, index }))
    .filter((entry): entry is { item: TurnItem; index: number } => (
      entry.item.type === "agent" || entry.item.type === "streaming"
    ));

  for (const { item: turn, index: turnIndex } of turnItems) {
    const turnId = turn.type === "agent"
      ? turn.response.clientTurnId || ""
      : turn.clientTurnId;
    if (!turnId) continue;
    const userTriggerIndex = items.findIndex((candidate) => (
      candidate.type === "user"
      && (candidate.clientTurnId === turnId || candidate.id === `user-${turnId}`)
    ));
    // Question replies resume work without appending a new user trigger. The
    // runtime's CID suffix and persisted answer identify the exact revision.
    const question = userTriggerIndex < 0 ? /:question:([^:]+)(?::feedback:([1-9]\d*))?$/.exec(turnId) : null;
    const triggerIndex = question ? items.findIndex((candidate) => candidate.type === "question_answer"
      && candidate.questionId === question[1] && (candidate.revision ?? 0) === Number(question[2] || 0))
      : userTriggerIndex;
    if (triggerIndex < 0 || triggerIndex >= turnIndex) continue;
    const trigger = items[triggerIndex];
    if (trigger.type === "user") {
      initialUsersByTurnId.set(turnId, trigger);
      consumed.add(trigger.id);
    } else if (trigger.type !== "question_answer") continue;
    const start = trigger.type === "user" ? parseTime(trigger.createdAt) : Math.max(
      ...[parseTime(trigger.updatedAt), parseTime(turn.type === "agent" ? turn.response.startedAt : turn.createdAt)].filter(Number.isFinite),
    );
    if (question && turn.type === "streaming" && !Number.isFinite(start)) continue;
    const confirmedIds = question && turn.type === "agent" ? new Set(turn.response.consumedSteerInputIds || []) : undefined;
    const terminal = turn.type === "agent" ? terminalEventTime(turn) : Number.POSITIVE_INFINITY;
    const nextTurnIndex = turnItems.find((entry) => entry.index > turnIndex)?.index ?? items.length;
    const endIndex = turn.type === "agent" ? turnIndex : nextTurnIndex;
    const messages: UserConversationItem[] = [];
    for (let index = triggerIndex + 1; index < endIndex; index += 1) {
      const candidate = items[index];
      if (question && turn.type === "streaming" && candidate.type === "question_answer") break;
      if (candidate.type !== "user" || !isSentFollowup(candidate) || consumed.has(candidate.id)) continue;
      // Live consumption clears queueStatus but keeps queuedFrom. Terminal
      // reconciliation clears both, so use the response's exact receipt IDs.
      if (question && !(confirmedIds ? confirmedIds.has(candidate.clientTurnId || "") : candidate.queuedFrom === true)) continue;
      const createdAt = parseTime(candidate.createdAt);
      if (!Number.isFinite(createdAt) || (Number.isFinite(start) && createdAt < start) || createdAt > terminal) continue;
      messages.push(candidate);
      consumed.add(candidate.id);
    }
    if (messages.length) messagesByTurnId.set(turnId, messages.sort((left, right) => parseTime(left.createdAt) - parseTime(right.createdAt)));
  }

  return { items: items.filter((item) => !consumed.has(item.id)), messagesByTurnId, initialUsersByTurnId };
}

function isSentFollowup(item: UserConversationItem): boolean {
  return Boolean(item.clientTurnId)
    && item.composerQueued !== true
    && !item.queueStatus;
}

function terminalEventTime(turn: Extract<ConversationItem, { type: "agent" }>): number {
  return parseTime(turn.completedAt || turn.response.completedAt);
}

function parseTime(value?: string): number {
  if (!value) return Number.NaN;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : Number.NaN;
}
