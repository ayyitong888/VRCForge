import { AlertTriangle, CheckCircle2, Clock3, HelpCircle, ShieldCheck, X } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import type {
  AgentGoalBackgroundAcknowledgement,
  AgentGoalDelivery,
  AgentGoalProviderWarning,
  AgentGoalRenderedRecap,
} from "../../lib/api";

type Translate = (key: string) => string;

const PHASE_KEYS: Record<string, string> = {
  wake: "goal.backgroundPhaseWake",
  project_lock: "goal.backgroundPhaseProjectLock",
  provider_call: "goal.backgroundPhaseProviderCall",
  apply: "goal.backgroundPhaseApply",
  deliver: "goal.backgroundPhaseDeliver",
};

const FAILURE_KEYS: Record<string, string> = {
  auth: "goal.backgroundFailureAuthCredit",
  credit: "goal.backgroundFailureAuthCredit",
  auth_credit: "goal.backgroundFailureAuthCredit",
  schema: "goal.backgroundFailureSchemaPrivacy",
  privacy: "goal.backgroundFailureSchemaPrivacy",
  schema_privacy: "goal.backgroundFailureSchemaPrivacy",
  invalid_request: "goal.backgroundFailureInvalidRequest",
  network: "goal.backgroundFailureNetwork",
  transient: "goal.backgroundFailureNetwork",
  rate_limit: "goal.backgroundFailureRateLimit",
  timeout: "goal.backgroundFailureTimeout",
  server_error: "goal.backgroundFailureServer",
  provider_unreachable: "goal.backgroundFailureProviderUnavailable",
  permission_denied: "goal.backgroundFailurePermissionDenied",
  cancelled: "goal.backgroundFailureCancelled",
  loop_suppressed: "goal.backgroundFailureLoopSuppressed",
  apply_failed: "goal.backgroundFailureApply",
  tool_failed: "goal.backgroundFailureTool",
  context_compaction_required: "goal.backgroundFailureContextCompaction",
  await_user_instruction: "goal.backgroundFailureNeedsInstruction",
  paused: "goal.backgroundFailurePaused",
  step_limit_reached: "goal.backgroundFailurePaused",
};

const COST_UNAVAILABLE_KEYS: Record<string, string> = {
  pricing_not_configured: "goal.backgroundCostPricingNotConfigured",
  pricing_incomplete: "goal.backgroundCostPricingIncomplete",
  pricing_invalid: "goal.backgroundCostPricingInvalid",
  usage_incomplete: "goal.backgroundCostUsageIncomplete",
  usage_bounded: "goal.backgroundCostUsageBounded",
  usage_inconsistent: "goal.backgroundCostUsageInconsistent",
};

const PROVIDER_WARNING_KEYS: Record<string, string> = {
  provider_unreachable: "goal.backgroundProviderWarningUnavailable",
  local_provider_unreachable: "goal.backgroundProviderWarningUnavailable",
  preflight_unreachable: "goal.backgroundProviderWarningUnavailable",
};

function normalizedSemanticValue(value: unknown): string {
  return String(value || "").trim().toLowerCase().replace(/-/g, "_");
}

function finiteRevision(value: unknown, fallback = 0): number {
  const revision = Number(value);
  return Number.isInteger(revision) && revision >= 0 ? revision : fallback;
}

function recapRevision(delivery: AgentGoalDelivery): number {
  return finiteRevision(delivery.recapRevision, finiteRevision(delivery.revision));
}

function recapKey(delivery: AgentGoalDelivery): string {
  return `${delivery.deliveryId}:${recapRevision(delivery)}`;
}

function providerWarningRevision(warning: AgentGoalProviderWarning): number {
  return finiteRevision(warning.count);
}

function providerWarningRevisionKey(warning: AgentGoalProviderWarning): string {
  return `${warning.warningKey}:${providerWarningRevision(warning)}`;
}

function providerWarningAlreadyAcknowledged(warning: AgentGoalProviderWarning): boolean {
  const acknowledgedRevision = Number(warning.acknowledgedRevision);
  return Number.isInteger(acknowledgedRevision) && acknowledgedRevision >= 0
    ? acknowledgedRevision >= providerWarningRevision(warning)
    : Boolean(warning.acknowledgedAt);
}

