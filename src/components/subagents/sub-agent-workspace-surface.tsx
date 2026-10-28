import { formatPayload } from "../../lib/conversation-utils";
import { Ban, BellOff, Check, CornerDownRight, Loader2, RefreshCw, ArrowLeft, ArrowUp } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import type { SubAgentRole, SubAgentTask } from "../../lib/api";
import { canAdoptSubAgentResult, canCancelSubAgentTask, canDismissSubAgentResult, canResumeSubAgentTask,
  canRetrySubAgentTask, isAwaitingMergeReview, subAgentProposedNextAction } from "../../lib/subagent-merge";
import { displaySubAgentStatus, subAgentRoleLabel, subAgentStatusTone } from "../../lib/subagent-ui";
import { groupSubAgentThreads } from "../../lib/subagent-threads";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { OutputBlock } from "../ui/output-block";
import { SubAgentConversation } from "./sub-agent-conversation";
import { useConversationScroll } from "../../hooks/use-conversation-scroll";

export function SubAgentWorkspaceSurface({ tasks, roles = [], selected, endpoint = "", developerOptionsEnabled = false,
  onCancel, onRetry, onResume, onMerge, onIgnoreNotification, onAdoptNextAction, busyTaskIds, onClose }: {
  tasks: SubAgentTask[];
  roles?: SubAgentRole[];
  selected: SubAgentTask | null;
  endpoint?: string;
  developerOptionsEnabled?: boolean;
  onCancel: (taskId: string) => void;
  onRetry: (taskId: string) => void;
  onResume: (taskId: string, question: string) => Promise<boolean>;
  onMerge: (task: SubAgentTask, decision: "adopted" | "dismissed") => void;
  onIgnoreNotification: (task: SubAgentTask) => void;
  onAdoptNextAction: (task: SubAgentTask) => void;
  busyTaskIds: ReadonlySet<string>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [resumeQuestion, setResumeQuestion] = useState("");
  const [resumeSubmitting, setResumeSubmitting] = useState(false);
  const threads = useMemo(() => groupSubAgentThreads(tasks), [tasks]);
  const activeThread = threads.find((thread) => thread.turns.some((turn) => turn.id === selected?.id)) || threads[0];
  const activeTask = activeThread ? selected?.id === activeThread.task.id ? selected : activeThread.task : null;
  const conversationEndRef = useRef<HTMLDivElement>(null);
  const { onScroll } = useConversationScroll({ chatKey: activeThread?.id || "", endRef: conversationEndRef });
  const activeTaskNextAction = activeTask ? subAgentProposedNextAction(activeTask) : "";
  const canCancel = activeTask ? canCancelSubAgentTask(activeTask) : false;
  const canRetry = activeTask ? canRetrySubAgentTask(activeTask) : false;
  const canResume = activeTask ? canResumeSubAgentTask(activeTask) : false;
  const canAdopt = activeTask ? canAdoptSubAgentResult(activeTask) : false;
  const canDismiss = activeTask ? canDismissSubAgentResult(activeTask) : false;
  const canIgnoreNotification = activeTask ? isAwaitingMergeReview(activeTask) : false;
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
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Button type="button" variant="ghost" className="h-7 w-7 shrink-0 p-0" onClick={onClose}
          title={t("subagent.backToList")} aria-label={t("subagent.backToList")} data-vrcforge-subagent-back>
          <ArrowLeft className="h-3.5 w-3.5" />
        </Button>
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{activeTask?.displayName || t("subagent.conversation")}</span>
        {activeTask ? <span className="truncate text-xs text-muted-foreground">{subAgentRoleLabel(activeTask.role, roles)}</span> : null}
        {activeTask ? <Badge tone={subAgentStatusTone(activeTask.status)}>{displaySubAgentStatus(activeTask.status)}</Badge> : null}
      </header>

      {activeTask && activeThread ? <section className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {activeTask.mergeDecision || activeTask.parentContinuationStatus ? <div className="flex items-center gap-2 px-5 py-2 text-xs">
          {activeTask.mergeDecision ? <Badge tone="muted">{t(activeTask.mergeDecision === "adopted" ? "subagent.mergedBadge" : "subagent.dismissedBadge")}</Badge> : null}
          {activeTask.parentContinuationStatus ? <span className="text-muted-foreground">{t("subagent.parentContinuation")}: {activeTask.parentContinuationStatus}</span> : null}
        </div> : null}
        <div className="app-scrollbar min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-6" data-vrcforge-subagent-conversation-scroll data-chat-history-scroll onScroll={(event) => onScroll(event.currentTarget)}>
          <div className="mx-auto w-full max-w-3xl space-y-4">
          <SubAgentConversation key={activeThread.id} endpoint={endpoint}
            turns={activeThread.turns.map((turn) => turn.id === activeTask.id ? activeTask : turn)} />
          {activeTask.resultUnavailable ? <OutputBlock label={t("subagent.result")} value={t("subagent.resultUnavailable")} danger /> : null}
          {!activeTask.childSessionId && activeTask.result ? <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">{t("subagent.result")}</summary>
            <OutputBlock label={t("subagent.result")} value={formatPayload(activeTask.result)} />
          </details> : null}
          {developerOptionsEnabled ? <details className="text-xs"><summary className="cursor-pointer text-muted-foreground">{t("subagent.eventHistory")}</summary>
            <OutputBlock label={t("subagent.eventHistory")} value={formatPayload(activeTask.events || [])} />
          </details> : null}
          <div ref={conversationEndRef} />
          </div>
        </div>
          <div className="sticky bottom-0 px-4 pb-3 pt-2">
            {canResume && activeTask ? (
              <form className="chat-composer-shell mx-auto mb-2 w-full max-w-3xl" onSubmit={(event) => void submitResume(event)}>
                <div className="chat-composer-frame p-3">
                <label htmlFor="subagent-resume-question" className="sr-only">
                  {t("subagent.resumeQuestionLabel")}
                </label>
                <textarea
                  id="subagent-resume-question"
                  className="app-scrollbar min-h-20 w-full resize-none bg-transparent px-1 text-base leading-relaxed outline-none placeholder:text-muted-foreground"
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
                    title={t("subagent.resume")}
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
              {canRetry && activeTask ? (
                <Button type="button" variant="ghost" className="h-7 px-2 text-xs" disabled={actionBusy} onClick={() => onRetry(activeTask.id)}>
                  <RefreshCw className="h-3.5 w-3.5" />
                  {t("subagent.retryNewTask")}
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
              {canIgnoreNotification && activeTask ? (
                <Button
                  type="button"
                  variant="ghost"
                  className="h-7 px-2 text-xs"
                  onClick={() => onIgnoreNotification(activeTask)}
                >
                  <BellOff className="h-3.5 w-3.5" />
                  {t("subagent.ignoreNotification")}
                </Button>
              ) : null}
              {activeTaskNextAction && activeTask ? (
                <Button
                  type="button"
                  variant="ghost"
                  className="h-7 px-2 text-xs"
                  onClick={() => onAdoptNextAction(activeTask)}
                >
                  <CornerDownRight className="h-3.5 w-3.5" />
                  {t("subagent.adoptNextAction")}
                </Button>
              ) : null}
            </div>
          </div>
      </section> : <div className="p-4 text-xs text-muted-foreground">{t("subagent.noTasks")}</div>}
    </section>
  );
}
