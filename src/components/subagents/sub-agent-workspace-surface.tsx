import { Ban, Check, Loader2, ArrowLeft, ArrowUp, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api";
import { elapsedSecondsSince } from "../../lib/chat-streaming";
import type { ConversationItem } from "../../lib/chat-types";
import { canAdoptSubAgentResult, canCancelSubAgentTask, canDismissSubAgentResult, canResumeSubAgentTask } from "../../lib/subagent-merge";
import { displaySubAgentStatus, isActiveSubAgentStatus, subAgentStatusTone } from "../../lib/subagent-ui";
import { groupSubAgentThreads } from "../../lib/subagent-threads";
import { Badge } from "../ui/badge";
import { MessageCopyButton } from "../chat/message-copy-button";
import { Button } from "../ui/button";
import { SubAgentConversation, subAgentConversationCopyItem, subAgentConversationModel, type SubAgentTimelineCache } from "./sub-agent-conversation";
import { subAgentInspectionConclusion } from "./sub-agent-inspection-report";
import { SubAgentMark } from "./sub-agent-panel";
import { useConversationScroll } from "../../hooks/use-conversation-scroll";

export function SubAgentWorkspaceSurface({ tasks, selected, endpoint = "", developerOptionsEnabled = false,
  onCancel, onResume, onMerge, busyTaskIds, onClose, onCloseSidebar, onCopyItem, onOpenChatById, emptyMessage, timelineCache }: {
  tasks: SubAgentTask[];
  selected: SubAgentTask | null;
  emptyMessage?: string;
  timelineCache?: SubAgentTimelineCache;
  endpoint?: string;
  developerOptionsEnabled?: boolean;
  onCancel: (taskId: string) => void;
  onResume: (taskId: string, question: string) => Promise<boolean>;
  onMerge: (task: SubAgentTask, decision: "adopted" | "dismissed") => void;
  busyTaskIds: ReadonlySet<string>;
  onClose: () => void;
  onCloseSidebar?: () => void;
  onCopyItem?: (item: ConversationItem) => Promise<boolean>;
  onOpenChatById?: (chatId: string) => void;
}) {
  const { t } = useTranslation();
  const [resumeQuestion, setResumeQuestion] = useState("");
  const [resumeSubmitting, setResumeSubmitting] = useState(false);
  const [readModel, setReadModel] = useState<{ taskId: string; model: string }>();
  const recordReadModel = useCallback((taskId: string, model: string) => setReadModel({ taskId, model }), []);
  const threads = useMemo(() => groupSubAgentThreads(tasks), [tasks]);
  const activeThread = threads.find((thread) => thread.turns.some((turn) => turn.id === selected?.id));
  const activeTask = activeThread ? selected?.id === activeThread.task.id ? selected : activeThread.task : null;
  const activeModel = activeTask && (subAgentConversationModel(activeTask)
    || (readModel?.taskId === activeTask.id ? readModel.model : ""));
  const localExecution = activeTask?.handler && activeTask.handler !== "llm_readonly" && !activeTask.childSessionId;
  const executionLabel = activeModel || t(localExecution ? "subagent.modelNotUsed"
    : activeTask && isActiveSubAgentStatus(activeTask.status) ? "subagent.modelPending" : "subagent.modelNotRecorded");
  const end = Date.parse(activeTask?.stoppedAt || "");
  const elapsed = activeTask && !activeTask.childSessionId && !subAgentInspectionConclusion(activeTask, t) && Number.isFinite(end)
    ? elapsedSecondsSince(activeTask.startedAt, end) : undefined;
  const copyItem = useMemo(() => activeTask && onCopyItem ? subAgentConversationCopyItem(activeTask, t) : undefined, [activeTask, onCopyItem, t]);
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const { onScroll } = useConversationScroll({ chatKey: activeThread?.id || "", endRef: conversationEndRef });
  const canCancel = activeTask ? canCancelSubAgentTask(activeTask) : false;
  const canResume = activeTask ? canResumeSubAgentTask(activeTask) : false;
  const canAdopt = activeTask ? canAdoptSubAgentResult(activeTask) : false;
  const canDismiss = activeTask ? canDismissSubAgentResult(activeTask) : false;
  const actionBusy = activeTask ? busyTaskIds.has(activeTask.id) : false;

  useEffect(() => setResumeQuestion(""), [activeTask?.id]);
  async function submitResume(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const question = resumeQuestion.trim();
    if (!activeTask || !canResume || !question || actionBusy || resumeSubmitting) return;
    setResumeSubmitting(true);
    try { if (await onResume(activeTask.id, question)) setResumeQuestion(""); }
    finally { setResumeSubmitting(false); }
  }

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden bg-transparent" data-vrcforge-subagent-workspace-surface>
      <header className="shrink-0 border-b border-border px-3 py-2">
        <div className="flex items-center gap-2">
          <Button type="button" variant="ghost" className="h-7 w-7 shrink-0 p-0" onClick={onClose}
            data-app-tooltip={t("subagent.backToList")} aria-label={t("subagent.backToList")} data-vrcforge-subagent-back>
            <ArrowLeft className="h-3.5 w-3.5" />
          </Button>
          {activeThread ? <SubAgentMark threadId={activeThread.id} /> : null}
          <span className="min-w-0 flex-1 truncate text-sm font-medium">{activeTask?.displayName || t("subagent.conversation")}</span>
          {elapsed !== undefined ? <span className="shrink-0 text-xs tabular-nums text-muted-foreground" data-subagent-duration>
            {t("agent.elapsed", { time: `${elapsed}s` })}
          </span> : null}
          {activeTask ? <Badge tone={subAgentStatusTone(activeTask.status)}>{displaySubAgentStatus(activeTask.status)}</Badge> : null}
          {copyItem && onCopyItem ? <MessageCopyButton key={copyItem.id} onCopy={() => onCopyItem(copyItem)} data-subagent-copy-reply /> : null}
          {onCloseSidebar ? <Button type="button" variant="ghost" className="h-7 w-7 shrink-0 p-0" onClick={onCloseSidebar}
            aria-label={t("common.close")} data-app-tooltip={t("common.close")} data-vrcforge-close-subagent-sidebar>
            <X className="h-3.5 w-3.5" />
          </Button> : null}
        </div>
        {activeTask ? <div className="flex flex-wrap gap-x-3 gap-y-1 pl-9 text-xs text-muted-foreground">
          <span className="min-w-0 [overflow-wrap:anywhere]" data-subagent-model>
            {t("subagent.modelValue", { model: executionLabel })}
          </span>
          {localExecution && activeTask.parentModel ? <span className="min-w-0 [overflow-wrap:anywhere]" data-subagent-parent-model>
            {t("subagent.parentModelValue", { model: activeTask.parentModel })}
          </span> : null}
        </div> : null}
      </header>

      {activeTask && activeThread ? <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {activeTask.mergeDecision ? <div className="flex items-center gap-2 px-5 py-2 text-xs">
          <Badge tone="muted">{t(activeTask.mergeDecision === "adopted" ? "subagent.mergedBadge" : "subagent.dismissedBadge")}</Badge>
        </div> : null}
        <div className="app-scrollbar min-h-0 flex-1 overflow-auto pl-7 pr-4 py-5 md:pl-8 md:pr-6 md:py-6" data-vrcforge-subagent-conversation-scroll data-chat-history-scroll onScroll={(event) => onScroll(event.currentTarget)}>
          <div className="mx-auto max-w-3xl space-y-6">
          <SubAgentConversation key={activeThread.id} endpoint={endpoint} developerOptionsEnabled={developerOptionsEnabled} onCopyItem={onCopyItem} onOpenChatById={onOpenChatById}
            timelineCache={timelineCache} modelTaskId={activeTask.id} onModelChange={recordReadModel}
            turns={activeThread.turns.map((turn) => turn.id === activeTask.id ? activeTask : turn)} />
          <div ref={conversationEndRef} />
          </div>
        </div>
          {canResume || canCancel || canAdopt || canDismiss ? <div className="shrink-0 px-4 pb-3 pt-2">
            {canResume && activeTask ? (
              <form className="chat-composer-shell mx-auto mb-2 w-full max-w-3xl" onSubmit={(event) => void submitResume(event)}>
                <div className="chat-composer-frame p-3">
                <label htmlFor="subagent-resume-question" className="sr-only">
                  {t("subagent.resumeQuestionLabel")}
                </label>
                <textarea
                  id="subagent-resume-question"
                  className="app-scrollbar min-h-20 w-full resize-none bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
                  value={resumeQuestion}
                  onChange={(event) => setResumeQuestion(event.target.value)}
                  placeholder={t("subagent.resumeQuestionPlaceholder")}
                  required
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault();
                      void submitResume();
                    }
                  }}
                  disabled={actionBusy || resumeSubmitting}
                  data-vrcforge-subagent-resume-question
                />
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span className="text-[11px] text-muted-foreground">{t("subagent.resumeQuestionHint")}</span>
                  <Button
                    type="submit"
                    variant="primary"
                    className="h-8 w-8 shrink-0 rounded-full p-0"
                    data-app-tooltip={t("subagent.resume")}
                    aria-label={t("subagent.resume")}
                    disabled={actionBusy || resumeSubmitting || !resumeQuestion.trim()}
                    data-vrcforge-subagent-resume
                  >
                    {resumeSubmitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ArrowUp className="h-4 w-4" />}
                  </Button>
                </div>
                </div>
              </form>
            ) : null}
            <div className="flex flex-wrap justify-end gap-2">
              {canCancel && activeTask ? (
                <Button type="button" variant="ghost" className="h-7 px-2 text-xs" disabled={actionBusy} onClick={() => onCancel(activeTask.id)}>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {t("subagent.cancel")}
                </Button>
              ) : null}
              {canAdopt && activeTask ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    disabled={actionBusy}
                    onClick={() => onMerge(activeTask, "adopted")}
                  >
                    <Check className="h-3.5 w-3.5" />
                    {t("subagent.mergeAdopt")}
                  </Button>
              ) : null}
              {canDismiss && activeTask ? (
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    disabled={actionBusy}
                    onClick={() => onMerge(activeTask, "dismissed")}
                  >
                    <Ban className="h-3.5 w-3.5" />
                    {t("subagent.mergeDismiss")}
                  </Button>
              ) : null}
            </div>
          </div> : null}
      </section> : <div className="p-4 text-xs text-muted-foreground">{emptyMessage ?? t("subagent.noTasks")}</div>}
    </section>
  );
}
