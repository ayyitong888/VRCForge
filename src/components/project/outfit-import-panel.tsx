import { Bot, ChevronDown, ChevronRight, FolderPlus, Loader2, Search, Shield } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { OutfitImportPlanResult } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

export function OutfitImportPanel({
  projectPath,
  packagePath,
  result,
  status,
  loading,
  requesting,
  onPackagePathChange,
  onPlan,
  onRequest,
  onReview,
}: {
  projectPath: string;
  packagePath: string;
  result: OutfitImportPlanResult | null;
  status: string;
  loading: boolean;
  requesting: boolean;
  onPackagePathChange: (value: string) => void;
  onPlan: () => void;
  onRequest: () => void;
  onReview: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!projectPath) {
    return null;
  }
  const plan = result?.plan;
  const summary = result?.inspection?.summary;
  const ready = Boolean(plan?.readyToApply);
  const hasResult = Boolean(result);
  const tone: "ok" | "warn" | "danger" | "muted" = !hasResult ? "muted" : result?.ok && ready ? "ok" : result?.ok ? "warn" : "danger";
  const label = !hasResult ? t("outfit.statusPending") : ready ? t("outfit.statusRequestable") : result?.ok ? t("outfit.statusNeedsConfirm") : t("outfit.statusBlocked");
  const expected = plan?.expectedAssetPaths || [];
  const dependencyPreflight = result?.dependencyPreflight || plan?.dependencyPreflight;
  const dependencyEntries = dependencyPreflight?.entries || [];
  const visibleDependencyEntries = dependencyEntries.filter((entry) => entry.status && entry.status !== "not_detected");
  const packageOrder = dependencyPreflight?.packageOrder;
  const importQueue = packageOrder?.importQueue || plan?.source?.importQueue || [];
  const skippedInstalledSupportPackages = packageOrder?.skippedInstalledSupportPackages || [];
  const compatibility = dependencyPreflight?.compatibility;
  const dependencySummary = dependencyPreflight
    ? `${dependencyPreflight.readyForImport ? t("connector.ready") : "blocked"} / ${dependencyPreflight.blockingIssueCount || dependencyPreflight.blockingMissingCount || 0} issue(s) / ${
        dependencyPreflight.detectedCount || visibleDependencyEntries.length
      } detected`
    : "";
  return (
    <section className="mb-4 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => setOpen((value) => !value)}>
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <FolderPlus className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("outfit.title")}</span>
          <Badge tone={tone} className="shrink-0">
            {label}
          </Badge>
          {summary ? (
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {t("outfit.summary", { pkg: summary.unityPackageCount || 0, prefab: summary.prefabCandidateCount || 0 })}
            </span>
          ) : null}
        </button>
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          <div className="flex min-w-0 gap-2">
            <input
              value={packagePath}
              onChange={(event) => onPackagePathChange(event.target.value)}
              placeholder=".unitypackage / Booth folder / prefab folder"
              className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
            <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading || !packagePath.trim()} onClick={onPlan}>
              {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
              Plan
            </Button>
            <Button type="button" className="h-9 shrink-0 px-3 text-xs" disabled={requesting || !ready} onClick={onRequest}>
              {requesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
              Request
            </Button>
            <Button
              type="button"
              variant="ghost"
              className="h-9 shrink-0 px-3 text-xs"
              disabled={loading || (!packagePath.trim() && !result)}
              onClick={onReview}
              title={t("outfit.reviewPlan")}
            >
              <Bot className="h-3.5 w-3.5" />
              {t("outfit.review")}
            </Button>
          </div>
          {status ? <DataLine label={t("connector.status")} value={status} /> : null}
          {plan?.kind ? <DataLine label={t("encryption.plan")} value={plan.kind} /> : null}
          {dependencySummary ? <DataLine label="Dependency preflight" value={dependencySummary} /> : null}
          {compatibility ? (
            <DataLine
              label="Avatar compatibility"
              value={`${compatibility.status || t("optimization.unknown")}${compatibility.message ? ` - ${compatibility.message}` : ""}`}
            />
          ) : null}
          {importQueue.length ? (
            <OutputBlock
              label="Import order"
              value={importQueue
                .map((item, index) => `${item.order || index + 1}. ${item.role || "package"} ${item.path || item.actualPackagePath || ""}`)
                .join("\n")}
            />
          ) : null}
          {skippedInstalledSupportPackages.length ? (
            <OutputBlock
              label="Skipped packages"
              value={skippedInstalledSupportPackages
                .map(
                  (item) =>
                    `${item.dependencyLabel || item.dependencyId || "dependency"}: ${item.path || item.actualPackagePath || ""}${
                      item.message ? `\n  ${item.message}` : ""
                    }`,
                )
                .join("\n")}
            />
          ) : null}
          {visibleDependencyEntries.length ? (
            <OutputBlock
              label="Dependencies"
              value={visibleDependencyEntries
                .map((entry) => {
                  const evidence = [
                    ...(entry.evidence?.project || []),
                    ...(entry.evidence?.packagePathnames || []),
                    ...(entry.evidence?.hints || []),
                  ];
                  return `${entry.status || t("optimization.unknown")} ${entry.label || entry.id || "dependency"}${entry.blockingBeforeImport ? " [before import]" : ""}${
                    evidence.length ? `\n  ${evidence.slice(0, 3).join("\n  ")}` : ""
                  }`;
                })
                .join("\n")}
            />
          ) : null}
          {plan?.targetFolder ? <DataLine label={t("recovery.target")} value={plan.targetFolder} mono /> : null}
          {plan?.selectedPrefab ? <DataLine label="Prefab" value={plan.selectedPrefab} mono /> : null}
          {expected.length ? <OutputBlock label="Expected assets" value={expected.slice(0, 20).join("\n")} /> : null}
          {plan?.steps?.length ? (
            <OutputBlock
              label="Steps"
              value={plan.steps
                .map((step) => `${step.enabled === false ? "[off]" : "[on]"} ${step.category || ""} ${step.tool || step.id || ""}`.trim())
                .join("\n")}
            />
          ) : null}
          {result?.warnings?.length ? <OutputBlock label="Warnings" value={result.warnings.join("\n")} /> : null}
        </div>
      ) : null}
    </section>
  );
}
