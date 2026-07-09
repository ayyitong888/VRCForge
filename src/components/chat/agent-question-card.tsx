import { ChevronLeft, ChevronRight, Pencil } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { AgentQuestion } from "../../lib/api";
import { cn, formatCount } from "../../lib/utils";

export function AgentQuestionCard({
  questions,
  onAnswerQuestion,
}: {
  questions: AgentQuestion[];
  onAnswerQuestion: (questionId: string, optionId: string, value: string) => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const pendingQuestions = useMemo(
    () => questions.filter((question) => (question.status || "pending").toLowerCase() === "pending"),
    [questions],
  );
  const [index, setIndex] = useState(0);
  const [customOpen, setCustomOpen] = useState(false);
  const [customValue, setCustomValue] = useState("");
  const [busyChoice, setBusyChoice] = useState("");

  useEffect(() => {
    setIndex((current) => Math.min(current, Math.max(0, pendingQuestions.length - 1)));
    setCustomOpen(false);
    setCustomValue("");
    setBusyChoice("");
  }, [pendingQuestions.length]);

  const question = pendingQuestions[index];
  if (!question) {
    return null;
  }

  const options = question.options || [];
  const answer = async (optionId: string, value: string) => {
    if (!value.trim() && optionId !== "skip") {
      return;
    }
    setBusyChoice(optionId);
    try {
      await onAnswerQuestion(question.questionId, optionId, value);
      setCustomValue("");
      setCustomOpen(false);
    } finally {
      setBusyChoice("");
    }
  };

  return (
    <section className="rounded-2xl border border-border bg-card p-3 shadow-sm" aria-label={t("questionCard.label")}>
      <div className="mb-2 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
        <span className="min-w-0 flex-1 truncate font-medium text-foreground">{question.header || t("questionCard.title")}</span>
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

      <div className="mb-3 whitespace-pre-wrap break-words text-sm font-medium text-foreground">
        {question.question || question.questionId}
      </div>

      <div className="grid gap-1.5">
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
              disabled={Boolean(busyChoice)}
              title={option.description || option.label}
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-background text-sm font-semibold text-muted-foreground">
                {formatCount(optionIndex + 1)}
              </span>
              <span className="min-w-0">
                <span className={cn("flex min-w-0 items-center gap-2 text-sm font-medium", busy && "text-muted-foreground")}>
                  <span className="truncate">{option.label}</span>
                  {optionIndex === 0 ? (
                    <span className="shrink-0 rounded-md bg-background px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                      {t("questionCard.recommended")}
                    </span>
                  ) : null}
                </span>
                {option.description ? <span className="block truncate text-xs text-muted-foreground">{option.description}</span> : null}
              </span>
            </button>
          );
        })}
        </div>

        {customOpen ? (
          <form
            className="grid gap-2 rounded-xl border border-border bg-background p-2"
            onSubmit={(event) => {
              event.preventDefault();
              void answer("custom", customValue.trim());
            }}
          >
            <input
              value={customValue}
              onChange={(event) => setCustomValue(event.target.value)}
              className="min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
              placeholder={t("questionCard.customPlaceholder")}
              disabled={Boolean(busyChoice)}
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted"
                onClick={() => {
                  setCustomOpen(false);
                  setCustomValue("");
                }}
                disabled={Boolean(busyChoice)}
              >
                {t("common.cancel")}
              </button>
              <button
                type="submit"
                className="rounded-md bg-primary px-2 py-1 text-xs font-medium text-primary-foreground disabled:opacity-50"
                disabled={!customValue.trim() || Boolean(busyChoice)}
              >
                {t("questionCard.answer")}
              </button>
            </div>
          </form>
        ) : (
          <button
            type="button"
            className="grid min-w-0 grid-cols-[32px_minmax(0,1fr)] items-center gap-2 rounded-xl px-2.5 py-2 text-left text-muted-foreground transition-colors hover:bg-muted"
            onClick={() => setCustomOpen(true)}
            disabled={Boolean(busyChoice)}
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-muted">
              <Pencil className="h-3.5 w-3.5" />
            </span>
            <span className="truncate text-sm">{t("questionCard.somethingElse")}</span>
          </button>
        )}
      </div>

      <div className="mt-3 flex justify-end">
        <button
          type="button"
          className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-xs font-medium transition-colors hover:bg-muted disabled:opacity-60"
          onClick={() => void answer("skip", t("questionCard.skipAnswer"))}
          disabled={Boolean(busyChoice)}
        >
          {t("questionCard.skip")}
        </button>
      </div>
    </section>
  );
}
