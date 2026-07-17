import { Check, Sparkles } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DEFAULT_LOCALE, SUPPORTED_LOCALES, type LocaleCode } from "../../i18n";
import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

const LANGUAGE_OPTION_VISUAL_CLASSES = {
  selected: "border-primary bg-primary/10 text-foreground",
  idle: "border-border bg-card text-foreground hover:bg-accent",
} as const;

export function OnboardingLanguageGate({
  open,
  currentLanguage,
  onContinue,
}: {
  open: boolean;
  currentLanguage: string;
  onContinue: (locale: LocaleCode) => void;
}) {
  const { t } = useTranslation();
  const [selectedLocale, setSelectedLocale] = useState<LocaleCode>(() =>
    SUPPORTED_LOCALES.find((locale) => locale.code === currentLanguage)?.code || DEFAULT_LOCALE,
  );
  if (!open) {
    return null;
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-language-gate-title"
        aria-describedby="onboarding-language-gate-description"
        data-vrcforge-onboarding-language-gate="true"
        className="max-h-[calc(100vh-3rem)] w-full max-w-md overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-panel"
      >
        <div className="flex min-w-0 items-center gap-3">
          <Sparkles className="h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
          <h2 id="onboarding-language-gate-title" className="text-lg font-semibold">
            {t("onboarding.welcome")}
          </h2>
        </div>
        <p id="onboarding-language-gate-description" className="mt-3 text-sm text-muted-foreground">
          {t("settings.languageDesc")}
        </p>
        <fieldset className="mt-5">
          <legend className="text-sm font-medium">{t("settings.language")}</legend>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {SUPPORTED_LOCALES.map((locale) => {
              const selectionState = locale.code === selectedLocale ? "selected" : "idle";
              return (
                <button
                  key={locale.code}
                  type="button"
                  data-state={selectionState}
                  data-vrcforge-onboarding-language-option={locale.code}
                  aria-pressed={selectionState === "selected"}
                  onClick={() => setSelectedLocale(locale.code)}
                  className={cn(
                    "flex min-w-0 items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium transition-colors",
                    LANGUAGE_OPTION_VISUAL_CLASSES[selectionState],
                  )}
                >
                  <span className="min-w-0 flex-1 text-left">{locale.label}</span>
                  {selectionState === "selected" ? (
                    <Check className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
                  ) : null}
                </button>
              );
            })}
          </div>
        </fieldset>
        <div className="mt-6 flex justify-end">
          <Button
            type="button"
            data-vrcforge-onboarding-language-continue
            onClick={() => onContinue(selectedLocale)}
          >
            {t("onboarding.nextStep")}
          </Button>
        </div>
      </section>
    </div>
  );
}
