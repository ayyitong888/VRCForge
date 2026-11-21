import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { AgentQuestion, AgentRuntimeResponse } from "./api";
import type { ChatTimelineEvent, ConversationItem } from "./chat-types";
import { isCancellationMarkerAssistantText, isCancellationMarkerTimelineEvent } from "./chat-timeline-presentation";
import { includesQuestionText, visibleAgentDialogueText } from "./conversation-utils";
import { assistantReplyTimestamp, parseMessageTimestamp } from "./message-time";

type Reveal = { questionId: string };
type Utterance = { key: string; turn: string; text: string; at?: number; live?: boolean };
type AssistantEvent = ChatTimelineEvent | NonNullable<AgentRuntimeResponse["timeline"]>[number];

function assistantUtterances(conversation: ConversationItem[], sessionId: string): Utterance[] {
  const utterances = new Map<string, Utterance>();
  const collect = (event: AssistantEvent, owner: string): Utterance | undefined => {
    if (event.kind !== "assistant" || (sessionId && event.sessionId && event.sessionId !== sessionId)
      || event.payload?.status === "completion_unverified" || isCancellationMarkerTimelineEvent(event)) return;
    const text = event.payload?.summary?.trim();
    if (!text) return;
    const turn = event.clientTurnId || owner || event.turnId || "";
    const key = `speech:${turn}:${event.payload?.messageId || event.id || event.timestamp || text}`;
    const value = { key, turn, text, at: parseMessageTimestamp(event.timestamp) };
    utterances.set(key, value);
    return value;
  };

  for (const item of conversation) {
    if (item.type === "timeline_event") {
      collect(item.event, "");
      continue;
    }
    if (item.type !== "agent" && item.type !== "streaming") continue;
    const itemSession = item.type === "agent" ? item.response.sessionId || item.response.session_id : item.sessionId;
    if (sessionId && itemSession && sessionId !== itemSession) continue;
    const turn = item.type === "agent"
      ? item.response.clientTurnId || item.response.turnId || item.response.turn_id || item.id
      : item.clientTurnId || item.turnId || item.id;
    const timeline = item.type === "agent" ? [...(item.response.timeline || []), ...(item.timeline || [])] : item.timeline || [];
    let last: Utterance | undefined;
    for (const event of timeline) last = collect(event, turn) || last;
    if (item.type === "streaming") {
      const text = item.text.trim();
      if (!text || text === last?.text || isCancellationMarkerAssistantText(text)) continue;
      const key = `stream:${turn}:${last?.key || ""}`;
      utterances.set(key, { key, turn, text, live: true });
    } else if (!last && (item.response.plan?.reply || item.response.plan?.publicMessages?.length)) {
      const text = visibleAgentDialogueText(item.response).trim();
      if (!text || isCancellationMarkerAssistantText(text)) continue;
      const key = `reply:${turn}`;
      utterances.set(key, { key, turn, text, at: assistantReplyTimestamp(item) });
    }
  }
  return [...utterances.values()];
}

function spokeAfterQuestion(question: AgentQuestion, speech: Utterance[], previous?: Utterance[]): boolean {
  const askedAt = parseMessageTimestamp(question.createdAt);
  const prompt = question.question?.replace(/\s+/g, "");
  const previousKeys = previous && new Set(previous.map((line) => line.key));
  return speech.some((line) => {
    if (previousKeys?.has(line.key)) return false;
    if (line.at !== undefined && askedAt !== undefined && line.at <= askedAt) return false;
    if (!previous && (line.at === undefined || askedAt === undefined)) return false;
    if (prompt && (includesQuestionText(line.text, prompt) || (line.live && prompt.startsWith(line.text.replace(/\s+/g, ""))))) return false;
    return !previous?.some((prior) => prior.turn && prior.turn === line.turn
      && (prior.text === line.text || (prior.live && line.text.startsWith(prior.text))));
  });
}

/** The dock follows the next visible assistant utterance. The question itself stays pending. */
export function useQuestionDockPresentation({ chatId, sessionId, conversation, questions, liveQuestions, reveal, onSpeechDismissed }: {
  chatId: string;
  sessionId: string;
  conversation: ConversationItem[];
  questions: AgentQuestion[];
  liveQuestions: AgentQuestion[];
  reveal?: Reveal;
  onSpeechDismissed?: (questionId: string) => void;
}): AgentQuestion | undefined {
  const speech = useMemo(() => assistantUtterances(conversation, sessionId), [conversation, sessionId]);
  const [visible, setVisible] = useState<{ chatId: string; questionId: string }>();
  const previous = useRef<{
    chatId: string;
    openedAt: number;
    pending: Set<string>;
    speech: Utterance[];
    appliedReveal?: Reveal;
  }>();
  useLayoutEffect(() => {
    const prior = previous.current;
    const sameChat = prior?.chatId === chatId;
    const openedAt = sameChat ? prior.openedAt : Date.now();
    const requested = reveal && reveal !== prior?.appliedReveal
      ? questions.find((question) => question.questionId === reveal.questionId) : undefined;
    const appeared = questions.filter((question) => (!sameChat || !prior.pending.has(question.questionId))
      && (liveQuestions.some((live) => live.questionId === question.questionId)
        || (parseMessageTimestamp(question.createdAt) ?? 0) >= openedAt)
      && !spokeAfterQuestion(question, speech));
    const newQuestion = appeared.reduce<AgentQuestion | undefined>((latest, question) =>
      !latest || (parseMessageTimestamp(question.createdAt) ?? 0) >= (parseMessageTimestamp(latest.createdAt) ?? 0)
        ? question : latest, undefined);
    const current = sameChat && visible?.chatId === chatId
      ? questions.find((question) => question.questionId === visible.questionId) : undefined;
    const spoken = current && spokeAfterQuestion(current, speech, prior?.speech);
    const selected = requested || newQuestion || (spoken ? undefined : current);
    previous.current = { chatId, openedAt, pending: new Set(questions.map((question) => question.questionId)), speech,
      appliedReveal: requested ? reveal : prior?.appliedReveal };
    if (selected ? visible?.chatId !== chatId || visible.questionId !== selected.questionId : visible !== undefined) {
      setVisible(selected ? { chatId, questionId: selected.questionId } : undefined);
    }
    if (!requested && spoken) onSpeechDismissed?.(current.questionId);
  }, [chatId, questions, liveQuestions, reveal, speech, visible, onSpeechDismissed]);
  return visible?.chatId === chatId ? questions.find((question) => question.questionId === visible.questionId) : undefined;
}