function visibleProviderWarnings(warnings: AgentGoalProviderWarning[]): AgentGoalProviderWarning[] {
  const unique = new Map<string, AgentGoalProviderWarning>();
  for (const warning of warnings) {
    const key = String(warning.warningKey || "").trim();
    if (!key || providerWarningAlreadyAcknowledged(warning)) {
      continue;
    }
    const current = unique.get(key);
    if (!current || providerWarningRevision(warning) >= providerWarningRevision(current)) {
      unique.set(key, warning);
    }
  }
  return [...unique.values()]
    .sort((left, right) => String(right.lastSeenAt || "").localeCompare(String(left.lastSeenAt || "")));
}

function deliveryLabel(status: string, blockedKind: string, t: Translate): string {
  if (status === "completed" || status === "materialized") return t("goal.backgroundCompleted");
  if (status === "denied") return t("goal.backgroundDenied");
  if (status === "failed") return t("goal.backgroundFailed");
  if (status === "parked") return t("goal.backgroundParked");
  if (status === "blocked" && blockedKind === "approval") return t("goal.backgroundApproval");
  if (status === "blocked" && blockedKind === "question") return t("goal.backgroundQuestion");
  return t("goal.backgroundUpdated");
}

function phaseLabel(phase: unknown, t: Translate): string {
  const key = PHASE_KEYS[normalizedSemanticValue(phase)] || "goal.backgroundPhaseUnknown";
  return t(key);
}

function failureKey(failure: unknown): string | undefined {
  const normalized = normalizedSemanticValue(failure)
    .replace(/^provider_/, "")
    .replace(/^watchdog_/, "");
  return FAILURE_KEYS[normalized]
    || (normalized.includes("timeout") ? "goal.backgroundFailureTimeout" : undefined);
}

function failureLabel(delivery: AgentGoalDelivery, t: Translate): string {
  const key = failureKey(delivery.failureLabel)
    || failureKey(delivery.failureClass)
    || "goal.backgroundFailureUnknown";
  return t(key);
}

function costUnavailableLabel(reason: unknown, t: Translate): string {
  const key = COST_UNAVAILABLE_KEYS[normalizedSemanticValue(reason)] || "goal.backgroundCostUnavailableUnknown";
  return t(key);
}

function providerWarningLabel(warningKey: unknown, t: Translate): string {
  const key = PROVIDER_WARNING_KEYS[normalizedSemanticValue(warningKey)]
    || "goal.backgroundProviderWarningUnknown";
  return t(key);
}

function formatExplicitCost(cost: number, currency: unknown): string {
  const explicitCurrency = String(currency || "").trim().toUpperCase();
  try {
    if (/^[A-Z]{3}$/.test(explicitCurrency)) {
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency: explicitCurrency,
        maximumFractionDigits: cost > 0 && cost < 0.01 ? 6 : 2,
      }).format(cost);
    }
  } catch {
    // Fall through to a bounded locale-aware decimal when a currency is invalid.
  }
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 6 }).format(cost);
}

function DeliveryIcon({ status, blockedKind }: { status: string; blockedKind: string }) {
  if (status === "completed" || status === "materialized") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
  if (status === "failed" || status === "denied") return <AlertTriangle className="h-4 w-4 text-destructive" />;
  if (blockedKind === "approval") return <ShieldCheck className="h-4 w-4 text-amber-500" />;
  if (blockedKind === "question" || status === "parked") return <HelpCircle className="h-4 w-4 text-amber-500" />;
  return <Clock3 className="h-4 w-4 text-muted-foreground" />;
}

