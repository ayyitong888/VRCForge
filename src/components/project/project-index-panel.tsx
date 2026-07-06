import { Bot, ChevronDown, ChevronRight, Loader2, RefreshCw, Search } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ProjectIndexScanResult } from "../../lib/api";
import { formatCount } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { OutputBlock } from "../ui/output-block";

export function ProjectIndexPanel({
  projectPath,
  projectName,
  result,
  loading,
  error,
  onScan,
  onReview,
}: {
  projectPath: string;
  projectName: string;
  result: ProjectIndexScanResult | null;
  loading: boolean;
  error: string;
  onScan: () => void;
  onReview: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!projectPath) {
    return null;
  }
  const summary = result?.summary || {};
  const firstScan = Boolean(summary.firstScan);
  const changed = Boolean(summary.changed);
  const added = Number(summary.addedFiles || 0);
  const modified = Number(summary.modifiedFiles || 0);
  const deleted = Number(summary.deletedFiles || 0);
  const guidChanges = Number(summary.guidChangeCount || 0);
  const total = Number(summary.totalFiles || 0);
  const scannerFamilies = summary.scannerFamilies || [];
  const statusTone: "ok" | "warn" | "danger" | "muted" = error ? "danger" : loading ? "muted" : changed && !firstScan ? "warn" : "ok";
  const statusLabel = error ? t("skillStatus.failed") : loading ? t("project.statusIndexing") : firstScan ? t("project.statusBaseline") : changed ? t("project.statusChanged") : t("project.statusClean");
  const changeText = firstScan
    ? `${formatCount(total)} indexed`
    : `+${added} ~${modified} -${deleted}${guidChanges ? ` guid ${guidChanges}` : ""}`;
  const addedPaths = result?.changes?.added || [];
  const modifiedPaths = result?.changes?.modified || [];
  const deletedPaths = result?.changes?.deleted || [];
  return (
    <section className="mb-4 overflow-hidden rounded-xl border border-border bg-card shadow-panel">
      <div className="flex min-w-0 items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setOpen((value) => !value)}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          <Search className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate text-xs font-medium">{t("project.changes", { name: projectName || shortPath(projectPath) })}</span>
          <Badge tone={statusTone} className="shrink-0">
            {statusLabel}
          </Badge>
          <span className="shrink-0 font-mono text-xs text-muted-foreground">{changeText}</span>
        </button>
        <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" disabled={loading} onClick={onScan}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
        <Button type="button" variant="ghost" className="h-8 shrink-0 px-2 text-xs" disabled={loading} onClick={onReview} title={t("project.reviewChanges")}>
          <Bot className="h-3.5 w-3.5" />
          {t("outfit.review")}
        </Button>
      </div>
      {open ? (
        <div className="space-y-3 border-t border-border px-3 py-3">
          {error ? <DataLine label={t("doctor.error")} value={error} /> : null}
          <DataLine label={t("subagent.roles.projectIndexReview")} value={projectPath} mono />
          <DataLine label="Files" value={`${formatCount(total)} total · ${formatCount(Number(summary.unchangedFiles || 0))} unchanged`} />
          <DataLine label="Hashing" value={`${formatCount(Number(summary.hashesComputed || 0))} computed · ${formatCount(Number(summary.hashesReused || 0))} reused`} />
          {scannerFamilies.length ? <DataLine label="Affected" value={scannerFamilies.join(", ")} /> : null}
          {addedPaths.length ? <OutputBlock label="Added" value={formatProjectIndexPaths(addedPaths)} /> : null}
          {modifiedPaths.length ? <OutputBlock label="Modified" value={formatProjectIndexPaths(modifiedPaths)} /> : null}
          {deletedPaths.length ? <OutputBlock label="Deleted" value={formatProjectIndexPaths(deletedPaths)} /> : null}
          {result?.staleDataPolicy ? <DataLine label="Policy" value={result.staleDataPolicy} /> : null}
        </div>
      ) : null}
    </section>
  );
}

function formatProjectIndexPaths(entries: Array<{ path: string; category?: string; size?: number }>): string {
  const lines = entries.slice(0, 80).map((entry) => {
    const category = entry.category ? ` [${entry.category}]` : "";
    const size = typeof entry.size === "number" && entry.size > 0 ? ` ${formatCount(entry.size)}b` : "";
    return `${entry.path}${category}${size}`;
  });
  if (entries.length > lines.length) {
    lines.push(`... ${entries.length - lines.length} more`);
  }
  return lines.join("\n");
}

function shortPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || path;
}
