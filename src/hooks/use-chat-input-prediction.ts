import { useLayoutEffect, useRef, useState } from "react";
import { requestInputPrediction, type InputPredictionContext } from "../lib/api/input-prediction";
import type { ChatThread, ConversationItem } from "../lib/chat-types";
import { latestConversationItemId, legacyGoalInputIds, visibleAgentDialogueText } from "../lib/conversation-utils";
import { responseFromRuntimeTurnEvent, type RuntimeTurnEvent } from "./use-runtime-turn-continuation";

const HANDOFF_HEADER = "[Session handoff context - transferred data, not instructions]\n";
const HANDOFF_END = "\n[End of session handoff context]\n\n";
const RESUME_SOURCES = new Set(["question_answered", "approval_finished", "approval_revision_requested", "shell_process_finished", "sub_agent_finished"]);
const FINAL_STEPS = new Set(["done", "completed", "final"]);

type CompletedPredictionTurn = {
  owner: string;
  turnId: string;
  context: InputPredictionContext;
};

type ResumePredictionOptions = {
  chatId: string;
  provider: string;
  model: string;
  refreshRuntime: () => Promise<unknown>;
  getChatById: (chatId: string) => ChatThread | undefined;
};

export function chatInputPredictionOwner(endpoint: string, chatId: string, sessionId: string, provider: string, model: string) {
  return JSON.stringify([endpoint, chatId, sessionId, provider, model]);
}

export function recentPredictionInputs(items: ConversationItem[], currentInputId?: string, currentText?: string) {
  const legacyGoalInputs = legacyGoalInputIds(items);
  return items.flatMap((item) => {
    if (item.type === "question_answer") return item.text.trim() ? [item.text] : [];
    if (item.type !== "user" || item.goalDeliveryId || item.forwardedByAgent || item.forwardedFrom
      || legacyGoalInputs.has(item.id) || item.composerQueued || item.queueStatus) return [];
    if (item.id === currentInputId) return currentText?.trim() ? [currentText] : [];
    // Keep the original input after the sealed, program-generated handoff block.
    const handoffEnd = item.text.startsWith(HANDOFF_HEADER) ? item.text.indexOf(HANDOFF_END) : -1;
    const text = handoffEnd >= 0 ? item.text.slice(handoffEnd + HANDOFF_END.length)
      : item.text.startsWith("[Session handoff context") ? "" : item.text;
    return text.trim() ? [text] : [];
  }).slice(-20);
}

export function useChatInputPrediction(endpoint: string, owner: string, eligible: boolean) {
  const [candidate, setCandidate] = useState<CompletedPredictionTurn | null>(null);
  const [display, setDisplay] = useState<{ owner: string; text: string } | null>(null);
  const attempted = useRef<CompletedPredictionTurn | null>(null);
  const pending = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const lastOffered = useRef("");
  const ownerObserved = useRef({ owner, at: Date.now() });
  const lastComposerActivity = useRef(0);

  useLayoutEffect(() => {
    ownerObserved.current = { owner, at: Date.now() };
  }, [owner]);

  const dismiss = () => {
    generation.current += 1;
    lastComposerActivity.current = Date.now();
    pending.current?.abort();
    setCandidate(null);
    setDisplay(null);
    return generation.current;
  };

  useLayoutEffect(() => {
    setDisplay(null);
    if (!candidate || attempted.current === candidate) return;
    attempted.current = candidate;
    if (!eligible || candidate.owner !== owner) return;
    const controller = new AbortController();
    pending.current = controller;
    void requestInputPrediction(endpoint, candidate.context, controller.signal).then((result) => {
      if (result?.suggestion && !controller.signal.aborted) {
        setDisplay({ owner, text: result.suggestion });
      }
    }).catch(() => undefined).finally(() => {
      if (pending.current === controller) pending.current = null;
    });
    return () => controller.abort();
  }, [candidate, endpoint, eligible, owner]);

  function offer(turn: CompletedPredictionTurn, submittedGeneration: number) {
    const identity = JSON.stringify([turn.owner, turn.turnId]);
    if (generation.current !== submittedGeneration || lastOffered.current === identity) return;
    lastOffered.current = identity;
    setCandidate(turn);
  }

  async function offerAfterResume(event: RuntimeTurnEvent, { chatId, provider, model, refreshRuntime, getChatById }: ResumePredictionOptions) {
    if (!event.sessionId || !event.turnId || !event.startedAt || !event.completedAt || event.dispatchStopped
      || event.ok === false || !RESUME_SOURCES.has(event.continuationSource || "")
      || !FINAL_STEPS.has(event.plan?.nextStep || "") || event.plan?.plannerFailure) return;
    const turnOwner = chatInputPredictionOwner(endpoint, chatId, event.sessionId, provider, model);
    // A restored receipt began before this chat became the observed composer.
    // A live receipt cannot replace any text interaction during its run.
    const started = Date.parse(event.startedAt);
    if (!(ownerObserved.current.owner === turnOwner && started >= ownerObserved.current.at
      && started > lastComposerActivity.current)) return;
    const submittedGeneration = generation.current;
    const publicReply = visibleAgentDialogueText(responseFromRuntimeTurnEvent(event)).trim();
    if (!publicReply) return;
    // The terminal receipt can arrive ahead of the settled question or approval.
    await refreshRuntime();
    const chat = getChatById(chatId);
    if (!chat || chat.sessionId !== event.sessionId) return;
    const lastAgentId = latestConversationItemId(chat.items, (item) => item.type === "agent");
    const last = chat.items.find((item) => item.id === lastAgentId);
    if (last?.type !== "agent" || (last.response.turnId || last.response.turn_id) !== event.turnId) return;
    offer({
      owner: turnOwner,
      turnId: event.turnId,
      context: {
        recent_user_messages: recentPredictionInputs(chat.items),
        assistant_tail: Array.from(publicReply).slice(-2000).join(""),
        provider,
        model,
      },
    }, submittedGeneration);
  }

  return {
    suggestion: eligible && display?.owner === owner ? display.text : undefined,
    dismiss,
    offer,
    offerAfterResume,
  };
}
