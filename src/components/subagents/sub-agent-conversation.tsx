import { useEffect, useState } from "react";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { fetchAgentRunTimeline, fetchSubAgent, type AgentRuntimeRun, type AgentRuntimeResponse, type SubAgentTask } from "../../lib/api";
import { ApiError } from "../../lib/api/http";
import type { ChatTimelineEvent, ConversationItem } from "../../lib/chat-types";
import { normalizeAgentRuntimePhase } from "../../lib/chat-streaming";
import { projectConversationSteeringMessages } from "../../lib/chat-steering-projection";
import { materializeRuntimeTimeline, mergeConversationTimelineItems, mergeRuntimeTimelines } from "../../lib/chat-timeline-presentation";
import { conversationRoundDateSeparators } from "../../lib/message-time";
import { displaySubAgentStatus } from "../../lib/subagent-ui";
import { subAgentResultSummaryText } from "../../lib/subagent-merge";
import { ConversationCard, MessageDateSeparator, StreamingPhaseStatus } from "../chat/conversation-card";
import { ChatMarkdown } from "../chat/chat-markdown";
import { Button } from "../ui/button";

type TurnTimeline = {
  events: ChatTimelineEvent[];
  nextSequence: number;
  run?: AgentRuntimeRun | null;
  detail?: SubAgentTask;
  detailChecked?: boolean;
};
const RUNNING = new Set(["queued", "running", "cancelling"]);

function recordedResponse(turn: SubAgentTask): AgentRuntimeResponse | undefined {
  const result = turn.result?.rawResult as Partial<AgentRuntimeResponse> | undefined;
  if (turn.childSessionId && (result?.sessionId || result?.session_id) === turn.childSessionId
    && result?.clientTurnId === turn.id && (result.turnId || result.turn_id) && result.plan) {
    return result as AgentRuntimeResponse;
  }
}

export function subAgentConversationModel(turn: SubAgentTask): string {
  const model = recordedResponse(turn)?.model;
  if (model?.trim()) return model.trim();
  const route = turn.result?.providerRoute as { model?: unknown } | undefined;
  return typeof route?.model === "string" ? route.model.trim() : "";
}

function turnItems(turn: SubAgentTask, timeline?: TurnTimeline): ConversationItem[] {
  const user: ConversationItem = { id: `user-${turn.id}`, clientTurnId: turn.id, type: "user", text: turn.task, createdAt: turn.createdAt };
  const recorded = recordedResponse(turn);
  const ledger = timeline?.run;
  const startedAt = recorded?.startedAt || ledger?.startedAt || turn.startedAt;
  const events = mergeRuntimeTimelines(
    recorded ? materializeRuntimeTimeline(recorded, startedAt || turn.createdAt || "") : [],
    timeline?.events || [],
  );
  if (RUNNING.has(turn.status)) {
    const phase = [...events].reverse().find(event => event.kind === "phase");
    return [user, {
      id: turn.id, type: "streaming", clientTurnId: turn.id, sessionId: turn.childSessionId,
      turnId: ledger?.turnId, createdAt: startedAt || turn.createdAt, timeline: events, text: "",
      phase: normalizeAgentRuntimePhase(phase?.payload.phase || phase?.payload.status),
      stopRequested: turn.status === "cancelling" || turn.cancelRequested === true,
    }];
  }
  const paused = turn.status === "paused";
  const completedAt = recorded?.completedAt || turn.stoppedAt;
  const finalReport = typeof turn.result?.finalReport === "string" ? turn.result.finalReport.trim() : "";
  const reply = paused ? "" : finalReport || subAgentResultSummaryText(turn);
  const elapsed = (Date.parse(completedAt || "") - Date.parse(startedAt || "")) / 1000;
  const response: AgentRuntimeResponse = {
    ok: turn.status === "completed",
    sessionId: turn.childSessionId || "", session_id: turn.childSessionId || "",
    turnId: ledger?.turnId || "", turn_id: ledger?.turnId || "",
    clientTurnId: turn.id,
    observe: {},
    plan: { reply, summary: "", planner: "", shellNeeded: false },
    ...recorded,
    startedAt, completedAt, timeline: events,
    status: turn.status === "cancelled" || turn.status === "interrupted" ? turn.status : recorded?.status || ledger?.status,
  };
  return [user, {
    id: turn.id, type: "agent", response,
    createdAt: startedAt, completedAt, elapsedSeconds: Number.isFinite(elapsed) && elapsed >= 0 ? elapsed : undefined,
  }];
}

function withRecordedResult(turn: SubAgentTask, stored?: SubAgentTask): SubAgentTask {
  if (recordedResponse(turn) || stored?.id !== turn.id || stored.childSessionId !== turn.childSessionId || !stored.result) return turn;
  return { ...turn, result: stored.result };
}