export function BackgroundGoalCatchUpCard({
  deliveries,
  providerWarnings = [],
  onRendered,
  onProviderWarningsRendered,
  onDismiss,
}: {
  deliveries: AgentGoalDelivery[];
  providerWarnings?: AgentGoalProviderWarning[];
  onRendered?: (recaps: AgentGoalRenderedRecap[]) => void;
  onProviderWarningsRendered?: (warnings: AgentGoalBackgroundAcknowledgement[]) => void;
  onDismiss?: () => void;
}) {
  const { t } = useTranslation();
  const visible = deliveries.slice(0, 3);
  const warnings = visibleProviderWarnings(providerWarnings);
  const deliveryKey = visible
    .map((delivery) => `${recapKey(delivery)}:${finiteRevision(delivery.revision)}`)
    .join("|");
  const warningKey = warnings.map(providerWarningRevisionKey).join("|");

  useEffect(() => {
    if (!onRendered || !visible.length) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      onRendered(visible.map((delivery) => ({
        deliveryId: delivery.deliveryId,
        expectedRevision: finiteRevision(delivery.revision),
        recapRevision: recapRevision(delivery),
      })));
    });
    return () => window.cancelAnimationFrame(frame);
  }, [deliveryKey, onRendered]);

  useEffect(() => {
    if (!onProviderWarningsRendered || !warnings.length) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      onProviderWarningsRendered(warnings.map((warning) => ({
        deliveryId: warning.warningKey,
        expectedRevision: providerWarningRevision(warning),
      })));
    });
    return () => window.cancelAnimationFrame(frame);
  }, [onProviderWarningsRendered, warningKey]);

  if (!visible.length && !warnings.length) {
    return null;
  }
  return (
    <section
      className="rounded-xl border border-border bg-card/80 p-3 shadow-sm"
      data-background-goal-catch-up
      aria-label={t("goal.backgroundCatchUp")}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("goal.backgroundCatchUp")}
        </div>
        {onDismiss ? (
          <button
            type="button"
            className="rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            onClick={onDismiss}
            aria-label={t("goal.backgroundDismiss")}
            title={t("goal.backgroundDismiss")}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        ) : null}
      </div>
      <div className="mt-2 grid gap-2">
        {warnings.length ? (
          <div className="text-xs font-medium text-amber-600 dark:text-amber-400">
            {t("goal.backgroundProviderWarningTitle")}
          </div>
        ) : null}
        {warnings.map((warning) => {
          const provider = String(warning.provider || "").trim() || t("goal.backgroundProviderUnknown");
          const timestamp = warning.lastSeenAt || "";
          return (
            <div
              key={warning.warningKey}
              className="flex min-w-0 items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2"
              data-background-goal-provider-warning-key={providerWarningRevisionKey(warning)}
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">
                  {providerWarningLabel(warning.status || warning.warningKey, t)}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                  <span>
                    {t("goal.backgroundProviderWarningEvidence", {
                      provider,
                      count: providerWarningRevision(warning),
                    })}
                  </span>
                  {timestamp ? (
                    <time dateTime={timestamp}>
                      {t("goal.backgroundProviderWarningLastSeen", { time: new Date(timestamp).toLocaleString() })}
                    </time>
                  ) : null}
                </div>
              </div>
            </div>
          );
        })}
        {visible.map((delivery) => {
          const status = normalizedSemanticValue(delivery.state || delivery.status);
          const blockedKind = normalizedSemanticValue(delivery.blockedKind);
          const totalTokens = Number(delivery.usage?.totalTokens);
          const explicitCost = Number(delivery.usage?.cost);
          const hasExplicitCost = delivery.usage?.cost !== undefined && Number.isFinite(explicitCost) && explicitCost >= 0;
          const unavailableReason = delivery.usage?.costUnavailableReason;
          const hasSemanticFailure = Boolean(delivery.failureLabel || delivery.failureClass);
          const timestamp = delivery.updatedAt || delivery.completedAt || delivery.failedAt || delivery.deniedAt || "";
          return (
            <div
              key={delivery.deliveryId}
              className="flex min-w-0 items-start gap-2 rounded-lg bg-muted/45 px-3 py-2"
              data-background-goal-delivery-id={delivery.deliveryId}
              data-background-goal-recap-key={recapKey(delivery)}
              data-background-goal-status={status}
            >
              <span className="mt-0.5 shrink-0">
                <DeliveryIcon status={status} blockedKind={blockedKind} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{deliveryLabel(status, blockedKind, t)}</div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
                  <span>{t("goal.backgroundPhaseLabel", { phase: phaseLabel(delivery.phase, t) })}</span>
                  {hasSemanticFailure ? (
                    <span>{t("goal.backgroundFailureLabel", { failure: failureLabel(delivery, t) })}</span>
                  ) : null}
                  {delivery.attempt ? (
                    <span>{t("goal.backgroundAttempt", { current: delivery.attempt, max: delivery.maxAttempts || 3 })}</span>
                  ) : null}
                  {Number.isFinite(totalTokens) && totalTokens > 0 ? (
                    <span>{t("goal.backgroundTokens", { count: totalTokens })}</span>
                  ) : null}
                  {hasExplicitCost ? (
                    <span>{t("goal.backgroundCost", { cost: formatExplicitCost(explicitCost, delivery.usage?.currency) })}</span>
                  ) : (
                    <span>
                      {t("goal.backgroundCostUnavailable", {
                        reason: costUnavailableLabel(unavailableReason, t),
                      })}
                    </span>
                  )}
                  {timestamp ? <time dateTime={timestamp}>{new Date(timestamp).toLocaleString()}</time> : null}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
