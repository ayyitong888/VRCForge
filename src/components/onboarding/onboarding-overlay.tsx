import { Check, Circle, FolderPlus, Globe, Loader2, RefreshCw, Settings, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LOCALES, type LocaleCode } from "../../i18n";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { onboardingChecklistItemState, onboardingChecklistVisualClasses } from "./onboarding-checklist-state";

export function OnboardingOverlay({
  open,
  minimized,
  stepIndex,
  runtimeConnected,
  apiKeyPresent,
  hasProjects,
  loadingRuntime,
  currentLanguage,
  onRetryRuntime,
  onOpenSettings,
  onOpenProjectPicker,
  onResume,
  onFinish,
  onPreviousStep,
  onNextStep,
  onLocaleChange,
}: {
  open: boolean;
  minimized: boolean;
  stepIndex: number;
  runtimeConnected: boolean;
  apiKeyPresent: boolean;
  hasProjects: boolean;
  loadingRuntime: boolean;
  currentLanguage: string;
  onRetryRuntime: () => void;
  onOpenSettings: () => void;
  onOpenProjectPicker: () => void;
  onResume: () => void;
  onFinish: () => void;
  onPreviousStep: () => void;
  onNextStep: () => void;
  onLocaleChange: (locale: LocaleCode) => void;
}) {
  const { t } = useTranslation();
  if (!open) {
    return null;
  }
  const steps = [
    {
      title: t("onboarding.step1Title"),
      done: runtimeConnected,
      doneDesc: t("onboarding.step1DoneDesc"),
      todoDesc: t("onboarding.step1TodoDesc"),
      action: (
        <Button variant="outline" disabled={loadingRuntime} onClick={onRetryRuntime}>
          <RefreshCw className="mr-1 h-4 w-4" />
          {loadingRuntime ? t("onboarding.connecting") : t("onboarding.retryConnection")}
        </Button>
      ),
    },
    {
      title: t("onboarding.step2Title"),
      done: apiKeyPresent,
      doneDesc: t("onboarding.step2DoneDesc"),
      todoDesc: t("onboarding.step2TodoDesc"),
      action: (
        <Button variant="outline" onClick={onOpenSettings}>
          <Settings className="mr-1 h-4 w-4" />
          {t("onboarding.goToSettings")}
        </Button>
      ),
    },
    {
      title: t("onboarding.step3Title"),
      done: hasProjects,
      doneDesc: t("onboarding.step3DoneDesc"),
      todoDesc: t("onboarding.step3TodoDesc"),
      action: (
        <Button variant="outline" onClick={onOpenProjectPicker}>
          <FolderPlus className="mr-1 h-4 w-4" />
          {t("sidebar.newProject")}
        </Button>
      ),
    },
  ];
  if (minimized) {
    return (
      <button
        type="button"
        onClick={onResume}
        className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2.5 text-sm shadow-panel transition-colors hover:bg-muted"
      >
        <Sparkles className="h-4 w-4 shrink-0 text-primary" />
        <span>{t("onboarding.continueOnboarding", { step: stepIndex + 1 })}</span>
      </button>
    );
  }
  const step = steps[Math.min(stepIndex, steps.length - 1)];
  const isLast = stepIndex >= steps.length - 1;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="vrcforge-onboarding-title"
        data-vrcforge-onboarding="true"
        className="max-h-[calc(100vh-3rem)] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-panel"
      >
        <div className="flex min-w-0 items-center gap-3">
          <Sparkles className="h-5 w-5 shrink-0 text-primary" />
          <h2 id="vrcforge-onboarding-title" className="truncate text-lg font-semibold">
            {t("onboarding.welcome")}
          </h2>
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">{t("onboarding.stepProgress", { current: stepIndex + 1, total: steps.length })}</span>
        </div>
        <div className="mt-3 flex justify-end">
          <label className="flex max-w-full items-center gap-2 text-xs text-muted-foreground">
            <Globe className="h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="sr-only">{t("settings.language")}</span>
            <select
              value={currentLanguage}
              onChange={(event) => onLocaleChange(event.target.value as LocaleCode)}
              aria-label={t("settings.language")}
              data-vrcforge-onboarding-language
              className="min-w-0 max-w-44 rounded-md border border-border bg-card px-2 py-1 text-xs text-foreground"
            >
              {SUPPORTED_LOCALES.map((locale) => (
                <option key={locale.code} value={locale.code}>
                  {locale.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ol
          className="mt-4 grid gap-2"
          aria-label={t("onboarding.stepProgress", { current: stepIndex + 1, total: steps.length })}
          data-vrcforge-onboarding-checklist
        >
          {steps.map((item, index) => {
            const state = onboardingChecklistItemState(item.done, index === stepIndex);
            return (
              <li
                key={item.title}
                data-state={state.completion}
                data-position={state.position}
                data-vrcforge-onboarding-checklist-item
                aria-current={state.position === "current" ? "step" : undefined}
                aria-label={`${item.title}: ${state.completion === "done" ? t("onboarding.done") : item.todoDesc}`}
                className={cn(
                  "flex min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors",
                  onboardingChecklistVisualClasses.item[state.position],
                )}
              >
                {state.completion === "done" ? (
                  <Check
                    className={cn("h-4 w-4 shrink-0", onboardingChecklistVisualClasses.icon[state.completion])}
                    aria-hidden="true"
                  />
                ) : (
                  <Circle
                    className={cn("h-4 w-4 shrink-0", onboardingChecklistVisualClasses.icon[state.completion])}
                    aria-hidden="true"
                  />
                )}
                <span className="min-w-0 flex-1 break-words font-medium leading-tight">{item.title}</span>
                {state.completion === "done" ? (
                  <span className="shrink-0 text-xs font-medium text-muted-foreground">{t("onboarding.done")}</span>
                ) : null}
              </li>
            );
          })}
        </ol>
        <div className="mt-5 rounded-xl border border-border px-5 py-4">
          <div className="flex min-w-0 items-center gap-2">
            {step.done ? <Check className="h-4 w-4 shrink-0 text-primary" /> : <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" />}
            <div className="truncate text-sm font-medium">{step.title}</div>
            <Badge tone={step.done ? "ok" : "muted"} className="ml-auto shrink-0">
              {step.done ? t("onboarding.done") : t("onboarding.detecting")}
            </Badge>
          </div>
          <p className="mt-2 text-sm text-muted-foreground">{step.done ? step.doneDesc : step.todoDesc}</p>
          {!step.done ? <div className="mt-4">{step.action}</div> : null}
        </div>
        <div className="mt-6 flex items-center gap-3">
          <Button
            variant="ghost"
            className="text-muted-foreground"
            data-vrcforge-onboarding-skip
            onClick={onFinish}
          >
            {t("onboarding.skipOnboarding")}
          </Button>
          <div className="ml-auto flex gap-3">
            {stepIndex > 0 ? (
              <Button variant="outline" onClick={onPreviousStep}>
                {t("onboarding.prevStep")}
              </Button>
            ) : null}
            <Button disabled={!step.done} onClick={isLast ? onFinish : onNextStep}>
              {isLast ? t("onboarding.startUsing") : t("onboarding.nextStep")}
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}
