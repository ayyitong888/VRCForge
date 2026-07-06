import { Check, FolderPlus, Loader2, RefreshCw, Settings, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

export function OnboardingOverlay({
  open,
  minimized,
  stepIndex,
  runtimeConnected,
  apiKeyPresent,
  hasProjects,
  loadingRuntime,
  onRetryRuntime,
  onOpenSettings,
  onOpenProjectPicker,
  onResume,
  onFinish,
  onPreviousStep,
  onNextStep,
}: {
  open: boolean;
  minimized: boolean;
  stepIndex: number;
  runtimeConnected: boolean;
  apiKeyPresent: boolean;
  hasProjects: boolean;
  loadingRuntime: boolean;
  onRetryRuntime: () => void;
  onOpenSettings: () => void;
  onOpenProjectPicker: () => void;
  onResume: () => void;
  onFinish: () => void;
  onPreviousStep: () => void;
  onNextStep: () => void;
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
      <section className="w-full max-w-lg rounded-lg border border-border bg-card p-6 shadow-panel">
        <div className="flex min-w-0 items-center gap-3">
          <Sparkles className="h-5 w-5 shrink-0 text-primary" />
          <h2 className="truncate text-lg font-semibold">{t("onboarding.welcome")}</h2>
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">{t("onboarding.stepProgress", { current: stepIndex + 1, total: steps.length })}</span>
        </div>
        <div className="mt-4 flex items-center gap-2">
          {steps.map((item, index) => (
            <div
              key={item.title}
              className={cn(
                "h-1.5 flex-1 rounded-full transition-colors",
                index < stepIndex || item.done ? "bg-primary" : index === stepIndex ? "bg-primary/40" : "bg-muted",
              )}
            />
          ))}
        </div>
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
          <Button variant="ghost" className="text-muted-foreground" onClick={onFinish}>
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
