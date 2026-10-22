import { ChevronLeft, ChevronRight, HelpCircle, Pencil, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentQuestion } from "../../lib/api";
import { cn, formatCount } from "../../lib/utils";
import { questionContinuationDisplay } from "../../hooks/use-runtime-turn-continuation";

export function AgentQuestionCard({
  questions,
  onAnswerQuestion,
  onStopContinuation,
  onReplyInChat,
}: {
  questions: AgentQuestion[];
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
  onStopContinuation?: (question: AgentQuestion) => void | Promise<void>;
  /** Focus the shared composer; the owner should retain this question id and send text/attachments through the normal composer path. */
  onReplyInChat?: (questionId: string) => void;
}) {
  const { t } = useTranslation();
  const pendingQuestions = useMemo(
    () => questions.filter((question) => (question.status || "pending").toLowerCase() === "pending"),
    [questions],
  );
  const [index, setIndex] = useState(0);
  const [customValues, setCustomValues] = useState<Record<string, string>>({});
  const [checkpointNotes, setCheckpointNotes] = useState<Record<string, string>>({});
  const [busyChoice, setBusyChoice] = useState("");
  const [questionErrors, setQuestionErrors] = useState<Record<string, string>>({});
  const [retryAnswers, setRetryAnswers] = useState<Record<string, { optionId: string; value: string }>>({});

  useEffect(() => {
    setIndex((current) => Math.min(current, Math.max(0, pendingQuestions.length - 1)));
  }, [pendingQuestions.length]);

  const question = pendingQuestions[index];
  if (!question) {
    return null;
  }

  const options = question.options || [];
  const continuationStatus = String(question.runtimeContinuation?.status || "").toLowerCase();
  const continuationDisplay = questionContinuationDisplay(question);
  const continuationActive = continuationDisplay === "active";
  const continuationSettled = continuationDisplay === "settled" && Boolean(question.runtimeContinuation);
  const customValue = customValues[question.questionId] || "";
  const isVisualCheckpoint = question.kind === "visual_checkpoint";
  const checkpointNote = checkpointNotes[question.questionId] || "";
  const optionLabel = (option: NonNullable<AgentQuestion["options"]>[number]) => isVisualCheckpoint
    ? t(`questionCard.visualCheckpoint.options.${option.id.toLowerCase()}`, option.label)
    : option.label;
  const optionAnswer = (option: NonNullable<AgentQuestion["options"]>[number]) => {
    const label = optionLabel(option);
    const note = checkpointNote.trim();
    return note ? `${label}\n\n${note}` : label;
  };
  const answer = async (optionId: string, value: string) => {
    if (!value.trim() && optionId !== "skip") {
      return;
    }
    setBusyChoice(optionId);
    setQuestionErrors((current) => ({ ...current, [question.questionId]: "" }));
    setRetryAnswers((current) => ({ ...current, [question.questionId]: { optionId, value } }));
    try {
      await onAnswerQuestion(question.questionId, optionId, value);
      setCustomValues((current) => ({ ...current, [question.questionId]: "" }));
      setRetryAnswers((current) => { const next = { ...current }; delete next[question.questionId]; return next; });
    } catch (cause) {
      setQuestionErrors((current) => ({ ...current, [question.questionId]: cause instanceof Error ? cause.message : String(cause) }));
    } finally {
      setBusyChoice("");
    }
  };

  return (
    <section className="app-scrollbar max-h-[48vh] overflow-y-auto rounded-2xl border border-border bg-card p-4" aria-label={t("questionCard.label")}>
      <div className="mb-1.5 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <HelpCircle className="h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1 font-normal">{t("questionCard.title")}</span>
        {onReplyInChat ? (
          <button
            type="button"
            className="shrink-0 rounded-md px-1 py-0.5 text-xs text-muted-foreground underline-offset-2 hover:bg-muted hover:text-foreground hover:underline disabled:opacity-60"
            onClick={() => onReplyInChat(question.questionId)}
            disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
            data-question-reply-in-chat={question.questionId}
            aria-label={t("questionCard.replyInChat")}
          >
            <Pencil className="h-4 w-4" aria-label={t("questionCard.replyInChat")} />
          </button>
        ) : null}
        {pendingQuestions.length > 1 ? (
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-muted disabled:opacity-40"
              onClick={() => setIndex((current) => Math.max(0, current - 1))}
              disabled={index === 0 || Boolean(busyChoice)}
              aria-label={t("questionCard.previous")}
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="tabular-nums">{t("questionCard.position", { current: index + 1, total: pendingQuestions.length })}</span>
            <button
              type="button"
              className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-muted disabled:opacity-40"
              onClick={() => setIndex((current) => Math.min(pendingQuestions.length - 1, current + 1))}
              disabled={index >= pendingQuestions.length - 1 || Boolean(busyChoice)}
              aria-label={t("questionCard.next")}
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        ) : null}
        <button type="button" className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-muted disabled:opacity-50" aria-label={t("common.close")} title={t("questionCard.skip")} onClick={() => void answer("skip", t("questionCard.skipAnswer"))} disabled={Boolean(busyChoice) || continuationActive || continuationSettled}><X className="h-4 w-4" /></button>
      </div>

      {!continuationActive && !continuationSettled ? <div className="app-scrollbar mb-2 max-h-[20vh] overflow-y-auto whitespace-pre-wrap break-words text-sm font-normal text-foreground">
        {question.question || question.questionId}
      </div> : null}

      {continuationActive || continuationSettled ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-border bg-muted/40 px-2.5 py-2 text-xs" role="status">
          <span className="min-w-0 flex-1">
            {continuationStatus === "cancelling"
              ? t("questionCard.continuationCancelling", "Stopping…")
              : continuationStatus === "blocked"
                ? t("questionCard.continuationBlocked", "Waiting for the next action")
                : continuationActive
              ? t("questionCard.continuationRunning", "Continuing this task…")
              : continuationStatus === "completed"
                ? t("questionCard.continuationCompleted", "Continuation completed")
                : continuationStatus === "cancelled"
                  ? t("questionCard.continuationCancelled", "Continuation stopped")
                  : continuationStatus === "delivered"
                    ? t("questionCard.continuationDelivered", "Continuation result delivered; inspect the transcript")
                  : question.runtimeContinuation?.error || t("questionCard.continuationFailed", "Continuation failed")}
          </span>
          {continuationActive && onStopContinuation ? (
            <button
              type="button"
              className="shrink-0 rounded-md border border-border px-2 py-1 font-medium hover:bg-muted disabled:opacity-60"
              onClick={() => void onStopContinuation(question)}
              disabled={continuationStatus === "cancelling" || (!question.runtimeContinuation?.turnId && !question.runtimeContinuation?.clientTurnId)}
            >
              {question.runtimeContinuation?.turnId || question.runtimeContinuation?.clientTurnId
                ? t("questionCard.stopContinuation", "Stop")
                : t("questionCard.continuationWaiting", "Waiting…")}
            </button>
          ) : null}
        </div>
      ) : null}

      {questionErrors[question.questionId] ? (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-2.5 py-2 text-xs text-destructive" role="alert">
          <span className="min-w-0 flex-1 break-words">{questionErrors[question.questionId]}</span>
          <button type="button" className="shrink-0 rounded-md border border-destructive/40 px-2 py-1 font-medium hover:bg-destructive/10 disabled:opacity-60" onClick={() => { const retry = retryAnswers[question.questionId]; if (retry) void answer(retry.optionId, retry.value); }} disabled={Boolean(busyChoice)}>
            {t("questionCard.retry")}
          </button>
        </div>
      ) : null}

      {!continuationActive && !continuationSettled ? <><div className="grid gap-1.5">
        <div className="app-scrollbar grid max-h-48 gap-1 overflow-y-auto pr-1">
        {options.map((option, optionIndex) => {
          const value = isVisualCheckpoint ? optionAnswer(option) : option.value || option.label;
          const busy = busyChoice === option.id;
          return (
            <button
              key={option.id}
              type="button"
              className="grid min-w-0 grid-cols-[20px_minmax(0,1fr)] items-center gap-2 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-muted/50 disabled:opacity-60"
              onClick={() => void answer(option.id, value)}
              disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
              title={option.description || option.label}
            >
              <span className="flex h-5 w-5 items-center justify-center rounded border border-border bg-muted/40 text-xs tabular-nums text-muted-foreground">
                {formatCount(optionIndex + 1)}
              </span>
              <span className="min-w-0">
                <span className={cn("flex min-w-0 items-center gap-2 text-sm font-normal", busy && "text-muted-foreground")}>
                  <span className="min-w-0 whitespace-pre-wrap break-words">{optionLabel(option)}</span>
                </span>
                {option.description ? <span className="block whitespace-pre-wrap break-words text-xs text-muted-foreground">{option.description}</span> : null}
              </span>
            </button>
          );
        })}
        </div>

        {isVisualCheckpoint ? (
          <textarea
            value={checkpointNote}
            onChange={(event) => setCheckpointNotes((current) => ({ ...current, [question.questionId]: event.target.value.slice(0, 2000) }))}
            maxLength={2000}
            rows={2}
            className="app-scrollbar min-w-0 max-h-24 resize-y rounded-lg border border-border bg-background px-2 py-1.5 text-sm outline-none placeholder:text-muted-foreground"
            placeholder={t("questionCard.visualCheckpoint.notePlaceholder")}
            aria-label={t("questionCard.visualCheckpoint.notePlaceholder")}
            disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
          />
        ) : null}

          {!isVisualCheckpoint ? <form
            className="mt-2 flex min-w-0 items-center gap-2 px-1 py-1"
            onSubmit={(event) => {
              event.preventDefault();
              void answer("custom", customValue.trim());
            }}
          >
            <textarea
              value={customValue}
              onChange={(event) => setCustomValues((current) => ({ ...current, [question.questionId]: event.target.value.slice(0, 2000) }))}
              maxLength={2000}
              rows={1}
              className="app-scrollbar h-6 min-w-0 flex-1 resize-none bg-transparent text-base leading-6 outline-none placeholder:text-muted-foreground"
              placeholder={t("questionCard.customPlaceholder")}
              aria-label={t("questionCard.customPlaceholder")}
              disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
            />
            <div className="flex shrink-0 items-center justify-end gap-2">
              <button
                type="button"
                className="rounded-full border border-border px-2.5 py-1 text-sm hover:bg-muted disabled:opacity-60"
                onClick={() => void answer("skip", t("questionCard.skipAnswer"))}
                disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
              >
                {t("questionCard.skip")}
              </button>
              <button
                type="submit"
                className="rounded-full bg-primary px-3 py-1 text-sm font-medium text-primary-foreground disabled:bg-muted-foreground/50"
              disabled={!customValue.trim() || Boolean(busyChoice) || continuationActive || continuationSettled}
              >
                {t("chat.send")}
              </button>
            </div>
          </form> : null}
      </div>

      {isVisualCheckpoint ? <div className="mt-2 flex justify-end">
        <button
          type="button"
          className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-60"
          onClick={() => void answer("skip", t("questionCard.skipAnswer"))}
          disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
        >
          {t("questionCard.skip")}
        </button>
      </div> : null}</> : null}
    </section>
  );
}
