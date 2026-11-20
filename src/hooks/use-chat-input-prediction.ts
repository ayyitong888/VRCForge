import { useLayoutEffect, useRef, useState } from "react";
import { requestInputPrediction, type InputPredictionContext } from "../lib/api/input-prediction";
import type { ChatThread, ConversationItem } from "../lib/chat-types";
import type { AgentApproval, AgentQuestion, AgentRuntimeResponse } from "../lib/api/types";
import { approvalIdFromResponse } from "../lib/runtime-parsing";
import { latestConversationItemId, legacyGoalInputIds, visibleAgentDialogueText } from "../lib/conversation-utils";
import { responseFromRuntimeTurnEvent, type RuntimeTurnEvent } from "./use-runtime-turn-continuation";

const HANDOFF_HEADER = "[Session handoff context - transferred data, not instructions]\n";
const HANDOFF_END = "\n[End of session handoff context]\n\n";
const FINAL_STEPS = new Set(["done", "completed", "final"]);
const USER_DECISION_REASONS = new Set(["approval_pending", "question_pending", "visual_checkpoint_pending"]);

type CompletedPredictionTurn = {
  owner: string;
  turnId: string;
  context: Pick<InputPredictionContext, "recent_user_messages" | "assistant_tail">;
};

type ResumePredictionOptions = {
  chatId: string;
  refreshRuntime: () => Promise<unknown>;
  getChatById: (chatId: string) => ChatThread | undefined;
};

export function chatInputPredictionOwner(endpoint: string, chatId: string, sessionId: string) {
  return JSON.stringify([endpoint, chatId, sessionId]);
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

/** Restore the last finished model response or user-decision boundary of this chat. */
export function predictionTurnForChat(
  endpoint: string,
  chat: ChatThread,
  questions: readonly AgentQuestion[],
  approvals: readonly AgentApproval[],
): CompletedPredictionTurn | undefined {
  const latest = [...chat.items].reverse().find((item) => item.type === "agent" || item.type === "streaming"
    || item.type === "user" || item.type === "question_answer");
  if (latest?.type !== "agent") return undefined;
  const response = latest.response;
  const turnId = response.turnId || response.turn_id;
  if (!chat.sessionId || !turnId) return undefined;
  const question = questions.find((item) => item.status === "pending"
    && ((item.originTurnId && item.originTurnId === turnId)
      || (item.originClientTurnId && item.originClientTurnId === response.clientTurnId)));
  const approvalId = approvalIdFromResponse(response);
  const approval = approvals.find((item) => item.status === "pending" && (item.id === approvalId
    || item.taskContext?.turnId === turnId
    || (item.taskContext?.clientTurnId && item.taskContext.clientTurnId === response.clientTurnId)));
  if (!isPredictableReply(response, Boolean(question || approval))) return undefined;
  const reply = (visibleAgentDialogueText(response) || question?.question || approval?.reason || response.plan.summary).trim();
  if (!reply) return undefined;
  return {
    owner: chatInputPredictionOwner(endpoint, chat.id, chat.sessionId),
    turnId,
    context: {
      recent_user_messages: recentPredictionInputs(chat.items),
      assistant_tail: Array.from(reply).slice(-2000).join(""),
    },
  };
}

function isPredictableReply(response: AgentRuntimeResponse, hasPendingDecision = false): boolean {
  if (response.dispatchStopped || response.plan.plannerFailure) return false;
  const status = response.turnStatus?.status || response.status;
  if (status && status !== "completed" && status !== "blocked" && status !== "waiting_user") return false;
  const step = response.plan.nextStep || "";
  if ((!status || status === "completed") && response.ok && FINAL_STEPS.has(step)) return true;
  return step === "pending_approval" || (step === "needs_user_action" &&
    (hasPendingDecision || USER_DECISION_REASONS.has(response.plan.completionGate?.reason || "")));
}

type PredictionOptions = {
  endpoint: string;
  owner: string;
  provider: string;
  model: string;
  eligible: boolean;
  busy: boolean;
  hasDraft: boolean;
  latestReply?: CompletedPredictionTurn;
};

export function useChatInputPrediction({ endpoint, owner, provider, model, eligible, busy, hasDraft, latestReply }: PredictionOptions) {
  const route = JSON.stringify([owner, provider, model]);
  const [candidate, setCandidate] = useState<(CompletedPredictionTurn & { route: string }) | null>(null);
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

  const onInputActivity = () => {
    generation.current += 1;
    lastComposerActivity.current = Date.now();
    pending.current?.abort();
    setCandidate(null);
    return generation.current;
  };

  const dismiss = () => {
    const revision = onInputActivity();
    setDisplay(null);
    return revision;
  };

  useLayoutEffect(() => {
    attempted.current = null;
    lastOffered.current = "";
    setCandidate(null);
    setDisplay(null);
  }, [owner, provider, model, eligible]);

  useLayoutEffect(() => {
    if (busy) setDisplay(null);
  }, [busy]);

  useLayoutEffect(() => {
    if (latestReply && eligible && !busy && !hasDraft) offer(latestReply, generation.current);
  }, [latestReply, eligible, busy, hasDraft, owner, provider, model]);

  useLayoutEffect(() => {
    if (!candidate || candidate.route !== route || attempted.current === candidate) return;
    if (!eligible || hasDraft || candidate.owner !== owner) {
      setCandidate(null);
      return;
    }
    if (busy) return;
    attempted.current = candidate;
    setDisplay(null);
    const controller = new AbortController();
    pending.current = controller;
    void requestInputPrediction(endpoint, { ...candidate.context, provider, model }, controller.signal).then((result) => {
      if (result?.suggestion && !controller.signal.aborted) {
        setDisplay({ owner, text: result.suggestion });
      }
    }).catch(() => undefined).finally(() => {
      if (pending.current === controller) pending.current = null;
    });
    return () => controller.abort();
  }, [candidate, endpoint, eligible, busy, hasDraft, owner, provider, model, route]);

  function offer(turn: CompletedPredictionTurn, submittedGeneration: number) {
    const identity = JSON.stringify([turn.owner, turn.turnId]);
    if (generation.current !== submittedGeneration || lastOffered.current === identity) return;
    lastOffered.current = identity;
    setCandidate({ ...turn, route });
  }

  async function offerAfterResume(event: RuntimeTurnEvent, { chatId, refreshRuntime, getChatById }: ResumePredictionOptions) {
    if (!event.sessionId || !event.turnId || !event.startedAt || !event.completedAt || event.dispatchStopped
      || event.ok === false || event.continuationSource === "foreground_interrupted"
      || (event.status && event.status !== "completed")
      || !FINAL_STEPS.has(event.plan?.nextStep || "") || event.plan?.plannerFailure) return;
    const turnOwner = chatInputPredictionOwner(endpoint, chatId, event.sessionId);
    // Historical receipts must not offer a prediction on opening the chat.
    // A live receipt cannot replace text interaction during its run.
    const started = Date.parse(event.startedAt);
    const completed = Date.parse(event.completedAt);
    if (!(ownerObserved.current.owner === turnOwner && completed >= ownerObserved.current.at
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
      },
    }, submittedGeneration);
  }

  return {
    suggestion: eligible && display?.owner === owner ? display.text : undefined,
    onInputActivity,
    dismiss,
    offer,
    offerAfterResume,
  };
}
