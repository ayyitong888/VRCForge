import { Brain, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MemoryReviewSnapshot } from "../../lib/api/memory-review";
import {
  useMemoryReview,
  type MemoryReviewConfigDraft,
} from "../../hooks/use-memory-review";
import { cn } from "../../lib/utils";

function preferenceDraft(
  snapshot: MemoryReviewSnapshot,
  memoryEnabled: boolean,
  crossSessionEnabled: boolean,
): MemoryReviewConfigDraft {
  const effectiveCrossSession = memoryEnabled && crossSessionEnabled;
  return {
    memoryEnabled,
    crossSessionEnabled: effectiveCrossSession,
    mode: "off",
    cadenceMinutes: snapshot.cadenceMinutes,
    inputCharCap: snapshot.inputCharCap,
    tokenCap: snapshot.tokenCap,
    costCapUsd: snapshot.costCapUsd,
    inputCostPerMillionUsd: snapshot.inputCostPerMillionUsd,
    outputCostPerMillionUsd: snapshot.outputCostPerMillionUsd,
    retentionDays: snapshot.retentionDays,
    automaticCaptureEnabled: effectiveCrossSession,
    provider: snapshot.provider || snapshot.providerDisclosure.provider || "",
    model: snapshot.model || snapshot.providerDisclosure.model || "",
    scope: snapshot.scope,
    projectRoot: snapshot.projectRoot,
  };
}

function ToggleRow({
  checked,
  disabled = false,
  busy = false,
  label,
  description,
  testId,
  onChange,
}: {
  checked: boolean;
  disabled?: boolean;
  busy?: boolean;
  label: string;
  description: string;
  testId: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-5 rounded-xl border border-border bg-card px-5 py-4">
      <div className="min-w-0">
        <div className="font-medium text-foreground">{label}</div>
        <div className="mt-1 text-sm leading-6 text-muted-foreground">{description}</div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        data-memory-toggle={testId}
        disabled={disabled || busy}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-7 w-12 shrink-0 rounded-full border transition-colors",
          checked ? "border-primary bg-primary" : "border-border bg-muted",
          (disabled || busy) && "cursor-not-allowed opacity-50",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform",
            checked ? "translate-x-5" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}

export function MemoryReviewSettings({
  endpoint,
  runtimeConnected,
  selectedProjectPath,
  refreshSignal = 0,
}: {
  endpoint: string;
  runtimeConnected: boolean;
  selectedProjectPath: string;
  refreshSignal?: number;
}) {
  const { t } = useTranslation();
  const controller = useMemoryReview({
    endpoint,
    runtimeConnected,
    selectedProjectPath,
    refreshSignal,
  });
  const snapshot = controller.snapshot;
  const memoryEnabled = snapshot?.memoryEnabled !== false;
  const crossSessionEnabled = memoryEnabled && snapshot?.crossSessionEnabled !== false;
  const busy = controller.busyKey === "config";

  const updatePreferences = (nextMemoryEnabled: boolean, nextCrossSessionEnabled: boolean) => {
    if (!snapshot) return;
    void controller.saveConfig(preferenceDraft(
      snapshot,
      nextMemoryEnabled,
      nextCrossSessionEnabled,
    ));
  };

  return (
    <section className="space-y-5" data-memory-preferences>
      <div className="flex items-start gap-3">
        <Brain className="mt-0.5 h-5 w-5 text-primary" />
        <div>
          <h2 className="text-xl font-semibold text-foreground">{t("settings.memoryPreferencesTitle")}</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{t("settings.memoryPreferencesDesc")}</p>
        </div>
      </div>

      {!runtimeConnected || controller.loading || !snapshot ? (
        <div className="flex items-center gap-2 rounded-xl border border-border bg-card px-5 py-4 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("settings.memoryPreferencesLoading")}
        </div>
      ) : (
        <div className="space-y-3">
          <ToggleRow
            checked={memoryEnabled}
            busy={busy}
            label={t("settings.memoryEnabled")}
            description={t("settings.memoryEnabledDesc")}
            testId="memory"
            onChange={(checked) => updatePreferences(checked, checked ? crossSessionEnabled : false)}
          />
          <ToggleRow
            checked={crossSessionEnabled}
            disabled={!memoryEnabled}
            busy={busy}
            label={t("settings.crossSessionMemory")}
            description={t("settings.crossSessionMemoryDesc")}
            testId="cross-session"
            onChange={(checked) => updatePreferences(memoryEnabled, checked)}
          />
        </div>
      )}

      {controller.error ? (
        <div className="text-sm text-destructive">
          {t(controller.error === "stale_revision"
            ? "settings.memoryReviewStaleRevision"
            : "settings.memoryReviewRequestFailed")}
        </div>
      ) : null}
    </section>
  );
}
