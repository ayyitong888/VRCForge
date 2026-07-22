import {
  BrainCircuit,
  Eye,
  Lightbulb,
  Loader2,
  Play,
  RefreshCw,
  Save,
  ShieldCheck,
  Square,
  TimerReset,
} from "lucide-react";
import { useEffect, useRef, useState, type SetStateAction } from "react";
import { useTranslation } from "react-i18next";
import {
  MEMORY_REVIEW_MODES,
  type MemoryReviewMode,
  type MemoryReviewSnapshot,
} from "../../lib/api/memory-review";
import {
  useMemoryReview,
  type MemoryReviewConfigDraft,
} from "../../hooks/use-memory-review";
import { cn } from "../../lib/utils";
import { Badge, type BadgeTone } from "../ui/badge";
import { Button } from "../ui/button";
import { MemoryReviewInbox } from "./memory-review-inbox";

const MODE_KEYS: Record<MemoryReviewMode, { label: string; description: string }> = {
  off: {
    label: "settings.memoryReviewModeOff",
    description: "settings.memoryReviewModeOffDesc",
  },
  shadow: {
    label: "settings.memoryReviewModeShadow",
    description: "settings.memoryReviewModeShadowDesc",
  },
  suggest_only: {
    label: "settings.memoryReviewModeSuggest",
    description: "settings.memoryReviewModeSuggestDesc",
  },
  bounded_background: {
    label: "settings.memoryReviewModeBackground",
    description: "settings.memoryReviewModeBackgroundDesc",
  },
  auto_safe: {
    label: "settings.memoryReviewModeAutoSafe",
    description: "settings.memoryReviewModeAutoSafeDesc",
  },
};

function modeIcon(mode: MemoryReviewMode) {
  if (mode === "off") return <TimerReset className="h-4 w-4" />;
  if (mode === "shadow") return <Eye className="h-4 w-4" />;
  if (mode === "suggest_only") return <Lightbulb className="h-4 w-4" />;
  if (mode === "bounded_background") return <BrainCircuit className="h-4 w-4" />;
  return <ShieldCheck className="h-4 w-4" />;
}

function modeTone(mode: MemoryReviewMode): BadgeTone {
  if (mode === "off") return "muted";
  if (mode === "shadow") return "default";
  if (mode === "suggest_only") return "warn";
  return "ok";
}

function draftFromSnapshot(snapshot: MemoryReviewSnapshot): MemoryReviewConfigDraft {
  return {
    mode: snapshot.mode,
    cadenceMinutes: snapshot.cadenceMinutes,
    inputCharCap: snapshot.inputCharCap,
    tokenCap: snapshot.tokenCap,
    costCapUsd: snapshot.costCapUsd,
    inputCostPerMillionUsd: snapshot.inputCostPerMillionUsd,
    outputCostPerMillionUsd: snapshot.outputCostPerMillionUsd,
    retentionDays: snapshot.retentionDays,
    provider: snapshot.provider || snapshot.providerDisclosure.provider || "",
    model: snapshot.model || snapshot.providerDisclosure.model || "",
    scope: snapshot.scope,
    projectRoot: snapshot.projectRoot,
  };
}

function configFingerprint(snapshot: MemoryReviewSnapshot): string {
  const draft = draftFromSnapshot(snapshot);
  return [
    draft.mode,
    draft.cadenceMinutes,
    draft.inputCharCap,
    draft.tokenCap,
    draft.costCapUsd,
    draft.inputCostPerMillionUsd,
    draft.outputCostPerMillionUsd,
    draft.retentionDays,
    draft.provider,
    draft.model,
    draft.scope,
    draft.projectRoot || "",
  ].map((value) => {
    const text = String(value);
    return `${text.length}:${text}`;
  }).join("|");
}

function safeDate(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString();
}

