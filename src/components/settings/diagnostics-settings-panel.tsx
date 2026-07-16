import { Download, FolderOpen, Loader2, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { DiagnosticIdentitySummary, DiagnosticLogLevel, DiagnosticsStatus } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { availableDiagnosticLogLevels, normalizeDiagnosticLogLevel } from "./diagnostic-log-levels";

function logLevelLabelKey(level: DiagnosticLogLevel) {
  return `settings.logLevel${level[0].toUpperCase()}${level.slice(1)}`;
}

function logLevelDescriptionKey(level: DiagnosticLogLevel) {
  return `${logLevelLabelKey(level)}Desc`;
}

function safeBasename(value?: string) {
  return value?.split(/[\\/]/).filter(Boolean).at(-1) || "";
}

function identityDetails(identity: DiagnosticIdentitySummary, t: (key: string, options?: Record<string, unknown>) => string) {
  const parts: string[] = [];
  if (identity.windowsUser || identity.userAlias) {
    parts.push(
      t("settings.logIdentityWindowsUser", {
        value: [identity.windowsUser, identity.userAlias].filter(Boolean).join(" · "),
      }),
    );
  }
  if (identity.projectName || identity.projectAlias) {
    parts.push(
      t("settings.logIdentityUnityProject", {
        value: [identity.projectName, identity.projectAlias].filter(Boolean).join(" · "),
      }),
    );
  }
  if (identity.avatarName) {
    parts.push(t("settings.logIdentityAvatar", { value: identity.avatarName }));
  }
  return parts;
}

export function DiagnosticsSettingsPanel({
  developerOptionsEnabled,
  status,
  message,
  loading,
  exportingSupportBundle,
  onLogLevelChange,
  onOpenLogsFolder,
  onCreateSupportBundle,
}: {
  developerOptionsEnabled: boolean;
  status: DiagnosticsStatus | null;
  message: string;
  loading: boolean;
  exportingSupportBundle: boolean;
  onLogLevelChange: (level: DiagnosticLogLevel) => void;
  onOpenLogsFolder: () => void;
  onCreateSupportBundle: () => void;
}) {
  const { t } = useTranslation();
  const availableLevels = availableDiagnosticLogLevels(developerOptionsEnabled);
  const statusLevel = normalizeDiagnosticLogLevel(
    status?.logLevel || (status?.debugLogging ? "debug" : "info"),
    developerOptionsEnabled,
  );
  const [selectedLevel, setSelectedLevel] = useState<DiagnosticLogLevel>(statusLevel);

  useEffect(() => {
    setSelectedLevel(statusLevel);
  }, [statusLevel]);

  const effectiveSelectedLevel = normalizeDiagnosticLogLevel(selectedLevel, developerOptionsEnabled);
  const selectedIndex = Math.max(0, availableLevels.indexOf(effectiveSelectedLevel));
  const selectedDescription = t(logLevelDescriptionKey(effectiveSelectedLevel));
  const activeLogFile = safeBasename(status?.activeLogFile);
  const redactionEnabled = Boolean(status?.redaction?.enabled ?? status?.redaction?.beforeWrite);
  const identities = useMemo(
    () => (status?.identities || []).filter((identity) => Boolean(identity?.alias)),
    [status?.identities],
  );

  const handleLevelChange = (nextIndex: number) => {
    const level = availableLevels[nextIndex];
    if (!level || level === effectiveSelectedLevel) {
      return;
    }
    setSelectedLevel(level);
    onLogLevelChange(level);
  };

  return (
    <div data-vrcforge-diagnostics-settings>
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h2 className="truncate text-base font-semibold">{t("settings.diagnostics")}</h2>
        {loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label={t("common.loading")} /> : null}
        {message ? (
          <Badge tone="ok" className="min-w-0 max-w-full truncate">
            {message}
          </Badge>
        ) : null}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{t("settings.loggingDesc")}</p>

      <div className="mt-4 rounded-xl border border-border bg-card p-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium">{t("settings.logLevel")}</div>
            <div className="mt-1 text-xs text-muted-foreground">
              {t("settings.logLevelImmediate", { count: availableLevels.length })}
            </div>
          </div>
          <Badge tone={effectiveSelectedLevel === "trace" || effectiveSelectedLevel === "debug" ? "warn" : "muted"}>
            {t(logLevelLabelKey(effectiveSelectedLevel))}
          </Badge>
        </div>

        <input
          type="range"
          min={0}
          max={availableLevels.length - 1}
          step={1}
          value={selectedIndex}
          aria-label={t("settings.logLevelSlider")}
          aria-valuetext={t("settings.logLevelAriaValue", {
            level: t(logLevelLabelKey(effectiveSelectedLevel)),
            description: selectedDescription,
          })}
          data-vrcforge-log-level={effectiveSelectedLevel}
          onChange={(event) => handleLevelChange(Number(event.currentTarget.value))}
          className="mt-5 w-full cursor-pointer accent-primary"
        />
        <div
          className="mt-1 grid gap-1 text-center text-[11px] text-muted-foreground"
          style={{ gridTemplateColumns: `repeat(${availableLevels.length}, minmax(0, 1fr))` }}
          aria-hidden="true"
        >
          {availableLevels.map((level) => (
            <span key={level}>{t(logLevelLabelKey(level))}</span>
          ))}
        </div>

        <div
          className="mt-4 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2"
          data-vrcforge-log-level-description={effectiveSelectedLevel}
        >
          <div className="text-xs font-semibold text-foreground">
            {t(logLevelLabelKey(effectiveSelectedLevel))}
          </div>
          <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
            {selectedDescription}
          </div>
        </div>
        {!developerOptionsEnabled ? (
          <p className="mt-3 text-xs text-muted-foreground" data-vrcforge-log-trace-locked>
            {t("settings.logLevelTraceLockedHint")}
          </p>
        ) : null}

        <div className="mt-5 grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-border bg-background p-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <ShieldCheck className="h-4 w-4 text-emerald-600 dark:text-emerald-300" />
              {t("settings.logRedaction")}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {status
                ? redactionEnabled
                  ? t("settings.logRedactionEnabled")
                  : t("settings.logRedactionUnavailable")
                : t("common.loading")}
            </p>
          </div>
          <div className="rounded-lg border border-border bg-background p-3">
            <div className="text-sm font-medium">{t("settings.logRetention")}</div>
            <p className="mt-1 text-xs text-muted-foreground">
              {t("settings.logRetentionSummary", {
                days: status?.retentionDays ?? 5,
                files: status?.maxFiles ?? 40,
                total: Math.round((status?.maxTotalBytes ?? 52_428_800) / 1_048_576),
                file: Math.round((status?.maxFileBytes ?? 8_388_608) / 1_048_576),
              })}
            </p>
          </div>
        </div>

        <div className="mt-4 flex min-w-0 flex-wrap items-center gap-2">
          <Button type="button" variant="outline" data-vrcforge-open-logs onClick={onOpenLogsFolder}>
            <FolderOpen className="h-4 w-4" />
            {t("settings.openLogsFolder")}
          </Button>
          <Button
            type="button"
            variant="outline"
            data-vrcforge-export-support
            disabled={exportingSupportBundle}
            onClick={onCreateSupportBundle}
          >
            {exportingSupportBundle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
            {t("settings.exportBundle")}
          </Button>
          <span className="min-w-0 truncate text-xs text-muted-foreground">
            {activeLogFile
              ? t("settings.activeLogFile", { file: activeLogFile })
              : t("settings.activeLogFilePending")}
          </span>
        </div>
      </div>

      <div className="mt-4 rounded-xl border border-border bg-card p-4" data-vrcforge-log-identities>
        <div className="text-sm font-medium">{t("settings.logIdentityMapping")}</div>
        <p className="mt-1 text-xs text-muted-foreground">{t("settings.logIdentityMappingDesc")}</p>
        {identities.length ? (
          <div className="mt-3 space-y-2">
            {identities.map((identity) => {
              const details = identityDetails(identity, t);
              return (
                <div key={`${identity.kind}:${identity.alias}`} className="rounded-lg border border-border bg-background px-3 py-2 text-xs">
                  <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
                    <code className="shrink-0 font-semibold text-foreground">{identity.alias}</code>
                    <span aria-hidden="true" className="text-muted-foreground">→</span>
                    <span className="min-w-0 break-words text-muted-foreground">
                      {details.length ? details.join(" · ") : t("settings.logIdentitySafeLabelPending")}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="mt-3 rounded-lg border border-dashed border-border bg-background px-3 py-2 text-xs text-muted-foreground">
            {t("settings.logIdentityEmpty")}
          </div>
        )}
      </div>
    </div>
  );
}