/** Read the child's existing public ledger; opening a conversation never starts a turn. */
export function SubAgentConversation({ endpoint, turns, onCopyItem, onOpenChatById }: {
  endpoint: string;
  turns: SubAgentTask[];
  onCopyItem?: (item: ConversationItem) => void;
  onOpenChatById?: (chatId: string) => void;
}) {
  const { t } = useTranslation();
  const [timelines, setTimelines] = useState<Record<string, TurnTimeline>>({});
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const sessionId = turns[0]?.childSessionId || "";
  const turnKey = turns.map((turn) => `${turn.id}:${turn.status}`).join("|");
  const polling = turns.some((turn) => RUNNING.has(turn.status));

  useEffect(() => {
    if (!endpoint || !sessionId) return;
    const controller = new AbortController();
    const loaded: Record<string, TurnTimeline> = {};
    const completedReads = new Set<string>();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      setLoading(true);
      try {
        const reads = await Promise.allSettled(turns.filter((turn) => !completedReads.has(turn.id) || RUNNING.has(turn.status)).map(async (turn) => {
          const current: TurnTimeline = { ...(loaded[turn.id] || { events: [], nextSequence: 0 }) };
          // List snapshots can omit a prior turn's result. Its detail owns
          // the persisted command output behind the public timeline rows.
          async function readDetail() {
            if (current.detailChecked || RUNNING.has(turn.status) || recordedResponse(turn)) return;
            try {
              current.detail = (await fetchSubAgent(endpoint, turn.id, controller.signal)).task;
            } catch (cause) {
              if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
            }
            current.detailChecked = true;
          }
          async function readTimeline() {
            let more: boolean;
            do {
              const page = await fetchAgentRunTimeline(endpoint, sessionId, turn.id, current.nextSequence, controller.signal);
              if (page.hasMore && page.nextSequence <= current.nextSequence) throw new Error("Timeline page did not advance.");
              current.events = mergeRuntimeTimelines(current.events, page.events);
              current.nextSequence = page.nextSequence;
              current.run = page.run;
              more = page.hasMore;
            } while (more && !controller.signal.aborted);
          }
          const outcomes = await Promise.allSettled([readTimeline(), readDetail()]);
          loaded[turn.id] = current;
          const failed = outcomes.find(result => result.status === "rejected");
          if (failed) throw failed.reason;
          completedReads.add(turn.id);
        }));
        if (!controller.signal.aborted) {
          const failed = reads.find(result => result.status === "rejected");
          setTimelines({ ...loaded });
          setError(failed ? (failed.reason instanceof Error ? failed.reason.message : String(failed.reason)) : "");
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (polling) timer = setTimeout(() => void read(), 1500);
        }
      }
    }
    void read();
    return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
    // Poll cursors belong to this child and these turn identities, not list-object refreshes.
  }, [endpoint, sessionId, turnKey, refresh, polling]);

  const turnsWithResults = turns.map(turn => withRecordedResult(turn, timelines[turn.id]?.detail));
  const projection = projectConversationSteeringMessages(mergeConversationTimelineItems(
    turnsWithResults.flatMap(turn => turnItems(turn, timelines[turn.id])),
  ));
  const dateSeparators = conversationRoundDateSeparators(projection.items, projection.initialUsersByTurnId);
  return (
    <div className="space-y-6" data-vrcforge-subagent-conversation data-subagent-session={sessionId}>
      {projection.items.map(item => {
        if (item.type !== "agent" && item.type !== "streaming") return null;
        const turn = turnsWithResults.find(task => task.id === item.id);
        const user = projection.initialUsersByTurnId.get(item.id);
        if (!turn) return null;
        const pausedReport = turn.status === "paused"
          ? (typeof turn.result?.finalReport === "string" ? turn.result.finalReport : turn.summary)
          : undefined;
        const response = item.type === "agent" ? item.response : undefined;
        const hasContent = response?.plan.reply || response?.plan.summary || response?.steps?.length || response?.timeline?.length;
        const empty = item.type === "agent" && !hasContent && !pausedReport && !turn.error
          && !loading && turn.status !== "cancelled" && turn.status !== "interrupted";
        return (
          <section key={turn.id} data-subagent-conversation-turn={turn.id}>
            {dateSeparators.has(item) ? <MessageDateSeparator timestamp={dateSeparators.get(item)} /> : null}
            <ConversationCard item={item} onCopyItem={onCopyItem} onOpenChatById={onOpenChatById}
              initialUserContent={user ? <div data-subagent-user-message><ConversationCard item={user} onCopyItem={onCopyItem} /></div> : undefined} />
            {item.type === "streaming" ? <StreamingPhaseStatus item={item} nowMs={Date.now()} showElapsed={false} /> : null}
            {turn.status === "paused" ? (
              <div className="mt-2 space-y-2 px-1 text-chat-body" data-subagent-paused-report>
                <p role="status" className="text-xs text-muted-foreground">
                  {turn.reason === "model_turn_budget_exhausted" ? t("subagent.budgetPaused") : displaySubAgentStatus(turn.status)}
                </p>
                {pausedReport && pausedReport !== response?.plan.reply && pausedReport !== response?.plan.summary
                  ? <ChatMarkdown text={pausedReport} /> : null}
              </div>
            ) : null}
            {turn.error && turn.error !== response?.plan.reply && turn.error !== response?.plan.summary
              ? <ConversationCard item={{ id: `${turn.id}:error`, type: "error", text: turn.error }} /> : null}
            {empty ? <p className="mt-2 px-1 text-xs text-muted-foreground">{t("subagent.noConversation")}</p> : null}
          </section>
        );
      })}
      {loading || (error && polling) ? (
        <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />{t(error && polling ? "header.reconnecting" : "common.loading")}
        </div>
      ) : null}
      {error && !polling && !loading ? <div role="alert" className="text-xs text-muted-foreground">
        <p>{error}</p>
        <Button variant="ghost" className="mt-1 h-7 px-2" onClick={() => setRefresh((value) => value + 1)}><RefreshCw className="h-3 w-3" />{t("common.refresh")}</Button>
      </div> : null}
    </div>
  );
}
