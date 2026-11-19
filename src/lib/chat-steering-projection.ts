import type { ConversationItem } from "./chat-types";
import type { AgentQuestion } from "./api/types";
import { conversationItemStart as itemStart } from "./chat-process-rounds";

type UserConversationItem = Extract<ConversationItem, { type: "user" }>;
type TurnItem = Extract<ConversationItem, { type: "agent" | "streaming" }>;
type AnswerItem = Extract<ConversationItem, { type: "question_answer" }>;
export type TimelineQuestionMessage = { kind: "ask" | "answer"; answer: AnswerItem; timestamp: string };

export type ConversationSteeringProjection = {
  items: ConversationItem[];
  messagesByTurnId: Map<string, UserConversationItem[]>;
  initialUsersByTurnId: Map<string, UserConversationItem>;
  questionMessages: Map<string, TimelineQuestionMessage[]>;
  placedQuestionIds: Set<string>;
};

/** Attach sent steering messages to the active turn they arrived during. */
export function projectConversationSteeringMessages(items: ConversationItem[], questions: AgentQuestion[] = []): ConversationSteeringProjection {
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
    if (triggerIndex < 0 || (!question && triggerIndex >= turnIndex)) continue;
    const trigger = items[triggerIndex];
    if (trigger.type === "user") {
      initialUsersByTurnId.set(turnId, trigger);
      consumed.add(trigger.id);
    } else if (trigger.type !== "question_answer") continue;
    const start = trigger.type === "user" ? parseTime(trigger.createdAt) : Math.max(
      ...[parseTime(trigger.updatedAt), parseTime(turn.type === "agent" ? turn.response.startedAt : turn.createdAt)].filter(Number.isFinite),
    );
    if (question && turn.type === "streaming" && !Number.isFinite(start)) continue;
    const confirmedIds = turn.type === "agent" ? new Set(turn.response.consumedSteerInputIds || []) : undefined;
    const terminal = turn.type === "agent" ? terminalEventTime(turn) : Number.POSITIVE_INFINITY;
    const nextTurnIndex = turnItems.find((entry) => entry.index > turnIndex)?.index ?? items.length;
    const endIndex = turn.type === "agent" && confirmedIds?.size ? items.length : turn.type === "agent" ? turnIndex : nextTurnIndex;
    const messages: UserConversationItem[] = [];
    for (let index = triggerIndex + 1; index < endIndex; index += 1) {
      const candidate = items[index];
      if (question && turn.type === "streaming" && candidate.type === "question_answer") break;
      if (candidate.type !== "user" || !isSentFollowup(candidate) || consumed.has(candidate.id)) continue;
      if (turn.type === "agent" && index >= turnIndex && !confirmedIds?.has(candidate.clientTurnId || "")) continue;
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

  const questionMessages = new Map<string, TimelineQuestionMessage[]>();
  const placedQuestionIds = new Set<string>();
  const standaloneAnswers = new Map<string, AnswerItem>();
  const place = (owner: TurnItem, message: TimelineQuestionMessage) => {
    const id = owner.type === "agent" ? owner.response.clientTurnId : owner.clientTurnId;
    if (!id) return false;
    const messages = questionMessages.get(id) || [];
    messages.push(message);
    questionMessages.set(id, messages);
    return true;
  };
  for (const item of items) {
    if (item.type !== "question_answer") continue;
    const question = questions.find(q => q.questionId === item.questionId);
    const client = item.originClientTurnId || question?.originClientTurnId;
    const turn = item.originTurnId || question?.originTurnId;
    const askedAt = item.askedAt || question?.createdAt;
    const sameRevision = (item.revision ?? 0) === (question?.questionFeedbackRevision ?? 0);
    const answerTime = (sameRevision && question?.answeredAt) || item.updatedAt;
    if (!(client || turn) || !askedAt || !answerTime) continue;
    const owners = turnItems.filter(({ item: entry }) => {
      const run = entry.type === "agent" ? entry.response : entry;
      return client ? run.clientTurnId === client : run.turnId === turn;
    });
    if (owners.length !== 1) continue;
    const askingTurn = owners[0].item;
    const answered = parseTime(answerTime);
    if (!(answered >= parseTime(askedAt))) continue;
    const answer = { ...item, updatedAt: answerTime, question: item.question ?? question?.question };
    if (!answer.revision && answer.question && hasTimeline(askingTurn)
      && place(askingTurn, { kind: "ask", answer, timestamp: askedAt })) {
      placedQuestionIds.add(answer.questionId);
    }
    // A person can reply while a different run is working. Its timeline owns the
    // position of the answer, while the question stays with the turn that asked.
    const overlapping = turnItems.map(entry => entry.item).filter(turn => turnContainsAnswer(turn, answered));
    const host = overlapping.reduce<TurnItem | undefined>((latest, next) =>
      !latest || itemStart(next) >= itemStart(latest) ? next : latest, undefined);
    if (host && place(host, { kind: "answer", answer, timestamp: answerTime })) consumed.add(item.id);
    else standaloneAnswers.set(item.id, answer);
  }
  const projected = items.filter(item => !consumed.has(item.id)).map(item => standaloneAnswers.get(item.id) || item);
  return { items: projected, messagesByTurnId, initialUsersByTurnId, questionMessages, placedQuestionIds };
}

function hasTimeline(turn: TurnItem) {
  return Boolean((turn.timeline || (turn.type === "agent" ? turn.response.timeline : undefined))?.length);
}

function turnContainsAnswer(owner: TurnItem, answered: number) {
  const timeline = owner.timeline || (owner.type === "agent" ? owner.response.timeline : undefined);
  if (!Number.isFinite(answered) || !timeline?.some(event => parseTime(event.timestamp) <= answered)) return false;
  return owner.type === "streaming" || terminalEventTime(owner) >= answered
    || timeline.some(event => parseTime(event.timestamp) >= answered);
}

/** Historical attribution is looked up only for question IDs present in the selected chat. */
export function missingTimelineAnswerSources(items: ConversationItem[], known: AgentQuestion[]) {
  const knownIds = new Set(known.filter(question => question.createdAt && (question.originClientTurnId || question.originTurnId))
    .map(question => question.questionId));
  if (!items.some(item => item.type === "agent" || item.type === "streaming")) return [];
  return [...new Set(items.flatMap(item => {
    if (item.type !== "question_answer" || knownIds.has(item.questionId)
      || (item.askedAt && (item.originClientTurnId || item.originTurnId))) return [];
    return Number.isFinite(parseTime(item.updatedAt)) ? [item.questionId] : [];
  }))];
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
