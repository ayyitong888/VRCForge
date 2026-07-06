import { AlertTriangle, ChevronDown, ChevronRight, Copy, Download, Loader2, RefreshCw, Settings, Shield, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DoctorCheck, DoctorReport } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

export function DoctorWorkspace({
  report,
  loading,
  message,
  repairingUnityBridge,
  exportingSupportBundle,
  onRefresh,
  onRepairUnityBridge,
  onOpenSettings,
  onExportSupportBundle,
  onCopy,
  formatPayload,
}: {
  report: DoctorReport | null;
  loading: boolean;
  message: string;
  repairingUnityBridge: boolean;
  exportingSupportBundle: boolean;
  onRefresh: () => void;
  onRepairUnityBridge: () => void;
  onOpenSettings: () => void;
  onExportSupportBundle: () => void;
  onCopy: () => void;
  formatPayload: (value: unknown) => string;
}) {
  const { t } = useTranslation();
  const summary = report?.summary;
  const checks = report?.checks ?? [];
  const suggestedFixes = checks.filter((check) => check.status !== "ok" && (check.fixCommand || check.howToFix)).slice(0, 8);
  const groupedChecks = useMemo(() => groupDoctorChecks(checks, report?.sections), [checks, report?.sections]);
  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <section className="rounded-xl border border-border bg-card p-5 shadow-panel">
          <div className="flex min-w-0 items-center gap-3">
            <Shield className="h-4 w-4 shrink-0 text-primary" />
            <div className="min-w-0 flex-1">
              <h1 className="truncate text-lg font-semibold">{t("doctor.title")}</h1>
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="truncate">{report?.version || "runtime"}</span>
                {report?.scope ? <span className="truncate">{report.scope}</span> : null}
                {report?.selectedUnityEnvironment?.label ? <span className="truncate">{report.selectedUnityEnvironment.label}</span> : null}
              </div>
            </div>
            <Badge tone={report?.ok ? "ok" : "warn"} className="shrink-0">
              {report?.ok ? t("doctor.ready") : t("connector.needsAttention")}
            </Badge>
          </div>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <DoctorSummaryTile label={t("doctor.ok")} value={summary?.okCount ?? 0} tone="ok" />
            <DoctorSummaryTile label={t("doctor.warning")} value={summary?.warningCount ?? 0} tone="warn" />
            <DoctorSummaryTile label={t("doctor.error")} value={summary?.errorCount ?? 0} tone="danger" />
            <DoctorSummaryTile label={t("doctor.unknown")} value={summary?.unknownCount ?? 0} tone="muted" />
          </div>
          <div className="mt-5 flex flex-wrap justify-end gap-2">
            {message ? (
              <Badge tone="ok" className="mr-auto shrink-0">
                {message}
              </Badge>
            ) : null}
            <Button type="button" variant="outline" onClick={onOpenSettings}>
              <Settings className="h-4 w-4" />
              {t("doctor.settings")}
            </Button>
            <Button type="button" variant="outline" onClick={onExportSupportBundle} disabled={exportingSupportBundle}>
              {exportingSupportBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
              {t("doctor.supportBundle")}
            </Button>
            <Button type="button" variant="outline" onClick={onCopy} disabled={!report}>
              <Copy className="h-4 w-4" />
              {t("connector.copy")}
            </Button>
            <Button type="button" onClick={onRefresh} disabled={loading}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              Retry
            </Button>
          </div>
        </section>

        {suggestedFixes.length > 0 ? (
          <section className="rounded-xl border border-border bg-card p-5 shadow-panel">
            <div className="mb-4 flex min-w-0 items-center gap-2">
              <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
              <h2 className="truncate text-sm font-semibold">{t("doctor.suggestedFixes")}</h2>
              <Badge tone="warn" className="ml-auto shrink-0">
                {suggestedFixes.length}
              </Badge>
            </div>
            <div className="grid gap-2">
              {suggestedFixes.map((check) => (
                <div key={`fix-${check.id}`} className="grid gap-1 rounded-lg border border-border bg-background px-3 py-2 text-sm">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-medium">{check.title}</span>
                    <Badge tone={doctorTone(check.status)} className="h-6 shrink-0">
                      {doctorStatusLabel(check.status)}
                    </Badge>
                  </div>
                  <div className="break-words text-xs text-muted-foreground">{check.fixCommand || check.howToFix}</div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <div className="grid gap-6">
          {checks.length === 0 ? (
            <div className="rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground shadow-panel">
              {loading ? t("doctor.running") : t("doctor.noResults")}
            </div>
          ) : null}
          {groupedChecks.map((group) => (
            <section key={group.name} className="grid gap-3">
              <div className="flex min-w-0 items-center gap-2 px-1">
                <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">{group.name}</h2>
                <Badge tone={group.summary.errorCount > 0 ? "danger" : group.summary.warningCount > 0 ? "warn" : "muted"} className="shrink-0">
                  {group.items.length}
                </Badge>
              </div>
              {group.items.map((check) => (
                <DoctorCheckRow
                  key={check.id}
                  check={check}
                  repairingUnityBridge={repairingUnityBridge}
                  onRepairUnityBridge={onRepairUnityBridge}
                  formatPayload={formatPayload}
                />
              ))}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

function groupDoctorChecks(
  checks: DoctorCheck[],
  sections?: DoctorReport["sections"],
): Array<{ name: string; summary: { okCount: number; warningCount: number; errorCount: number; unknownCount: number }; items: DoctorCheck[] }> {
  const byId = new Map(checks.map((check) => [check.id, check]));
  if (sections?.length) {
    return sections
      .map((section) => ({
        name: section.name,
        summary: section.summary,
        items: section.checkIds.map((id) => byId.get(id)).filter((item): item is DoctorCheck => Boolean(item)),
      }))
      .filter((section) => section.items.length > 0);
  }
  const grouped = new Map<string, DoctorCheck[]>();
  for (const check of checks) {
    const section = check.section || "Doctor";
    grouped.set(section, [...(grouped.get(section) || []), check]);
  }
  return [...grouped.entries()].map(([name, items]) => ({
    name,
    summary: {
      okCount: items.filter((check) => check.status === "ok").length,
      warningCount: items.filter((check) => check.status === "warning").length,
      errorCount: items.filter((check) => check.status === "error").length,
      unknownCount: items.filter((check) => check.status === "unknown").length,
    },
    items,
  }));
}

function DoctorSummaryTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: "ok" | "warn" | "danger" | "muted";
}) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-background px-3 py-3">
      <div className="truncate text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <Badge tone={tone} className="h-6 px-2">
          {value}
        </Badge>
      </div>
    </div>
  );
}

function DoctorCheckRow({
  check,
  repairingUnityBridge,
  onRepairUnityBridge,
  formatPayload,
}: {
  check: DoctorCheck;
  repairingUnityBridge: boolean;
  onRepairUnityBridge: () => void;
  formatPayload: (value: unknown) => string;
}) {
  const { t } = useTranslation();
  const openByDefault = check.status === "error" || check.status === "warning";
  const [open, setOpen] = useState(openByDefault);
  const tone = doctorTone(check.status);
  const canRepairUnityBridge = check.status !== "ok" && Boolean(check.fixable) && (check.actions || []).includes("repair_unity_bridge");
  return (
    <div className="overflow-hidden rounded-md border border-border/70 bg-background/70">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full min-w-0 items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{check.title}</span>
        <Badge tone={tone} className="shrink-0">
          {doctorStatusLabel(check.status)}
        </Badge>
      </button>
      {open ? (
        <div className="grid gap-3 border-t border-border px-4 py-4">
          <DataLine label={t("doctor.whatFailed")} value={check.whatFailed || (check.status === "ok" ? "-" : check.message)} />
          <DataLine label={t("doctor.why")} value={check.whyItMatters || "-"} />
          <DataLine label={t("doctor.howToFix")} value={check.howToFix || "-"} />
          {check.fixCommand ? <DataLine label={t("doctor.fix")} value={check.fixCommand} /> : null}
          {canRepairUnityBridge ? (
            <div className="flex justify-end">
              <Button type="button" variant="outline" className="h-8 px-3 text-xs" onClick={onRepairUnityBridge} disabled={repairingUnityBridge}>
                {repairingUnityBridge ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wrench className="h-3.5 w-3.5" />}
                {t("doctor.repairBridge")}
              </Button>
            </div>
          ) : null}
          <DataLine label={t("doctor.message")} value={check.message || "-"} />
          {check.detail !== undefined ? <OutputBlock label={t("doctor.detail")} value={formatPayload(check.detail)} /> : null}
        </div>
      ) : null}
    </div>
  );
}

function doctorTone(status: string): "ok" | "warn" | "danger" | "muted" {
  if (status === "ok") {
    return "ok";
  }
  if (status === "warning") {
    return "warn";
  }
  if (status === "error") {
    return "danger";
  }
  return "muted";
}

function doctorStatusLabel(status: string): string {
  switch (status) {
    case "ok":
      return "OK";
    case "warning":
      return "Warning";
    case "error":
      return "Error";
    default:
      return "Unknown";
  }
}