function safeCount(value: number, minimum = 0): number {
  return Math.max(minimum, Number.isFinite(value) ? value : minimum);
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
  const [draft, setDraft] = useState<MemoryReviewConfigDraft | null>(null);
  const [draftDirty, setDraftDirty] = useState(false);
  const [remoteConfigChanged, setRemoteConfigChanged] = useState(false);
  const observedConfig = useRef("");
  const observedContext = useRef("");
  const snapshot = controller.snapshot;
  const snapshotConfig = snapshot ? configFingerprint(snapshot) : "";
  const draftContext = `${endpoint}\u0000${selectedProjectPath}\u0000${snapshot?.projectRoot || ""}`;

  useEffect(() => {
    if (!snapshot) {
      setDraft(null);
      setDraftDirty(false);
      setRemoteConfigChanged(false);
      observedConfig.current = "";
      observedContext.current = "";
      return;
    }
    const contextChanged = observedContext.current !== draftContext;
    const configChanged = Boolean(observedConfig.current) && observedConfig.current !== snapshotConfig;
    observedContext.current = draftContext;
    observedConfig.current = snapshotConfig;
    if (contextChanged || !draftDirty) {
      setDraft(draftFromSnapshot(snapshot));
      setDraftDirty(false);
      setRemoteConfigChanged(false);
      return;
    }
    if (configChanged) {
      setRemoteConfigChanged(true);
    }
  }, [draftContext, draftDirty, snapshot, snapshotConfig]);

  const editDraft = (next: SetStateAction<MemoryReviewConfigDraft | null>) => {
    setDraftDirty(true);
    setDraft(next);
  };

  const saveDraft = async () => {
    if (!draft) return;
    if (await controller.saveConfig(draft)) {
      setDraftDirty(false);
    }
  };

  const reloadDraft = () => {
    setDraftDirty(false);
  };

  const projectScopeBlocked = draft?.scope === "project" && !selectedProjectPath;
  const providerConfigChanged = snapshot?.providerDisclosure.activeConfigMatches === false;
  const projectBindingChanged = draft?.scope === "project" && snapshot?.configuredProjectMatches === false;
  const configDirty = Boolean(draft && snapshot && (
    draft.mode !== snapshot.mode
    || draft.cadenceMinutes !== snapshot.cadenceMinutes
    || draft.inputCharCap !== snapshot.inputCharCap
    || draft.tokenCap !== snapshot.tokenCap
    || draft.costCapUsd !== snapshot.costCapUsd
    || draft.inputCostPerMillionUsd !== snapshot.inputCostPerMillionUsd
    || draft.outputCostPerMillionUsd !== snapshot.outputCostPerMillionUsd
    || draft.retentionDays !== snapshot.retentionDays
    || draft.provider !== (snapshot.provider || snapshot.providerDisclosure.provider || "")
    || draft.model !== (snapshot.model || snapshot.providerDisclosure.model || "")
    || draft.scope !== snapshot.scope
    || projectBindingChanged
    || providerConfigChanged
  ));
  const runState = String(snapshot?.runStatus?.state || "idle").toLowerCase();
  const runActive = ["queued", "scanning", "provider_call", "persisting"].includes(runState);
  const runStatusKey = runActive
    ? "settings.memoryReviewRunActive"
    : runState === "completed"
      ? "settings.memoryReviewRunCompleted"
      : runState === "failed"
        ? "settings.memoryReviewRunFailed"
        : runState === "timed_out"
          ? "settings.memoryReviewRunTimedOut"
          : runState === "skipped"
            ? "settings.memoryReviewRunDeferred"
        : runState === "cancelled"
          ? "settings.memoryReviewRunCancelled"
          : "settings.memoryReviewRunIdle";
  const nextRun = safeDate(snapshot?.nextRunAt);
  const lastRun = safeDate(snapshot?.lastRun?.completedAt || snapshot?.lastRun?.startedAt);
  const shadowScannedAt = safeDate(snapshot?.shadowSummary?.scannedAt);
  const shadowSkipped = Object.entries(snapshot?.shadowSummary?.reasonCounts || {})
    .filter(([reason]) => reason !== "admitted")
    .reduce((total, [, count]) => total + safeCount(Number(count)), 0);
  const lastRunUsage = snapshot?.lastRun?.usage;
  const providerDisclosure = snapshot?.providerDisclosure;

  return (
    <section className="space-y-5" data-memory-review-settings>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">
              <BrainCircuit className="mr-1.5 inline-block h-4 w-4 align-text-bottom" />
              {t("settings.memoryReviewTitle")}
            </h2>
            {snapshot ? (
              <Badge tone={modeTone(snapshot.mode)}>
                {t(MODE_KEYS[snapshot.mode].label)}
              </Badge>
            ) : null}
            {(snapshot?.unreadCount || 0) > 0 ? (
              <Badge tone="warn">
                {t("settings.memoryReviewUnreadCount", { count: snapshot?.unreadCount || 0 })}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{t("settings.memoryReviewDesc")}</p>
        </div>
        <Button
          type="button"
          variant="outline"
          className="h-9 px-3"
          disabled={!runtimeConnected || controller.loading || Boolean(controller.busyKey)}
          onClick={() => void controller.refresh(true)}
        >
          <RefreshCw className={cn("h-4 w-4", controller.loading && "animate-spin")} />
          {t("common.refresh")}
        </Button>
      </div>

      {!runtimeConnected ? (
        <div className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          {t("settings.memoryReviewRuntimeRequired")}
        </div>
      ) : null}
      {controller.error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive" role="alert">
          {controller.error === "stale_revision"
            ? t("settings.memoryReviewStaleRevision")
            : t("settings.memoryReviewRequestFailed")}
        </div>
      ) : null}
      {providerConfigChanged ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-foreground" role="status">
          {t("settings.memoryReviewProviderChanged")}
        </div>
      ) : null}
      {projectBindingChanged ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-foreground" role="status">
          {t("settings.memoryReviewProjectChanged")}
        </div>
      ) : null}
      {remoteConfigChanged ? (
        <div className="flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-foreground" role="status">
          <span className="min-w-0 flex-1">{t("settings.memoryReviewRemoteConfigChanged")}</span>
          <Button type="button" variant="outline" className="h-8 px-2" onClick={reloadDraft}>
            {t("settings.memoryReviewReloadConfig")}
          </Button>
        </div>
      ) : null}

      {controller.loading && !snapshot ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t("common.loading")}
        </div>
      ) : null}

      {draft && snapshot ? (
        <>
          <div>
            <h3 className="text-sm font-semibold">{t("settings.memoryReviewModeLabel")}</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
              {MEMORY_REVIEW_MODES.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={draft.mode === mode}
                  disabled={!runtimeConnected || Boolean(controller.busyKey) || mode === "auto_safe"}
                  onClick={() => editDraft((current) => current ? {
                    ...current,
                    mode,
                    ...(mode === "off" || mode === "shadow"
                      ? {
                          costCapUsd: 0,
                          inputCostPerMillionUsd: 0,
                          outputCostPerMillionUsd: 0,
                        }
                      : {}),
                  } : current)}
                  className={cn(
                    "rounded-lg border p-3 text-left transition-colors",
                    draft.mode === mode
                      ? "border-primary bg-primary/10"
                      : "border-border bg-card hover:bg-accent",
                  )}
                  data-memory-review-mode={mode}
                >
                  <span className="flex items-center gap-2 text-sm font-medium">
                    {modeIcon(mode)}
                    {t(MODE_KEYS[mode].label)}
                    {mode === "auto_safe" ? (
                      <Badge tone="muted" className="ml-auto h-6">{t("settings.memoryReviewModePlanned")}</Badge>
                    ) : null}
                  </span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">
                    {t(MODE_KEYS[mode].description)}
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="grid gap-4 rounded-lg border border-border bg-card p-4 md:grid-cols-2 xl:grid-cols-3">
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewScopeLabel")}</span>
              <select
                value={draft.scope}
                disabled={Boolean(controller.busyKey)}
                onChange={(event) => editDraft({
                  ...draft,
                  scope: event.target.value === "project" ? "project" : "user",
                  projectRoot: event.target.value === "project" ? selectedProjectPath || undefined : undefined,
                })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              >
                <option value="user">{t("settings.memoryReviewScopeUser")}</option>
                <option value="project" disabled={!selectedProjectPath}>{t("settings.memoryReviewScopeProject")}</option>
              </select>
              {projectScopeBlocked ? (
                <span className="mt-1 block text-xs text-destructive">{t("settings.memoryReviewProjectRequired")}</span>
              ) : null}
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewCadence")}</span>
              <input
                type="number"
                min={30}
                step={30}
                value={draft.cadenceMinutes}
                disabled={Boolean(controller.busyKey)}
                onChange={(event) => editDraft({ ...draft, cadenceMinutes: safeCount(event.target.valueAsNumber, 30) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewRetentionDays")}</span>
              <input
                type="number"
                min={1}
                step={1}
                value={draft.retentionDays}
                disabled={Boolean(controller.busyKey)}
                onChange={(event) => editDraft({ ...draft, retentionDays: safeCount(event.target.valueAsNumber, 1) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewInputCap")}</span>
              <input
                type="number"
                min={1_000}
                step={1_000}
                value={draft.inputCharCap}
                disabled={Boolean(controller.busyKey)}
                onChange={(event) => editDraft({ ...draft, inputCharCap: safeCount(event.target.valueAsNumber, 1_000) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewTokenCap")}</span>
              <input
                type="number"
                min={128}
                step={128}
                value={draft.tokenCap}
                disabled={Boolean(controller.busyKey)}
                onChange={(event) => editDraft({ ...draft, tokenCap: safeCount(event.target.valueAsNumber, 128) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewCostCap")}</span>
              <input
                type="number"
                min={0}
                max={100}
                step={0.01}
                value={draft.costCapUsd}
                disabled={Boolean(controller.busyKey) || draft.mode === "off" || draft.mode === "shadow"}
                onChange={(event) => editDraft({ ...draft, costCapUsd: safeCount(event.target.valueAsNumber) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewInputPrice")}</span>
              <input
                type="number"
                min={0}
                step={0.01}
                value={draft.inputCostPerMillionUsd}
                disabled={Boolean(controller.busyKey) || draft.mode === "off" || draft.mode === "shadow"}
                onChange={(event) => editDraft({ ...draft, inputCostPerMillionUsd: safeCount(event.target.valueAsNumber) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
            <label className="text-sm">
              <span className="font-medium">{t("settings.memoryReviewOutputPrice")}</span>
              <input
                type="number"
                min={0}
                step={0.01}
                value={draft.outputCostPerMillionUsd}
                disabled={Boolean(controller.busyKey) || draft.mode === "off" || draft.mode === "shadow"}
                onChange={(event) => editDraft({ ...draft, outputCostPerMillionUsd: safeCount(event.target.valueAsNumber) })}
                className="mt-1 h-10 w-full rounded-md border border-border bg-background px-3"
              />
            </label>
          </div>
          {draft.mode === "suggest_only" || draft.mode === "bounded_background" ? (
            <p className="-mt-2 text-xs text-muted-foreground">
              {t("settings.memoryReviewPricingHelp")}
            </p>
          ) : null}

          <div className="rounded-lg border border-border bg-muted/30 p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={providerDisclosure?.paidRun ? "warn" : "ok"}>
                {providerDisclosure?.paidRun
                  ? t("settings.memoryReviewPaidRun")
                  : t("settings.memoryReviewNoPaidRun")}
              </Badge>
              <span className="text-muted-foreground">{t(runStatusKey)}</span>
            </div>
            {providerDisclosure?.paidRun ? (
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
                <div>
                  <dt className="text-muted-foreground">{t("settings.memoryReviewProvider")}</dt>
                  <dd className="mt-0.5 font-medium">{providerDisclosure.providerLabel || providerDisclosure.provider || t("common.unavailable")}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t("settings.memoryReviewModel")}</dt>
                  <dd className="mt-0.5 font-medium">{providerDisclosure.model || t("common.unavailable")}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t("settings.memoryReviewTokenCap")}</dt>
                  <dd className="mt-0.5 font-medium">{providerDisclosure.tokenCap ?? t("common.unavailable")}</dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">{t("settings.memoryReviewCostCap")}</dt>
                  <dd className="mt-0.5 font-medium">
                    {typeof providerDisclosure.costCapUsd === "number" && providerDisclosure.costCapUsd > 0
                      ? `$${providerDisclosure.costCapUsd.toFixed(4)}`
                      : t("settings.memoryReviewCostUnavailable")}
                  </dd>
                </div>
              </dl>
            ) : null}
            {snapshot.lastRun?.provider || snapshot.lastRun?.model ? (
              <div className="mt-3 border-t border-border pt-3" data-memory-review-last-run-evidence>
                <p className="text-xs font-medium text-foreground">
                  {t("settings.memoryReviewLastRunEvidence", {
                    inputCap: snapshot.lastRun.budget?.inputCharCap ?? t("common.unavailable"),
                    tokenCap: snapshot.lastRun.budget?.tokenCap ?? t("common.unavailable"),
                  })}
                </p>
                <dl className="mt-2 grid gap-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
                  <div>
                    <dt className="text-muted-foreground">{t("settings.memoryReviewProvider")}</dt>
                    <dd className="mt-0.5 font-medium">{snapshot.lastRun.provider || t("common.unavailable")}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">{t("settings.memoryReviewModel")}</dt>
                    <dd className="mt-0.5 font-medium">{snapshot.lastRun.model || t("common.unavailable")}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">{t("settings.memoryReviewTokenUsage")}</dt>
                    <dd className="mt-0.5 font-medium">{lastRunUsage?.totalTokens ?? t("common.unavailable")}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">
                      {typeof lastRunUsage?.costUpperBoundUsd === "number"
                        ? t("settings.memoryReviewBoundedRunCost")
                        : t("settings.memoryReviewActualCost")}
                    </dt>
                    <dd className="mt-0.5 font-medium">
                      {typeof lastRunUsage?.costUpperBoundUsd === "number"
                        ? t("settings.memoryReviewRetryCostUpperBound", {
                          cost: lastRunUsage.costUpperBoundUsd.toFixed(4),
                          count: lastRunUsage.attempts || snapshot.lastRun.attempt || 1,
                        })
                        : typeof lastRunUsage?.costUsd === "number"
                        ? `$${lastRunUsage.costUsd.toFixed(4)}`
                        : t("settings.memoryReviewCostUnavailable")}
                    </dd>
                  </div>
                </dl>
              </div>
            ) : null}
            {nextRun || lastRun ? (
              <p className="mt-3 text-xs text-muted-foreground">
                {nextRun ? t("settings.memoryReviewNextRun", { time: nextRun }) : ""}
                {nextRun && lastRun ? " · " : ""}
                {lastRun ? t("settings.memoryReviewLastRun", { time: lastRun }) : ""}
              </p>
            ) : null}
          </div>

          {snapshot.shadowSummary ? (
            <div className="rounded-lg border border-border bg-card p-4 text-sm" data-memory-review-shadow-summary>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="default">{t("settings.memoryReviewShadowSummary")}</Badge>
                <span className="font-medium">
                  {t("settings.memoryReviewShadowEligible", {
                    count: safeCount(snapshot.shadowSummary.eligibleCount),
                  })}
                </span>
                <span className="text-muted-foreground">
                  {t("settings.memoryReviewShadowSkipped", { count: shadowSkipped })}
                </span>
              </div>
              {shadowScannedAt ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  {t("settings.memoryReviewShadowScannedAt", { time: shadowScannedAt })}
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              disabled={!runtimeConnected || Boolean(controller.busyKey) || projectScopeBlocked}
              onClick={() => void saveDraft()}
            >
              {controller.busyKey === "config" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
              {t("settings.memoryReviewSave")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={
                !runtimeConnected
                || Boolean(controller.busyKey)
                || projectScopeBlocked
                || draft.mode === "off"
                || runActive
                || configDirty
                || providerConfigChanged
              }
              onClick={() => void controller.startReview(draft.scope)}
            >
              {controller.busyKey === "run" || runActive
                ? <Loader2 className="h-4 w-4 animate-spin" />
                : <Play className="h-4 w-4" />}
              {draft.mode === "shadow"
                ? t("settings.memoryReviewRunShadow")
                : t("settings.memoryReviewRun")}
            </Button>
            {runActive ? (
              <Button
                type="button"
                variant="danger"
                disabled={!runtimeConnected || controller.cancelling}
                onClick={() => void controller.cancelRun()}
              >
                {controller.cancelling
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <Square className="h-4 w-4" />}
                {t("settings.memoryReviewCancelRun")}
              </Button>
            ) : null}
          </div>

          <div>
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-semibold">{t("settings.memoryReviewInbox")}</h3>
              <Badge tone="muted">{snapshot.candidates.length}</Badge>
            </div>
            <MemoryReviewInbox
              candidates={snapshot.candidates}
              busyKey={controller.busyKey}
              runtimeConnected={runtimeConnected}
              onDecision={controller.decideCandidate}
            />
          </div>
        </>
      ) : null}
    </section>
  );
}
