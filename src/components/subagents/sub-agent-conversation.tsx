import { useEffect, useState } from "react";
import { ChevronRight, Code2, Loader2, RefreshCw } from "lucide-react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { fetchAgentRunTimeline, fetchSubAgent, type AgentRuntimeRun, type AgentRuntimeResponse, type SubAgentTask } from "../../lib/api";
import { ApiError } from "../../lib/api/http";
import type { ChatTimelineEvent, ConversationItem } from "../../lib/chat-types";
import { normalizeAgentRuntimePhase } from "../../lib/chat-streaming";
import { projectConversationSteeringMessages } from "../../lib/chat-steering-projection";
import { isFinalAssistantTimelineEvent, materializeRuntimeTimeline, mergeConversationTimelineItems, mergeRuntimeTimelines, projectRuntimeResponseForDisplay, runtimeTerminalStatusKey } from "../../lib/chat-timeline-presentation";
import { conversationRoundDateSeparators } from "../../lib/message-time";
import { displaySubAgentStatus, subAgentTaskIssueText } from "../../lib/subagent-ui";
import { subAgentResultSummaryText } from "../../lib/subagent-merge";
import { copyableAgentDialogueText, formatPayload } from "../../lib/conversation-utils";
import { ConversationCard, MessageDateSeparator, StreamingPhaseStatus } from "../chat/conversation-card";
import { Button } from "../ui/button";
import { OutputBlock } from "../ui/output-block";
import { SubAgentInspectionDetails, subAgentInspectionConclusion } from "./sub-agent-inspection-report";

type TurnTimeline = {
  events: ChatTimelineEvent[];
  nextSequence: number;
  run?: AgentRuntimeRun | null;
  detail?: SubAgentTask;
  detailChecked?: boolean;
};
const RUNNING = new Set(["queued", "running", "cancelling"]);
const BUDGET_STOP = "model_turn_budget_exhausted";

// Older runtime receipts appended instructions for the main chat to the model's reply.
function childBudgetReply(text: string): string {
  return text.replace(/\s*（已暂停：达到本轮显式配置的 \d+ 次模型轮次上限，任务尚未完成。已有进展和会话记录已保留；可在当前聊天发送“继续”接着处理。）\s*$/, "");
}

function recordedResponse(turn: SubAgentTask): AgentRuntimeResponse | undefined {
  const result = turn.result?.rawResult as Partial<AgentRuntimeResponse> | undefined;
  if (turn.childSessionId && (result?.sessionId || result?.session_id) === turn.childSessionId
    && result?.clientTurnId === turn.id && (result.turnId || result.turn_id) && result.plan) {
    return result as AgentRuntimeResponse;
  }
}

export function subAgentConversationModel(turn: SubAgentTask): string {
  return recordedResponse(turn)?.model?.trim() || "";
}

