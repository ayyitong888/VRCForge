import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { useMemo, useState } from "react";
import i18n from "../../i18n";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

const PAGE_SIZE = 10;

export function SkillPackageAuditList({ audit }: { audit: Array<Record<string, unknown>> }) {
  const [query, setQuery] = useState("");
  const [eventFilter, setEventFilter] = useState("");
  const [page, setPage] = useState(0);
  const events = useMemo(
    () => [...new Set(audit.map((item) => String(item.event || "")).filter(Boolean))].sort(),
    [audit],
  );
  const filtered = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return [...audit]
      .reverse()
      .filter((item) => !eventFilter || String(item.event || "") === eventFilter)
      .filter((item) => !normalizedQuery || auditSearchText(item).includes(normalizedQuery));
  }, [audit, eventFilter, query]);
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const rows = filtered.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE);

  return (
    <div className="grid gap-2 border-t border-border pt-3">
      <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(180px,0.45fr)]">
        <label className="relative min-w-0">
          <span className="sr-only">{i18n.t("package.auditView.searchLabel")}</span>
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
          <input
            aria-label={i18n.t("package.auditView.searchLabel")}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(0);
            }}
            placeholder={i18n.t("package.auditView.search")}
            className="h-9 w-full rounded-md border border-border bg-card pl-9 pr-3 text-xs outline-none focus:border-primary"
          />
        </label>
        <label className="min-w-0">
          <span className="sr-only">{i18n.t("package.auditView.eventFilterLabel")}</span>
          <select
            aria-label={i18n.t("package.auditView.eventFilterLabel")}
            value={eventFilter}
            onChange={(event) => {
              setEventFilter(event.target.value);
              setPage(0);
            }}
            className="h-9 w-full min-w-0 rounded-md border border-border bg-card px-3 text-xs outline-none focus:border-primary"
          >
            <option value="">{i18n.t("package.auditView.allEvents")}</option>
            {events.map((event) => (
              <option key={event} value={event}>{event}</option>
            ))}
          </select>
        </label>
      </div>

      {rows.length ? (
        <div className="grid gap-1">
          {rows.map((item, index) => {
            const event = String(item.event || i18n.t("package.audit"));
            const timestamp = String(item.timestamp || "");
            const context = auditContext(item);
            const governance = auditGovernanceFields(item);
            return (
              <div key={`${timestamp}-${event}-${currentPage}-${index}`} className="grid min-w-0 gap-2 rounded-md border border-border/70 bg-card px-3 py-2 text-xs">
                <div className="grid min-w-0 gap-1 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1fr)_auto]">
                  <span className="truncate font-mono" title={event}>{event}</span>
                  <span className="truncate text-muted-foreground" title={context}>{context || "-"}</span>
                  <span className="shrink-0 text-muted-foreground">{formatAuditTime(timestamp)}</span>
                </div>
                {governance.length ? (
                  <div className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-muted-foreground">
                    {governance.map(({ key, label, value }) => (
                      <span key={key} className="min-w-0 break-all" title={`${label}: ${value}`}>
                        <span className="font-medium text-foreground/80">{label}:</span> {value}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-md border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
          {i18n.t("package.auditView.empty")}
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="sr-only" role="status" aria-live="polite">
          {i18n.t("package.auditView.count", { count: filtered.length })}{" "}
          {i18n.t("package.auditView.page", { current: currentPage + 1, total: pageCount })}
        </span>
        <Badge tone="muted">{i18n.t("package.auditView.count", { count: filtered.length })}</Badge>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            className="h-8 px-2"
            disabled={currentPage === 0}
            onClick={() => setPage(Math.max(0, currentPage - 1))}
            aria-label={i18n.t("package.auditView.previous")}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <span className="text-xs text-muted-foreground">
            {i18n.t("package.auditView.page", { current: currentPage + 1, total: pageCount })}
          </span>
          <Button
            type="button"
            variant="ghost"
            className="h-8 px-2"
            disabled={currentPage >= pageCount - 1}
            onClick={() => setPage(Math.min(pageCount - 1, currentPage + 1))}
            aria-label={i18n.t("package.auditView.next")}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}

function auditValue(item: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = item[key];
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value).trim();
    }
  }
  return "";
}

function auditContext(item: Record<string, unknown>): string {
  const skillId = auditValue(item, "skill_id", "skillId");
  const packageId = auditValue(item, "package_id", "packageId");
  const identity = [skillId, packageId && packageId !== skillId ? packageId : ""].filter(Boolean).join(" / ")
    || auditValue(item, "reason");
  const version = auditValue(item, "package_version", "packageVersion", "version");
  const digest = auditValue(item, "package_sha256", "packageSha256");
  const details = [identity, version ? `v${version}` : "", digest ? digest.slice(0, 12) : ""].filter(Boolean);
  return details.join(" | ");
}

function auditGovernanceFields(item: Record<string, unknown>): Array<{ key: string; label: string; value: string }> {
  const fields = [
    { key: "enabled", label: i18n.t("package.auditView.enabled"), value: auditValue(item, "enabled") },
    {
      key: "signatureStatus",
      label: i18n.t("package.auditView.signatureStatus"),
      value: auditValue(item, "signature_status", "signatureStatus"),
    },
    {
      key: "riskLevel",
      label: i18n.t("package.auditView.riskLevel"),
      value: auditValue(item, "risk_level", "riskLevel"),
    },
    {
      key: "signerFingerprint",
      label: i18n.t("package.auditView.signerFingerprint"),
      value: auditValue(item, "signer_fingerprint", "signerFingerprint"),
    },
    { key: "source", label: i18n.t("package.auditView.source"), value: auditValue(item, "source") },
    {
      key: "disabledSkillIds",
      label: i18n.t("package.auditView.disabledSkillIds"),
      value: auditValue(item, "disabled_skill_ids", "disabledSkillIds"),
    },
  ];
  return fields.filter((field) => field.value);
}

function auditSearchText(item: Record<string, unknown>): string {
  return [
    auditValue(item, "event"),
    auditValue(item, "skill_id", "skillId"),
    auditValue(item, "package_id", "packageId"),
    auditValue(item, "package_version", "packageVersion", "version"),
    auditValue(item, "package_sha256", "packageSha256"),
    auditValue(item, "signer_fingerprint", "signerFingerprint"),
    auditValue(item, "enabled"),
    auditValue(item, "signature_status", "signatureStatus"),
    auditValue(item, "risk_level", "riskLevel"),
    auditValue(item, "source"),
    auditValue(item, "disabled_skill_ids", "disabledSkillIds"),
    auditValue(item, "reason"),
  ]
    .join(" ")
    .toLowerCase();
}

function formatAuditTime(timestamp: string): string {
  if (!timestamp) {
    return "";
  }
  const parsed = new Date(timestamp);
  return Number.isNaN(parsed.getTime()) ? timestamp : parsed.toLocaleString();
}
