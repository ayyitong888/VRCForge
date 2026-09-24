import { ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentQuestion } from "../../lib/api";
import { cn, formatCount } from "../../lib/utils";
import { questionContinuationDisplay } from "../../hooks/use-runtime-turn-continuation";

export function AgentQuestionCard({
  questions,
  onAnswerQuestion,
  onStopContinuation,
}: {
  questions: AgentQuestion[];
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
  onStopContinuation?: (question: AgentQuestion) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const pendingQuestions = useMemo(
    () => questions.filter((question) => (question.status || "pending").toLowerCase() === "pending"
      || Boolean(question.runtimeContinuation)),
    [questions],
  );
  const [index, setIndex] = useState(0);
  const [customValues, setCustomValues] = useState<Record<string, string>>({});
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
    <section className="app-scrollbar max-h-[70vh] overflow-y-auto rounded-2xl border border-border bg-card p-3 shadow-sm" aria-label={t("questionCard.label")}>
      <div className="mb-2 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 whitespace-pre-wrap break-words font-medium text-foreground">{question.header || t("questionCard.title")}</span>
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
      </div>

      {!continuationActive && !continuationSettled ? <div className="app-scrollbar mb-3 max-h-[28vh] overflow-y-auto whitespace-pre-wrap break-words text-sm font-medium text-foreground">
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
        <div className="app-scrollbar grid max-h-64 gap-1.5 overflow-y-auto pr-1">
        {options.map((option, optionIndex) => {
          const value = option.value || option.label;
          const busy = busyChoice === option.id;
          return (
            <button
              key={option.id}
              type="button"
              className="grid min-w-0 grid-cols-[32px_minmax(0,1fr)] items-center gap-2 rounded-xl bg-muted/60 px-2.5 py-2 text-left transition-colors hover:bg-muted disabled:opacity-60"
              onClick={() => void answer(option.id, value)}
              disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
              title={option.description || option.label}
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-background text-sm font-semibold text-muted-foreground">
                {formatCount(optionIndex + 1)}
              </span>
              <span className="min-w-0">
                <span className={cn("flex min-w-0 items-center gap-2 text-sm font-medium", busy && "text-muted-foreground")}>
                  <span className="min-w-0 whitespace-pre-wrap break-words">{option.label}</span>
                  {optionIndex === 0 ? (
                    <span className="shrink-0 rounded-md bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                      {t("questionCard.recommended")}
                    </span>
                  ) : null}
                </span>
                {option.description ? <span className="block whitespace-pre-wrap break-words text-xs text-muted-foreground">{option.description}</span> : null}
              </span>
            </button>
          );
        })}
        </div>

          <form
            className="grid gap-2 rounded-xl border border-border bg-background p-2"
            onSubmit={(event) => {
              event.preventDefault();
              void answer("custom", customValue.trim());
            }}
          >
            <textarea
              value={customValue}
              onChange={(event) => setCustomValues((current) => ({ ...current, [question.questionId]: event.target.value.slice(0, 2000) }))}
              maxLength={2000}
              rows={2}
              className="app-scrollbar min-w-0 max-h-32 resize-y bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              placeholder={t("questionCard.customPlaceholder")}
              aria-label={t("questionCard.customPlaceholder")}
              disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                onClick={() => {
                  setCustomValues((current) => ({ ...current, [question.questionId]: "" }));
                }}
                disabled={Boolean(busyChoice)}
              >
                {t("questionCard.clear")}
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-2 py-1 text-xs font-medium text-primary-foreground disabled:opacity-50"
              disabled={!customValue.trim() || Boolean(busyChoice) || continuationActive || continuationSettled}
              >
                {t("questionCard.answer")}
              </button>
            </div>
          </form>
      </div>

      <div className="mt-3 flex justify-end">
        <button
          type="button"
          className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-60"
          onClick={() => void answer("skip", t("questionCard.skipAnswer"))}
          disabled={Boolean(busyChoice) || continuationActive || continuationSettled}
        >
          {t("questionCard.skip")}
        </button>
      </div></> : null}
    </section>
  );
}
