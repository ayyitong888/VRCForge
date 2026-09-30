import { Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  fetchDataRoot,
  updateDataRoot,
  type DataRootInfo,
  type DataRootUpdateResult,
} from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

/**
 * Pure classification of the POST /api/app/settings/data-root response so the
 * failure branch stays testable without a renderer. `ok: false` always carries
 * at least one user-facing error; the fallback covers an empty error list.
 */
export function dataRootUpdateOutcome(
  result: DataRootUpdateResult,
  fallbackError: string,
): { ok: boolean; errors: string[]; migrated: string[] } {
  if (result.ok === true) {
    return { ok: true, errors: [], migrated: Array.isArray(result.migrated) ? result.migrated : [] };
  }
  const errors = Array.isArray(result.errors) && result.errors.length > 0 ? result.errors : [fallbackError];
  return { ok: false, errors, migrated: [] };
}

type DataRootSettingsPanelProps = {
  endpoint: string;
};

export function DataRootSettingsPanel({ endpoint }: DataRootSettingsPanelProps) {
  const { t } = useTranslation();
  const [dataRoot, setDataRoot] = useState<DataRootInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [draftPath, setDraftPath] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [migratedPaths, setMigratedPaths] = useState<string[]>([]);

  const loadDataRoot = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const info = await fetchDataRoot(endpoint);
      setDataRoot(info);
      setDraftPath(info.path);
      setSaveSuccess(false);
      setSaveErrors([]);
      setMigratedPaths([]);
    } catch (cause) {
      setLoadError(cause instanceof Error && cause.message ? cause.message : t("settings.dataRootLoadFailed"));
    } finally {
      setLoading(false);
    }
  }, [endpoint, t]);

  useEffect(() => {
    void loadDataRoot();
  }, [loadDataRoot]);

  async function saveDataRoot(event: FormEvent) {
    event.preventDefault();
    const nextPath = draftPath.trim();
    if (!nextPath || saving) {
      return;
    }
    setSaving(true);
    setSaveSuccess(false);
    setSaveErrors([]);
    setMigratedPaths([]);
    try {
      const result = await updateDataRoot(endpoint, nextPath);
      const outcome = dataRootUpdateOutcome(result, t("settings.dataRootSaveFailed"));
      if (!outcome.ok) {
        setSaveErrors(outcome.errors);
        return;
      }
      setSaveSuccess(true);
      setMigratedPaths(outcome.migrated);
      setDataRoot({ path: nextPath, writable: true, inProgramFiles: false, migrated: false });
      setDraftPath(nextPath);
    } catch (cause) {
      setSaveErrors([cause instanceof Error && cause.message ? cause.message : t("settings.dataRootSaveFailed")]);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div data-vrcforge-data-root className="rounded-xl border border-border bg-card p-4">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{t("settings.dataRootTitle")}</h3>
        {dataRoot ? (
          <Badge tone={dataRoot.writable ? "ok" : "warn"} className="shrink-0">
            {dataRoot.writable ? t("settings.dataRootWritable") : t("settings.dataRootNotWritable")}
          </Badge>
        ) : null}
        {loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">{t("settings.dataRootDesc")}</p>
      {loadError ? <p className="mt-2 text-xs text-destructive">{loadError}</p> : null}
      {dataRoot ? (
        <p data-vrcforge-data-root-path className="mt-2 truncate text-xs text-muted-foreground/70">
          {dataRoot.path}
        </p>
      ) : null}
      {dataRoot?.inProgramFiles ? (
        <p data-vrcforge-data-root-program-files className="mt-2 text-xs text-destructive">
          {t("settings.dataRootProgramFilesWarning")}
        </p>
      ) : null}
      <form onSubmit={saveDataRoot} className="mt-3">
        <input
          data-vrcforge-data-root-input
          value={draftPath}
          onChange={(event) => setDraftPath(event.target.value)}
          disabled={saving || loading}
          placeholder={t("settings.dataRootPlaceholder")}
          className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
        />
        <div className="mt-3 flex items-center justify-end gap-2">
          <Button type="button" variant="outline" disabled={saving || loading} onClick={() => void loadDataRoot()}>
            <RefreshCw className="h-4 w-4" />
            {t("settings.dataRootReload")}
          </Button>
          <Button data-vrcforge-data-root-save type="submit" disabled={saving || loading || !draftPath.trim()}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("common.save")}
          </Button>
        </div>
      </form>
      {saveErrors.length ? (
        <ul data-vrcforge-data-root-errors className="mt-3 space-y-1 text-xs text-destructive">
          {saveErrors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      ) : null}
      {saveSuccess ? (
        <div data-vrcforge-data-root-success className="mt-3 text-xs">
          <p className="font-medium text-foreground">{t("settings.dataRootSaveSuccess")}</p>
          {migratedPaths.length ? (
            <p data-vrcforge-data-root-migrated className="mt-1 text-muted-foreground">
              {t("settings.dataRootMigratedCount", { count: migratedPaths.length })}
            </p>
          ) : null}
          <p className="mt-1 font-medium text-foreground">{t("settings.dataRootRestartRequired")}</p>
        </div>
      ) : null}
    </div>
  );
}
