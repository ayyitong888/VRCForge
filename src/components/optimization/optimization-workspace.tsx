import { AlertTriangle, Download, Eye, Gauge, History, Loader2, RefreshCw, RotateCcw, Shield, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n from "../../i18n";
import type { AvatarListItem, OptimizationPlannerReport, OptimizationProofDetail, OptimizationProofSummary, PermissionState } from "../../lib/api";
import {
  isMeshiaOptimizationRequest,
  isTttOptimizationRequest,
  optimizationActionMissingRequiredOptions,
  type OptimizationActionOptions,
} from "../../lib/optimization-options";
import { permissionVisualState } from "../../lib/permission-ui";
import { isInternalRuntimeUrl } from "../../lib/runtime-url";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

const OPTIMIZATION_TARGET_PROFILES = [
  { id: "pc_conservative", label: "PC Conservative" },
  { id: "pc_medium", label: "PC Medium" },
  { id: "quest_medium", label: "Quest Medium" },
  { id: "event_light", label: "Event Light" },
  { id: "custom", label: "Custom" },
];

type OptimizationActionCardItem = NonNullable<OptimizationPlannerReport["actionCards"]>[number];

export function OptimizationWorkspace({
  report,
  proofs,
  selectedProof,
  endpoint,
  permission,
  selectedProjectPath,
  avatarPath,
  avatars,
  targetProfile,
  loading,
  loadingProofs,
  loadingAvatars,
  message,
  proofMessage,
  avatarMessage,
  actionOptions,
  requestingActionId,
  requestingDependencyId,
  onAvatarPathChange,
  onTargetProfileChange,
  onRefresh,
  onRefreshProofs,
  onSelectProof,
  onRefreshAvatars,
  onActionOptionChange,
  onRequestAction,
  onRequestDependency,
}: {
  report: OptimizationPlannerReport | null;
  proofs: OptimizationProofSummary[];
  selectedProof: OptimizationProofDetail | null;
  endpoint: string;
  permission?: PermissionState;
  selectedProjectPath: string;
  avatarPath: string;
  avatars: AvatarListItem[];
  targetProfile: string;
  loading: boolean;
  loadingProofs: boolean;
  loadingAvatars: boolean;
  message: string;
  proofMessage: string;
  avatarMessage: string;
  actionOptions: Record<string, OptimizationActionOptions>;
  requestingActionId: string;
  requestingDependencyId: string;
  onAvatarPathChange: (value: string) => void;
  onTargetProfileChange: (profile: string) => void;
  onRefresh: () => void;
  onRefreshProofs: () => void;
  onSelectProof: (runId: string) => void;
  onRefreshAvatars: () => void;
  onActionOptionChange: (actionId: string, key: keyof OptimizationActionOptions, value: string) => void;
  onRequestAction: (card: NonNullable<OptimizationPlannerReport["actionCards"]>[number]) => void;
  onRequestDependency: (dependency: NonNullable<NonNullable<OptimizationPlannerReport["dependencyDoctor"]>["dependencies"]>[number]) => void;
}) {
  const dependencies = report?.dependencyDoctor?.dependencies ?? [];
  const actions = report?.actionCards ?? [];
  const offenders = report?.topOffenders ?? [];
  const metrics = report?.baseline?.metrics ?? {};
  const profile = report?.targetProfile;
  const optimizerApproval = optimizerApprovalBadge(permission);
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6">
        <section className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <Gauge className="h-4 w-4 shrink-0 text-primary" />
              <h1 className="truncate text-lg font-semibold">{i18n.t("optimization.title")}</h1>
              <Badge tone="muted" className="shrink-0">
                {report?.versionStage || "0.7.2-beta"}
              </Badge>
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{selectedProjectPath || i18n.t("encryption.noUnityProject")}</div>
          </div>
          <Badge tone={report?.readOnly && report?.noProjectWrites ? "ok" : "warn"} className="shrink-0">
            {report?.readOnly && report?.noProjectWrites ? i18n.t("optimization.readOnly") : i18n.t("encryption.needsReview")}
          </Badge>
          <Badge tone={report?.directApplyExposed ? "danger" : "muted"} className="shrink-0">
            {report?.directApplyExposed ? i18n.t("optimization.directApplyExposed") : i18n.t("optimization.noDirectApply")}
          </Badge>
          <Badge tone={optimizerApproval.modeTone} className="shrink-0">
            mode: {permission?.executionMode || "approval"}
          </Badge>
          <Button type="button" variant="outline" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(320px,0.9fr)]">
          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("optimization.targetProfile")}</div>
              {profile?.label ? (
                <Badge tone="default" className="ml-auto shrink-0">
                  {profile.label}
                </Badge>
              ) : null}
            </div>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
              {OPTIMIZATION_TARGET_PROFILES.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onTargetProfileChange(item.id)}
                  className={cn(
                    "h-10 min-w-0 rounded-md border px-3 text-sm transition-colors",
                    targetProfile === item.id ? "border-primary bg-primary/5 text-foreground" : "border-border text-muted-foreground hover:bg-muted",
                  )}
                >
                  <span className="block truncate">{item.label}</span>
                </button>
              ))}
            </div>
            <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
              <select
                value={avatars.some((item) => item.avatarPath === avatarPath) ? avatarPath : ""}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                disabled={loadingAvatars || avatars.length === 0}
                className="h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:text-muted-foreground"
              >
                <option value="">{loadingAvatars ? i18n.t("encryption.scanningAvatars") : avatars.length ? i18n.t("encryption.selectAvatar") : i18n.t("encryption.noSceneAvatars")}</option>
                {avatars.map((avatar, index) => {
                  const value = avatar.avatarPath || "";
                  return (
                    <option key={`${value}-${index}`} value={value}>
                      {avatarOptionLabel(avatar)}
                    </option>
                  );
                })}
              </select>
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loadingAvatars} onClick={onRefreshAvatars}>
                {loadingAvatars ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Avatars
              </Button>
            </div>
            <div className="mt-2 flex min-w-0 items-center gap-2">
              <input
                value={avatarPath}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                placeholder={i18n.t("encryption.avatarScenePath")}
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading} onClick={onRefresh}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Scan
              </Button>
            </div>
            {avatarMessage ? <div className="mt-2 truncate text-xs text-muted-foreground">{avatarMessage}</div> : null}
            <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              <OptimizationMetric label={i18n.t("optimization.pcRank")} value={report?.baseline?.performanceHeadline?.pc?.rank || i18n.t("optimization.unknown")} />
              <OptimizationMetric label={i18n.t("optimization.questRank")} value={report?.baseline?.performanceHeadline?.quest?.rank || i18n.t("optimization.unknown")} />
              <OptimizationMetric label={i18n.t("optimization.triangles")} value={formatOptimizationMetric(metrics.triangleCount)} />
              <OptimizationMetric label={i18n.t("optimization.parameterBits")} value={formatOptimizationMetric(metrics.expressionParameterBits)} />
            </div>
          </div>

          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("optimization.topOffenders")}</div>
              <Badge tone="muted" className="ml-auto shrink-0">
                {offenders.length}
              </Badge>
            </div>
            <div className="grid gap-2">
              {offenders.map((item) => (
                <div key={item.id || item.label} className="flex min-w-0 items-center gap-2 rounded-md border border-border px-3 py-2">
                  <div className="min-w-0 flex-1 truncate text-sm">{item.label || item.id}</div>
                  <Badge tone={offenderTone(item.severity)} className="shrink-0">
                    {item.count ?? 0}
                  </Badge>
                </div>
              ))}
              {message ? <div className="truncate text-xs text-muted-foreground">{message}</div> : null}
            </div>
          </div>
        </section>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          <OptimizationMetric label={i18n.t("optimization.textureBytes")} value={formatOptimizationMetric(metrics.textureMemoryBytes)} />
          <OptimizationMetric label={i18n.t("optimization.materialSlots")} value={formatOptimizationMetric(metrics.materialSlots)} />
          <OptimizationMetric label={i18n.t("optimization.skinnedMeshes")} value={formatOptimizationMetric(metrics.skinnedMeshCount)} />
          <OptimizationMetric label={i18n.t("optimization.physBones")} value={formatOptimizationMetric(metrics.physBones)} />
          <OptimizationMetric label={i18n.t("optimization.generatedResidue")} value={formatOptimizationMetric(metrics.generatedResidueCount)} />
        </section>

        <OptimizationProofReadiness report={report} />
        <OptimizationProofViewer
          proofs={proofs}
          selectedProof={selectedProof}
          endpoint={endpoint}
          loading={loadingProofs}
          message={proofMessage}
          onRefresh={onRefreshProofs}
          onSelectProof={onSelectProof}
        />

        <section>
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <h2 className="truncate text-sm font-semibold">{i18n.t("optimization.dependencyStatus")}</h2>
            <Badge tone="muted" className="shrink-0">
              {dependencies.length}
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {dependencies.map((item) => (
              <div key={item.id || item.label} className="min-w-0 rounded-lg border border-border bg-card p-3">
                <div className="flex min-w-0 items-center gap-2">
                  <div className="min-w-0 flex-1 truncate text-sm font-medium">{item.label || item.id}</div>
                  <Badge tone={dependencyTone(item.status)} className="shrink-0">
                    {item.status || i18n.t("optimization.unknown")}
                  </Badge>
                </div>
                <div className="mt-2 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label={i18n.t("subagent.roles.outfitPackageInspection")} value={item.matchedPackageId || "-"} />
                  <DataLine label="Version" value={item.version || "-"} />
                  <DataLine label={i18n.t("package.tableRisk")} value={item.riskLevel || "-"} />
                </div>
                <div className="mt-2 max-h-10 overflow-hidden text-xs text-muted-foreground">{item.recommendedRole || "-"}</div>
                {item.status !== "installed" && item.packageIds?.length ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="mt-3 h-8 px-3 text-xs"
                    disabled={loading || !selectedProjectPath || requestingDependencyId === (item.id || item.packageIds[0])}
                    onClick={() => onRequestDependency(item)}
                  >
                    {requestingDependencyId === (item.id || item.packageIds[0]) ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
                    Install request
                  </Button>
                ) : null}
              </div>
            ))}
          </div>
        </section>

        <section>
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <h2 className="truncate text-sm font-semibold">{i18n.t("optimization.recommendedOrder")}</h2>
            <Badge tone="muted" className="shrink-0">
              {report?.recommendedOrder?.length ?? 0}
            </Badge>
          </div>
          <div className="grid gap-3 lg:grid-cols-2">
            {actions.map((card) => (
              <div key={card.id} className={cn("min-w-0 rounded-lg border bg-card p-4", card.enabled ? "border-border" : "border-border opacity-70")}>
                <div className="flex min-w-0 items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-semibold">{card.title}</div>
                    <div className="mt-1 text-xs text-muted-foreground">{card.description}</div>
                  </div>
                  <Badge tone={optimizationRiskTone(card.riskLevel)} className="shrink-0">
                    {card.riskLevel || i18n.t("optimization.unknown")}
                  </Badge>
                </div>
                <div className="mt-3 flex min-w-0 flex-wrap gap-2">
                  <Badge tone={card.level === "read-only" ? "ok" : "muted"}>{card.level || "plan-only"}</Badge>
                  <Badge tone="muted">{card.dependency || "VRCForge"}</Badge>
                  <Badge tone="muted">{card.recommendedVersionStage || "0.7.2-beta"}</Badge>
                  {card.requestTool ? <Badge tone={optimizerApproval.requestTone}>{optimizerApproval.requestLabel}</Badge> : null}
                </div>
                <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label="Benefit" value={card.expectedBenefit || i18n.t("optimization.unknown")} />
                  <DataLine label={i18n.t("doctor.why")} value={card.whyRecommended || "-"} />
                  <DataLine label="Next" value={card.nextSafeAction || "-"} />
                  {card.requestTool ? <DataLine label={i18n.t("optimization.request")} value={card.requestTool} /> : null}
                  {card.blockedReason ? <DataLine label={i18n.t("package.labels.blocked")} value={card.blockedReason} /> : null}
                </div>
                {isTttOptimizationRequest(card) ? (
                  <textarea
                    value={actionOptions[card.id]?.atlasTargetMaterials ?? ""}
                    onChange={(event) => onActionOptionChange(card.id, "atlasTargetMaterials", event.target.value)}
                    placeholder="Assets/.../Material.mat"
                    rows={2}
                    className="mt-3 min-h-16 w-full resize-y rounded-md border border-border bg-background px-3 py-2 text-xs outline-none focus:border-primary"
                  />
                ) : null}
                {isMeshiaOptimizationRequest(card) ? (
                  <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_8rem]">
                    <input
                      value={actionOptions[card.id]?.rendererPath ?? ""}
                      onChange={(event) => onActionOptionChange(card.id, "rendererPath", event.target.value)}
                      placeholder={i18n.t("optimization.rendererPath")}
                      className="h-8 min-w-0 rounded-md border border-border bg-background px-3 text-xs outline-none focus:border-primary"
                    />
                    <input
                      value={actionOptions[card.id]?.relativeVertexCount ?? "0.9"}
                      onChange={(event) => onActionOptionChange(card.id, "relativeVertexCount", event.target.value)}
                      type="number"
                      min="0.75"
                      max="1"
                      step="0.05"
                      className="h-8 min-w-0 rounded-md border border-border bg-background px-3 text-xs outline-none focus:border-primary"
                    />
                  </div>
                ) : null}
                {card.requestTool ? (
                  <div className="mt-3 flex min-w-0 flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      className="h-8 px-3 text-xs"
                      disabled={
                        loading ||
                        !selectedProjectPath ||
                        !avatarPath.trim() ||
                        optimizationActionMissingRequiredOptions(card, actionOptions[card.id] ?? {}) ||
                        requestingActionId === card.id
                      }
                      onClick={() => onRequestAction(card)}
                    >
                      {requestingActionId === card.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                      Request
                    </Button>
                    <Badge tone={optimizerApproval.requestTone} className="h-8 shrink-0">
                      {optimizerApproval.requestLabel}
                    </Badge>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function optimizerApprovalBadge(permission?: PermissionState) {
  const visual = permissionVisualState(permission);
  if (visual.tier === "full") {
    return {
      modeTone: visual.badgeTone,
      requestTone: visual.badgeTone,
      requestLabel: i18n.t("optimization.explicitApproval"),
    };
  }
  if (visual.tier === "auto") {
    return {
      modeTone: visual.badgeTone,
      requestTone: visual.badgeTone,
      requestLabel: i18n.t("optimization.explicitApproval"),
    };
  }
  return {
    modeTone: visual.badgeTone,
    requestTone: visual.badgeTone,
    requestLabel: i18n.t("encryption.approvalRequired"),
  };
}

function OptimizationMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-card px-3 py-2">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function OptimizationProofReadiness({ report }: { report: OptimizationPlannerReport | null }) {
  const { t } = useTranslation();
  const plans = optimizationRecord(report?.plans);
  const visual = optimizationRecord(plans.visualRegression);
  const rollback = optimizationRecord(plans.rollbackVerify);
  const parameterRegression = optimizationRecord(plans.parameterBehaviorRegression);
  const parameterPath = optimizationRecord(plans.parameterPathToSkill);
  const ma2bt = optimizationRecord(plans.ma2btConvertibility);
  const visualShots = optimizationArray(visual.shots);
  const visualPlayModeShots = visualShots.filter((item) => Boolean(optimizationRecord(item).requiresPlayMode));
  const rollbackReady = Boolean(rollback.canGenerateFutureProof);
  const parameterSummary = optimizationRecord(parameterRegression.summary);
  const parameterGates = optimizationRecord(parameterPath.hardGates);
  const ma2btSummary = optimizationRecord(ma2bt.summary);
  const ma2btDiagnostics = optimizationArray(ma2bt.diagnostics);
  const cards = [
    {
      id: "visual",
      icon: Eye,
      title: t("optimization.visualProof"),
      tone: visualShots.length ? ("ok" as const) : ("warn" as const),
      lines: [
        ["Shots", `${visualShots.length}`],
        ["Play Mode", `${visualPlayModeShots.length}`],
        ["Scoring", optimizationRecord(visual.scoring).mode ? String(optimizationRecord(visual.scoring).mode) : "not-run"],
      ],
    },
    {
      id: "rollback",
      icon: RotateCcw,
      title: t("optimization.rollbackProof"),
      tone: rollbackReady ? ("ok" as const) : ("warn" as const),
      lines: [
        ["Project", rollback.projectReadable ? "readable" : "not ready"],
        ["Residue", formatOptimizationMetric(rollback.generatedResidueCount)],
        ["Checkpoint", rollback.checkpointInfrastructureRequired ? "required" : t("optimization.unknown")],
      ],
    },
    {
      id: "parameters",
      icon: Shield,
      title: t("optimization.parameterGates"),
      tone: Number(parameterSummary.dangerParameterCount || parameterGates.blockedParameterCount || 0) ? ("warn" as const) : ("ok" as const),
      lines: [
        ["Cases", formatOptimizationMetric(parameterSummary.testCaseCount)],
        ["Blocked", formatOptimizationMetric(parameterGates.blockedParameterCount ?? parameterSummary.dangerParameterCount)],
        ["Apply", parameterPath.applyBlocked ? "blocked" : t("optimization.review")],
      ],
    },
    {
      id: "ma2bt",
      icon: Sparkles,
      title: t("optimization.ma2btDiagnostics"),
      tone: Number(ma2btSummary.skippedLayerCount || 0) ? ("warn" as const) : ("ok" as const),
      lines: [
        ["Convertible", formatOptimizationMetric(ma2btSummary.convertibleLayerCount)],
        ["Skipped", formatOptimizationMetric(ma2btSummary.skippedLayerCount)],
        ["Reasons", `${ma2btDiagnostics.length}`],
      ],
    },
  ];
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="truncate text-sm font-semibold">{t("optimization.proofReadiness")}</h2>
        <Badge tone="muted" className="ml-auto shrink-0">
          {t("optimization.gates09")}
        </Badge>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <div key={card.id} className="min-w-0 rounded-lg border border-border bg-background p-3">
              <div className="mb-2 flex min-w-0 items-center gap-2">
                <Icon className="h-4 w-4 shrink-0 text-primary" />
                <div className="min-w-0 flex-1 truncate text-sm font-medium">{card.title}</div>
                <Badge tone={card.tone} className="shrink-0">
                  {card.tone === "ok" ? t("connector.ready") : t("optimization.review")}
                </Badge>
              </div>
              <div className="grid gap-1 text-xs text-muted-foreground">
                {card.lines.map(([label, value]) => (
                  <DataLine key={label} label={label} value={value || "-"} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function OptimizationProofViewer({
  proofs,
  selectedProof,
  endpoint,
  loading,
  message,
  onRefresh,
  onSelectProof,
}: {
  proofs: OptimizationProofSummary[];
  selectedProof: OptimizationProofDetail | null;
  endpoint: string;
  loading: boolean;
  message: string;
  onRefresh: () => void;
  onSelectProof: (runId: string) => void;
}) {
  const { t } = useTranslation();
  const proof = selectedProof?.proof || proofs[0] || null;
  const visual = optimizationRecord(proof?.visualRegression);
  const screenshots = optimizationRecord(visual.screenshots);
  const profile = optimizationRecord(proof?.profileDiff);
  const pc = optimizationRecord(profile.pc);
  const quest = optimizationRecord(profile.quest);
  const parameters = optimizationRecord(proof?.parameterBudgetDelta);
  const rollback = optimizationRecord(proof?.rollbackProof);
  const stageIds = ["before", "after_apply", "after_rollback"];
  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
      <div className="mb-3 flex min-w-0 flex-wrap items-center gap-2">
        <History className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{t("optimization.optimizerProof")}</h2>
        {message ? (
          <Badge tone="muted" className="shrink-0">
            {message}
          </Badge>
        ) : null}
        <Button type="button" variant="ghost" className="h-8 px-2 text-xs" disabled={loading} onClick={onRefresh}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
      </div>

      {!proof ? (
        <div className="rounded-md border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">{t("optimization.noProofRuns")}</div>
      ) : (
        <div className="grid gap-4">
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_220px]">
            <div className="grid gap-2 rounded-lg border border-border bg-background p-3">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge tone={proof.ok ? "ok" : "danger"} className="shrink-0">
                  {proof.status || (proof.ok ? "passed" : "failed")}
                </Badge>
                <span className="min-w-0 flex-1 truncate text-sm font-medium">{proof.runId}</span>
              </div>
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
                <DataLine label={t("proof.tool")} value={proof.tool || "-"} />
                <DataLine label={t("recovery.checkpoint")} value={proof.checkpointId || "-"} mono />
                <DataLine label={t("checkpoint.changedFiles")} value={formatOptimizationMetric(proof.changedFileCount)} />
                <DataLine label={t("optimization.proofMetric.rollback")} value={proof.rollbackDone ? t("proof.rollbackDone") : t("proof.rollbackNotDone")} />
              </div>
            </div>
            <select
              value={proof.runId}
              disabled={loading || proofs.length === 0}
              onChange={(event) => onSelectProof(event.target.value)}
              className="h-10 min-w-0 self-start rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            >
              {proofs.map((item) => (
                <option key={item.runId} value={item.runId}>
                  {item.status || "proof"} / {item.tool || item.runId}
                </option>
              ))}
            </select>
          </div>

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <ProofMetricCard title={t("optimization.pcRank")} before={pc.rankBefore} after={pc.rankAfter} rollback={pc.rankRollback} />
            <ProofMetricCard title={t("optimization.questRank")} before={quest.rankBefore} after={quest.rankAfter} rollback={quest.rankRollback} />
            <ProofMetricCard title={t("optimization.parameterBits")} before="delta" after={parameters.syncedBitsDelta} rollback={parameters.rollbackMatchesBefore ? "matched" : t("optimization.review")} />
            <ProofMetricCard title="Rollback gate" before="severity/gate" after={rollback.matchesBeforeSeverityAndGate ? "matched" : t("optimization.review")} rollback={rollback.remainingFindingCount ?? "-"} />
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            {stageIds.map((stage) => {
              const entry = optimizationRecord(screenshots[stage]);
              const imageUrl = proofImageUrl(endpoint, entry.imageUrl);
              const ok = Boolean(entry.artifactOk || entry.exists);
              return (
                <div key={stage} className="grid min-w-0 gap-2 rounded-lg border border-border bg-background p-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <Eye className="h-4 w-4 shrink-0 text-primary" />
                    <div className="min-w-0 flex-1 truncate text-sm font-medium">{proofStageLabel(stage)}</div>
                    <Badge tone={ok ? "ok" : "warn"} className="shrink-0">
                      {ok ? t("optimization.captured") : t("optimization.missing")}
                    </Badge>
                  </div>
                  {imageUrl ? (
                    <div className="aspect-square overflow-hidden rounded-md border border-border bg-muted">
                      <img src={imageUrl} alt={proofStageLabel(stage)} className="h-full w-full object-contain" />
                    </div>
                  ) : (
                    <div className="grid aspect-square place-items-center rounded-md border border-dashed border-border text-xs text-muted-foreground">{t("optimization.noScreenshot")}</div>
                  )}
                  <div className="grid gap-1 text-xs text-muted-foreground">
                    <DataLine label="SHA" value={String(entry.sha256 || "-")} mono />
                    <DataLine label="Size" value={formatOptimizationMetric(entry.size)} />
                  </div>
                </div>
              );
            })}
          </div>
          {proof.failedSteps?.length ? <OutputBlock label={t("optimization.failedSteps")} value={proof.failedSteps.join("\n")} /> : null}
        </div>
      )}
    </section>
  );
}

function ProofMetricCard({ title, before, after, rollback }: { title: string; before: unknown; after: unknown; rollback: unknown }) {
  const { t } = useTranslation();
  return (
    <div className="min-w-0 rounded-lg border border-border bg-background p-3">
      <div className="mb-2 truncate text-sm font-medium">{title}</div>
      <div className="grid gap-1 text-xs text-muted-foreground">
        <DataLine label={t("optimization.proofStages.before")} value={formatProofValue(before)} />
        <DataLine label={t("optimization.proofMetric.after")} value={formatProofValue(after)} />
        <DataLine label={t("optimization.proofMetric.rollback")} value={formatProofValue(rollback)} />
      </div>
    </div>
  );
}

function formatOptimizationMetric(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value >= 1000 ? Math.round(value).toLocaleString() : String(value);
  }
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return "unknown";
}

function optimizationRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function optimizationArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function proofStageLabel(stage: string): string {
  if (stage === "after_apply") {
    return "After apply";
  }
  if (stage === "after_rollback") {
    return "After rollback";
  }
  return "Before";
}

function proofImageUrl(endpoint: string, value: unknown): string {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  if (/^https?:\/\//i.test(raw)) {
    if (isInternalRuntimeUrl(raw)) {
      return "";
    }
    return raw;
  }
  if (raw.startsWith("/")) {
    if (isInternalRuntimeUrl(raw)) {
      return "";
    }
    return `${endpoint.replace(/\/$/, "")}${raw}`;
  }
  return raw;
}

function formatProofValue(value: unknown): string {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? i18n.t("proof.yes") : i18n.t("proof.no");
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}

function avatarOptionLabel(avatar: AvatarListItem): string {
  const name = avatar.avatarName || shortPath(avatar.avatarPath || "") || "Avatar";
  const parts = [name];
  if (avatar.sceneName) {
    parts.push(avatar.sceneName);
  }
  const stats: string[] = [];
  if (typeof avatar.rendererCount === "number") {
    stats.push(`${avatar.rendererCount} renderers`);
  }
  if (typeof avatar.blendshapeCount === "number") {
    stats.push(`${avatar.blendshapeCount} blendshapes`);
  }
  if (stats.length) {
    parts.push(stats.join(", "));
  }
  return parts.join(" - ");
}

function shortPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || path;
}

function dependencyTone(status?: string) {
  const value = String(status || "").toLowerCase();
  if (value === "installed") {
    return "ok" as const;
  }
  if (value === "missing") {
    return "warn" as const;
  }
  return "muted" as const;
}

function optimizationRiskTone(risk?: string) {
  const value = String(risk || "").toLowerCase();
  if (value === "high" || value.includes("danger")) {
    return "danger" as const;
  }
  if (value === "medium") {
    return "warn" as const;
  }
  if (value === "low") {
    return "ok" as const;
  }
  return "muted" as const;
}

function offenderTone(severity?: string) {
  const value = String(severity || "").toLowerCase();
  if (value.includes("error") || value.includes("danger")) {
    return "danger" as const;
  }
  if (value.includes("warn")) {
    return "warn" as const;
  }
  if (value.includes("suggest")) {
    return "ok" as const;
  }
  return "muted" as const;
}
