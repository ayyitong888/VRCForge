import { Brain, Check, Loader2, Play, ShieldX, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MemoryReviewSnapshot } from "../../lib/api/memory-review";
import {
  useMemoryReview,
  type MemoryReviewConfigDraft,
} from "../../hooks/use-memory-review";
import { MemoryReviewInbox } from "./memory-review-inbox";
import type { MemoryDreamingProposal } from "../../lib/api/memory-review";
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

function DreamingProposalCard({
  proposal,
  busy,
  runtimeConnected,
  approvalEnabled,
  onDecision,
}: {
  proposal: MemoryDreamingProposal;
  busy: boolean;
  runtimeConnected: boolean;
  approvalEnabled: boolean;
  onDecision: (action: "accept" | "reject") => void;
}) {
  const { t } = useTranslation();
  const actionable = proposal.state === "proposed" && !proposal.stale;
  return (
    <article className="rounded-xl border border-border bg-card px-5 py-4" data-memory-dreaming-proposal={proposal.proposalId}>
      <div className="font-medium text-foreground">{t("settings.memoryDreamingTitle")}</div>
      <p className="mt-1 text-sm leading-6 text-muted-foreground">{t("settings.memoryDreamingDesc")}</p>
      <div className="mt-3 space-y-3">
        {proposal.groups.map((group) => (
          <div key={group.keepId} className="rounded-lg border border-border/80 bg-background px-3 py-2 text-sm">
            <div><span className="font-medium">{t("settings.memoryDreamingKeep")}: </span>{group.keepText || group.keepId}</div>
            {group.removeTexts.length ? (
              <div className="mt-1 text-muted-foreground">
                <span className="font-medium">{t("settings.memoryDreamingRemove")}: </span>{group.removeTexts.join(" · ")}
              </div>
            ) : null}
          </div>
        ))}
      </div>
      {proposal.stale ? <p className="mt-3 text-sm text-amber-700 dark:text-amber-300">{t("settings.memoryDreamingStale")}</p> : null}
      {actionable ? (
        <div className="mt-4 flex gap-2">
          <button type="button" className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3 text-sm text-primary-foreground disabled:opacity-50" disabled={!runtimeConnected || !approvalEnabled || busy} onClick={() => onDecision("accept")}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}{t("settings.memoryDreamingApprove")}
          </button>
          <button type="button" className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-3 text-sm disabled:opacity-50" disabled={!runtimeConnected || busy} onClick={() => onDecision("reject")}>
            <ShieldX className="h-4 w-4" />{t("settings.memoryDreamingReject")}
          </button>
        </div>
      ) : null}
    </article>
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
  const ready = runtimeConnected && Boolean(snapshot);
  const memoryEnabled = snapshot?.memoryEnabled !== false;
  const crossSessionEnabled = memoryEnabled && snapshot?.crossSessionEnabled !== false;
  const busy = controller.busyKey === "config";
  const reviewBusy = controller.busyKey === "run";
  const runActive = Boolean(snapshot?.lastRun?.runId) && !["idle", "completed", "failed", "cancelled"].includes(snapshot?.runStatus.state || "idle");
  const approvalEnabled = memoryEnabled && crossSessionEnabled;

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

      <div className="space-y-3">
        <ToggleRow
          checked={memoryEnabled}
          disabled={!ready}
          busy={busy}
          label={t("settings.memoryEnabled")}
          description={t("settings.memoryEnabledDesc")}
          testId="memory"
          onChange={(checked) => updatePreferences(checked, checked ? crossSessionEnabled : false)}
        />
        <ToggleRow
          checked={crossSessionEnabled}
          disabled={!ready || !memoryEnabled}
          busy={busy}
          label={t("settings.crossSessionMemory")}
          description={t("settings.crossSessionMemoryDesc")}
          testId="cross-session"
          onChange={(checked) => updatePreferences(memoryEnabled, checked)}
        />
      </div>

      <div className="rounded-xl border border-border bg-card px-5 py-4" data-memory-review-controls>
        <div className="font-medium text-foreground">{t("settings.memoryReviewTitle")}</div>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">{t("settings.memoryReviewDesc")}</p>
        <div className="mt-2 text-xs text-muted-foreground">
          {snapshot?.providerDisclosure?.paidRun ? t("settings.memoryReviewPaidRun") : t("settings.memoryReviewNoPaidRun")}
          {snapshot?.providerDisclosure?.providerLabel || snapshot?.providerDisclosure?.provider ? ` · ${t("settings.memoryReviewProvider")}: ${snapshot.providerDisclosure.providerLabel || snapshot.providerDisclosure.provider}` : ""}
          {snapshot?.providerDisclosure?.model ? ` · ${t("settings.memoryReviewModel")}: ${snapshot.providerDisclosure.model}` : ""}
        </div>
        {runActive ? (
          <button type="button" className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-3 text-sm disabled:opacity-50" disabled={!ready || controller.cancelling} onClick={() => void controller.cancelRun()}>
            {controller.cancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
            {t("settings.memoryReviewCancelRun")}
          </button>
        ) : (
          <button type="button" className="mt-3 inline-flex h-9 items-center gap-1.5 rounded-md border border-border px-3 text-sm disabled:opacity-50" disabled={!ready || !approvalEnabled || Boolean(controller.busyKey)} onClick={() => void controller.startReview()}>
            {reviewBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {t("settings.memoryReviewRun")}
          </button>
        )}
        {snapshot?.runStatus.state && snapshot.runStatus.state !== "idle" ? <div className="mt-2 text-xs text-muted-foreground">{snapshot.runStatus.state}</div> : null}
      </div>

      {snapshot?.dreamingProposal ? (
        <DreamingProposalCard
          proposal={snapshot.dreamingProposal}
          busy={controller.busyKey.startsWith("candidate:dreaming:")}
          runtimeConnected={runtimeConnected}
          approvalEnabled={approvalEnabled}
          onDecision={(action) => void controller.decideCandidate(`dreaming:${snapshot.dreamingProposal?.proposalId || ""}`, action)}
        />
      ) : null}

      {snapshot ? <MemoryReviewInbox candidates={snapshot.candidates} busyKey={controller.busyKey} runtimeConnected={runtimeConnected} onDecision={controller.decideCandidate} /> : null}

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
