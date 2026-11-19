import type { ReactNode } from "react";
import type { TFunction } from "i18next";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SubAgentTask } from "../../lib/api/sub-agents";
import { asRecord } from "../../lib/runtime-parsing";

const INVENTORIES = [
  ["prefabCandidates", "prefabCandidateCount", "prefabs"],
  ["models", "modelCount", "models"],
  ["materials", "materialCount", "materials"],
  ["textures", "textureCount", "textures"],
  ["assemblyDefinitions", "assemblyDefinitionCount", "assemblies"],
] as const;

export function asOutfitInspectionResult(value: unknown) {
  const inspection = asRecord(value);
  return typeof inspection?.ok === "boolean" ? inspection : null;
}

export function subAgentInspectionResult(turn: SubAgentTask) {
  return turn.role === "outfit_package_inspection" ? asOutfitInspectionResult(turn.result?.inspection) : null;
}

function pathRecords(value: unknown): Array<Record<string, unknown> & { path: string }> {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry: unknown) => {
    const record = asRecord(entry);
    return typeof record?.path === "string" && record.path ? [{ ...record, path: record.path }] : [];
  });
}

function paths(value: unknown) {
  return pathRecords(value).map((entry) => entry.path);
}

export function subAgentInspectionConclusion(turn: SubAgentTask, t: TFunction): string {
  const inspection = subAgentInspectionResult(turn);
  if (!inspection) return "";
  if (inspection.ok === false) {
    return t(asRecord(inspection.source)?.type === "missing" ? "subagent.inspection.missing" : "subagent.inspection.failed");
  }
  const summary = asRecord(inspection.summary);
  const packages = summary?.unityPackageCount;
  const prefabs = summary?.prefabCandidateCount;
  const textures = summary?.textureCount;
  const counts = typeof packages === "number" && typeof prefabs === "number" && typeof textures === "number"
    ? t("subagent.inspection.counts", { packages, prefabs, textures })
    : t("subagent.inspection.completed");
  const partial = summary?.truncated === true || summary?.nestedPathnamesTruncated === true;
  return partial ? `${counts}\n\n${t("subagent.inspection.partialScan")}` : counts;
}

function ReportDisclosure({ label, children }: { label: string; children: ReactNode }) {
  return (
    <details className="group/report min-w-0 border-t border-border/60">
      <summary className="flex cursor-pointer list-none items-start gap-2 py-2 pr-2 font-medium focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground group-open/report:rotate-90" aria-hidden="true" />
        <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
      </summary>
      <div className="pb-3 pl-5">{children}</div>
    </details>
  );
}

function InventoryPaths({ entries }: { entries: string[] }) {
  return (
    <ul className="max-h-60 space-y-1 overflow-y-auto pr-2 text-muted-foreground [overflow-wrap:anywhere]">
      {entries.map((path, index) => <li key={`${index}:${path}`}>{path}</li>)}
    </ul>
  );
}

export function OutfitInspectionEvidence({ inspection }: { inspection: Record<string, unknown> }) {
  const { t, i18n } = useTranslation();
  const summary = asRecord(inspection.summary);
  const source = asRecord(inspection.source)?.name;
  const packages = pathRecords(inspection.unityPackages);
  const warnings: string[] = Array.isArray(inspection.warnings) ? inspection.warnings.filter(
    (warning): warning is string => typeof warning === "string" && !!warning,
  ) : [];
  // Some legacy scan warnings interpolate file readers' exceptions.
  const cannotReadPackage = (warning: string) => /^Could not inspect (?:nested )?UnityPackage /.test(warning);
  const reportWarnings = warnings.filter(warning => !cannotReadPackage(warning));
  if (warnings.some(cannotReadPackage)) reportWarnings.push(t("subagent.inspection.unreadablePackages"));
  const numberFormat = new Intl.NumberFormat(i18n.resolvedLanguage);

  return (
    <div className="min-w-0 text-xs leading-5" data-subagent-inspection-evidence>
      {typeof source === "string" && source ? (
        <p className="mb-2 font-medium [overflow-wrap:anywhere]" data-subagent-inspection-source>{source}</p>
      ) : null}
      {packages.length > 0 ? (
        <ReportDisclosure label={`${t("subagent.inspection.packages")} (${numberFormat.format(
          typeof summary?.unityPackageCount === "number" ? summary.unityPackageCount : packages.length,
        )})`}>
          {packages.map((entry, index) => {
            const files = paths(entry.pathnamesEvidence ?? entry.pathnames);
            const incomplete = entry.pathnamesEvidenceComplete === false
              || (entry.pathnamesEvidence == null && entry.pathnamesReturnedTruncated === true);
            return (
              <ReportDisclosure key={`${index}:${entry.path}`} label={entry.path}>
                {incomplete ? <p className="mb-2 text-muted-foreground">{t("subagent.inspection.partialPackage")}</p> : null}
                {files.length > 0 ? <InventoryPaths entries={files} />
                  : <p className="text-muted-foreground">{t("subagent.inspection.noFileList")}</p>}
              </ReportDisclosure>
            );
          })}
          {typeof summary?.unityPackageCount === "number" && summary.unityPackageCount > packages.length ? (
            <p className="text-muted-foreground">{t("subagent.inspection.partialList")}</p>
          ) : null}
        </ReportDisclosure>
      ) : null}
      {INVENTORIES.map(([field, countField, label]) => {
        const entries = paths(inspection[field]);
        if (!entries.length) return null;
        const count = summary?.[countField];
        return (
          <ReportDisclosure key={field} label={`${t(`subagent.inspection.${label}`)}${label === "prefabs" || label === "textures"
            ? ` (${numberFormat.format(typeof count === "number" ? count : entries.length)})`
            : ""}`}>
            <InventoryPaths entries={entries} />
            {typeof count === "number" && count > entries.length ? (
              <p className="mt-2 text-muted-foreground">{t("subagent.inspection.partialList")}</p>
            ) : null}
          </ReportDisclosure>
        );
      })}
      {reportWarnings.length > 0 ? (
        <ReportDisclosure label={t("subagent.inspection.warnings")}>
          <InventoryPaths entries={reportWarnings} />
        </ReportDisclosure>
      ) : null}
    </div>
  );
}
