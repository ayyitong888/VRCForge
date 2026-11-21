import { useLayoutEffect, useRef, useState } from "react";
import { requestInputPrediction, type InputPredictionContext } from "../lib/api/input-prediction";
import type { ChatThread, ConversationItem } from "../lib/chat-types";
import type { AgentApproval, AgentQuestion, AgentRuntimeResponse } from "../lib/api/types";
import { approvalIdFromResponse } from "../lib/runtime-parsing";
import { latestConversationItemId, legacyGoalInputIds, visibleAgentDialogueText } from "../lib/conversation-utils";
import { isInterruptedRuntimeTurn } from "../lib/runtime-run-status";
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
    if (item.type === "question_answer") return item.selectedOptionId !== "skip" && item.text.trim() ? [item.text] : [];
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
    || item.type === "user" || (item.type === "question_answer" && item.selectedOptionId !== "skip"));
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
  if (!isPredictableReply(response, { hasPendingDecision: Boolean(question || approval) })) return undefined;
  const reply = visibleAgentDialogueText(response) || (question?.question || approval?.reason || response.plan.summary).trim();
  if (!reply.trim()) return undefined;
  return {
    owner: chatInputPredictionOwner(endpoint, chat.id, chat.sessionId),
    turnId,
    context: {
      recent_user_messages: recentPredictionInputs(chat.items),
      assistant_tail: reply,
    },
  };
}

export function isPredictableReply(response: AgentRuntimeResponse, decision?: { hasPendingDecision: boolean }): boolean {
  if (response.dispatchStopped || response.plan.plannerFailure || isInterruptedRuntimeTurn(response)) return false;
  const status = response.turnStatus?.status || response.status;
  const step = response.plan.nextStep || "";
  // A tool failure can end the turn with a complete, visible assistant reply.
  if (step === "tool_failed") return !status || status === "failed" || status === "blocked" || status === "completed";
  if (status && status !== "completed" && status !== "blocked" && status !== "waiting_user") return false;
  if (response.ok && (!status || status === "completed") && (FINAL_STEPS.has(step) || (!step && status === "completed"))) return true;
  return Boolean(decision && (step === "pending_approval" || (step === "needs_user_action" &&
    (decision.hasPendingDecision || USER_DECISION_REASONS.has(response.plan.completionGate?.reason || "")))));
}

type PredictionOptions = {
  endpoint: string;
  owner: string;
  provider: string;
  model: string;
  reasoningEffort?: string;
  eligible: boolean;
  busy: boolean;
  hasDraft: boolean;
  latestReply?: CompletedPredictionTurn;
};

type CachedPrediction = { turnId: string; text: string };

function loadCompletedPrediction(key: string): CachedPrediction | null {
  try {
    const saved = JSON.parse(window.localStorage.getItem(key) || "null");
    return typeof saved?.turnId === "string" && typeof saved.text === "string" ? saved : null;
  } catch {
    return null;
  }
}

export function useChatInputPrediction({ endpoint, owner, provider, model, reasoningEffort, eligible, busy, hasDraft, latestReply }: PredictionOptions) {
  const route = JSON.stringify([owner, provider, model, reasoningEffort]);
  const [, chatId] = JSON.parse(owner) as [string, string, string];
  // The backend can listen on a different port after an app restart.
  const cacheKey = "vrcforge_input_prediction_result:" + chatId;
  const [candidate, setCandidate] = useState<(CompletedPredictionTurn & { route: string }) | null>(null);
  const [display, setDisplay] = useState<{ owner: string; text: string } | null>(null);
  const completedPredictions = useRef(new Map<string, CachedPrediction>());
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
  }, [owner, provider, model, reasoningEffort, eligible]);

  useLayoutEffect(() => {
    if (busy) setDisplay(null);
  }, [busy]);

  useLayoutEffect(() => {
    if (latestReply && eligible && !busy && !hasDraft) offer(latestReply, generation.current);
  }, [latestReply, eligible, busy, hasDraft, owner, provider, model, reasoningEffort]);

  useLayoutEffect(() => {
    if (!candidate || candidate.route !== route || attempted.current === candidate) return;
    if (!eligible || hasDraft || candidate.owner !== owner) {
      setCandidate(null);
      return;
    }
    if (busy) return;
    attempted.current = candidate;
    const cached = completedPredictions.current.get(cacheKey) ?? loadCompletedPrediction(cacheKey);
    if (cached?.turnId === candidate.turnId) {
      setDisplay({ owner, text: cached.text });
      return;
    }
    if (cached) {
      completedPredictions.current.delete(cacheKey);
      try {
        window.localStorage.removeItem(cacheKey);
      } catch {
        // The turn check still prevents displaying the old result.
      }
    }
    setDisplay(null);
    const controller = new AbortController();
    pending.current = controller;
    void requestInputPrediction(endpoint, { ...candidate.context, provider, model, reasoning_effort: reasoningEffort || undefined }, controller.signal).then((result) => {
      if (result?.suggestion && !controller.signal.aborted) {
        const completed = { turnId: candidate.turnId, text: result.suggestion };
        completedPredictions.current.set(cacheKey, completed);
        try {
          window.localStorage.setItem(cacheKey, JSON.stringify(completed));
        } catch {
          // Keep the successful result available in this window.
        }
        setDisplay({ owner, text: result.suggestion });
      }
    }).catch(() => undefined).finally(() => {
      if (pending.current === controller) pending.current = null;
    });
    return () => controller.abort();
  }, [candidate, endpoint, eligible, busy, hasDraft, owner, provider, model, reasoningEffort, route, cacheKey]);

  function offer(turn: CompletedPredictionTurn, submittedGeneration: number) {
    const identity = JSON.stringify([turn.owner, turn.turnId]);
    if (generation.current !== submittedGeneration || lastOffered.current === identity) return;
    lastOffered.current = identity;
    setCandidate({ ...turn, route });
  }

  async function offerAfterResume(event: RuntimeTurnEvent, { chatId, refreshRuntime, getChatById }: ResumePredictionOptions) {
    if (!event.sessionId || !event.turnId || !event.startedAt || !event.completedAt || !event.plan?.nextStep
      || event.continuationSource === "foreground_interrupted") return;
    const response = responseFromRuntimeTurnEvent(event);
    if (!isPredictableReply(response)) return;
    const turnOwner = chatInputPredictionOwner(endpoint, chatId, event.sessionId);
    // Historical receipts must not offer a prediction on opening the chat.
    // A live receipt cannot replace text interaction during its run.
    const started = Date.parse(event.startedAt);
    const completed = Date.parse(event.completedAt);
    if (!(ownerObserved.current.owner === turnOwner && completed >= ownerObserved.current.at
      && started > lastComposerActivity.current)) return;
    const submittedGeneration = generation.current;
    const publicReply = visibleAgentDialogueText(response);
    if (!publicReply.trim()) return;
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
        assistant_tail: publicReply,
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