function turnItems(turn: SubAgentTask, t: TFunction, timeline?: TurnTimeline): ConversationItem[] {
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
  const completedAt = recorded?.completedAt || turn.stoppedAt;
  const finalReport = typeof turn.result?.finalReport === "string" ? turn.result.finalReport.trim() : "";
  const pausedReport = turn.status === "paused" && finalReport;
  const legacyBudgetStop = turn.status === "failed" && recorded?.plan.reason === BUDGET_STOP;
  const issueStatus = turn.status === "failed" || turn.status === "cancelled" || turn.status === "interrupted";
  const responseText = pausedReport || recorded?.plan.reply || finalReport || subAgentInspectionConclusion(turn, t)
    || (!issueStatus ? subAgentResultSummaryText(turn) : "");
  const reply = legacyBudgetStop ? childBudgetReply(responseText) : responseText;
  const responseEvents = legacyBudgetStop ? events.flatMap((event) => {
    if (!isFinalAssistantTimelineEvent(event) || !event.payload.summary) return [event];
    const summary = childBudgetReply(event.payload.summary);
    return summary ? [{ ...event, payload: { ...event.payload, summary } }] : [];
  }) : events;
  const responseTimeline = pausedReport && events.length ? [
    ...events.filter((event) => !isFinalAssistantTimelineEvent(event)),
    {
      id: `${turn.id}:paused-report`, sequence: events.length, timestamp: completedAt || "",
      kind: "assistant" as const, payload: { phase: "final_answer", summary: pausedReport },
    },
  ] : responseEvents;
  const elapsed = (Date.parse(completedAt || "") - Date.parse(startedAt || "")) / 1000;
  const response: AgentRuntimeResponse = {
    ok: turn.status === "completed",
    sessionId: turn.childSessionId || "", session_id: turn.childSessionId || "",
    turnId: ledger?.turnId || "", turn_id: ledger?.turnId || "",
    clientTurnId: turn.id,
    observe: {},
    ...recorded,
    plan: { summary: "", planner: "", shellNeeded: false, ...recorded?.plan, reply },
    startedAt, completedAt, timeline: responseTimeline,
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

export function subAgentConversationCopyItem(turn: SubAgentTask, t: TFunction): ConversationItem | undefined {
  const item = turnItems(turn, t)[1];
  if (item.type !== "agent") return;
  const response = projectRuntimeResponseForDisplay(item.response, t);
  if (copyableAgentDialogueText(response)) return { ...item, response };
}

function TurnTechnicalDetails({ turn, readError }: { turn: SubAgentTask; readError?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const panelId = `subagent-technical-${turn.id}`;
  return (
    <div className="px-1">
      <Button type="button" variant="ghost" className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
        aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)} data-subagent-technical-details>
        <ChevronRight className={`h-3.5 w-3.5 ${open ? "rotate-90" : ""}`} aria-hidden="true" />
        <Code2 className="h-3.5 w-3.5" aria-hidden="true" />
        {t("subagent.technicalDetails")}
      </Button>
      <div id={panelId} hidden={!open} className="mt-1.5 space-y-2">
        {open && (turn.error || turn.errorCode || readError) ? <OutputBlock label={t("subagent.errorDetails")}
          value={[turn.errorCode, turn.error, readError].filter(Boolean).join("\n")} /> : null}
        {open && turn.result ? <OutputBlock label={t("subagent.result")} value={formatPayload(turn.result)} /> : null}
        {open && turn.events?.length ? <OutputBlock label={t("subagent.eventHistory")} value={formatPayload(turn.events)} /> : null}
        {open && turn.parentContinuationStatus ? <div className="text-xs text-muted-foreground">
          {t("subagent.parentContinuation")}: {turn.parentContinuationStatus}
        </div> : null}
      </div>
    </div>
  );
}

/** Read the child's existing public ledger; opening a conversation never starts a turn. */
export function SubAgentConversation({ endpoint, turns, developerOptionsEnabled = false, onCopyItem, onOpenChatById }: {
  endpoint: string;
  turns: SubAgentTask[];
  developerOptionsEnabled?: boolean;
  onCopyItem?: (item: ConversationItem) => void;
  onOpenChatById?: (chatId: string) => void;
}) {
  const { t } = useTranslation();
  const [timelines, setTimelines] = useState<Record<string, TurnTimeline>>({});
  const [readError, setReadError] = useState<{ turnId: string; message: string } | null>(null);
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
        const pendingTurns = turns.filter((turn) => !completedReads.has(turn.id) || RUNNING.has(turn.status));
        const reads = await Promise.allSettled(pendingTurns.map(async (turn) => {
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
          const failedIndex = reads.findIndex(result => result.status === "rejected");
          const failed = reads[failedIndex];
          setTimelines({ ...loaded });
          setReadError(failed?.status === "rejected" ? {
            turnId: pendingTurns[failedIndex].id,
            message: failed.reason instanceof Error ? failed.reason.message : String(failed.reason),
          } : null);
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
    turnsWithResults.flatMap(turn => turnItems(turn, t, timelines[turn.id])),
  ));
  const dateSeparators = conversationRoundDateSeparators(projection.items, projection.initialUsersByTurnId);
  return (
    <div className="space-y-6" data-vrcforge-subagent-conversation data-subagent-session={sessionId}>
      {projection.items.map(item => {
        if (item.type !== "agent" && item.type !== "streaming") return null;
        const turn = turnsWithResults.find(task => task.id === item.id);
        const user = projection.initialUsersByTurnId.get(item.id);
        if (!turn) return null;
        const response = item.type === "agent" ? item.response : undefined;
        const budgetStop = turn.reason === BUDGET_STOP || response?.plan.reason === BUDGET_STOP;
        const hasContent = response?.plan.reply || response?.plan.summary || response?.steps?.length || response?.timeline?.length;
        const failureInAnswer = runtimeTerminalStatusKey(response?.plan.plannerFailure?.code)
          || (turn.role === "outfit_package_inspection" && subAgentInspectionConclusion(turn, t));
        const errorText = turn.resultUnavailable || (
          !failureInAnswer && !budgetStop && turn.status !== "cancelled" && turn.status !== "interrupted"
        ) ? subAgentTaskIssueText(turn) : "";
        const turnReadError = readError?.turnId === turn.id ? readError.message : undefined;
        const empty = item.type === "agent" && !hasContent && !errorText && !budgetStop
          && !loading && turn.status !== "cancelled" && turn.status !== "interrupted";
        const supplement = response ? (
          <>
            <SubAgentInspectionDetails turn={turn} />
            {turn.status === "paused" || budgetStop ? (
              <p role="status" className="px-1 text-xs text-muted-foreground" data-subagent-paused-report>
                {budgetStop ? t("subagent.budgetPaused") : displaySubAgentStatus(turn.status)}
              </p>
            ) : null}
            {errorText ? <p role="status" className="break-words px-1 text-chat-body text-destructive">{errorText}</p> : null}
            {empty ? <p className="px-1 text-xs text-muted-foreground">{t("subagent.noConversation")}</p> : null}
          </>
        ) : undefined;
        return (
          <section key={turn.id} data-subagent-conversation-turn={turn.id}>
            {dateSeparators.has(item) ? <MessageDateSeparator timestamp={dateSeparators.get(item)} /> : null}
            <ConversationCard item={item} onCopyItem={onCopyItem} onOpenChatById={onOpenChatById} agentReplySupplement={supplement} compactAgentReply developerOptionsEnabled={developerOptionsEnabled}
              initialUserContent={user ? <div data-subagent-user-message><ConversationCard item={user} onCopyItem={onCopyItem} /></div> : undefined} />
            {item.type === "streaming" ? <StreamingPhaseStatus item={item} nowMs={Date.now()} showElapsed={false} /> : null}
            {developerOptionsEnabled && (turn.result || turn.events?.length || turn.parentContinuationStatus || turn.error || turn.errorCode || turnReadError)
              ? <TurnTechnicalDetails turn={turn} readError={turnReadError} /> : null}
          </section>
        );
      })}
      {loading || (readError && polling) ? (
        <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />{t(readError && polling ? "header.reconnecting" : "common.loading")}
        </div>
      ) : null}
      {readError && !polling && !loading ? <div role="alert" className="text-xs text-muted-foreground">
        <p>{t("subagent.conversationLoadFailed")}</p>
        <Button variant="ghost" className="mt-1 h-7 px-2" onClick={() => setRefresh((value) => value + 1)}><RefreshCw className="h-3 w-3" />{t("common.refresh")}</Button>
      </div> : null}
    </div>
  );
}
