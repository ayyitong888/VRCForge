import { Archive, FolderOpen, FolderPlus, Loader2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ExternalAgentConnectorStatus } from "../../lib/api";
import { formatCount } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

type CheckpointStoragePanelProps = {
  status: ExternalAgentConnectorStatus | null;
  loading: boolean;
  isDesktop: boolean;
  limitInput: string;
  onLimitInputChange: (value: string) => void;
  onSaveLimit: () => void;
  onOpenFolder: (targetPath: string) => void;
  onPickDirectory: (currentPath: string) => Promise<string>;
  onDeleteSelected: (ids: string[]) => void;
  onRelocate: (directory: string) => void;
};

export function CheckpointStoragePanel({
  status,
  loading,
  isDesktop,
  limitInput,
  onLimitInputChange,
  onSaveLimit,
  onOpenFolder,
  onPickDirectory,
  onDeleteSelected,
  onRelocate,
}: CheckpointStoragePanelProps) {
  const { t } = useTranslation();
  const usage = status?.gateway?.checkpointArchiveUsage;
  const prune = status?.gateway?.checkpointArchivePrune;
  const maxSizeMb = status?.gateway?.checkpointArchiveMaxSizeMb ?? usage?.maxSizeMb ?? 0;
  const usageText = `${formatStorageSize(usage?.sizeBytes)} / ${maxSizeMb > 0 ? `${formatCount(maxSizeMb)} MB` : t("settings.unlimited")}`;
  const directory = usage?.directory || "";

  const archives = useMemo(
    () => (usage?.archives ?? []).filter((item): item is NonNullable<typeof item> & { checkpointId: string } => Boolean(item?.checkpointId)),
    [usage?.archives],
  );
  const selectableIds = useMemo(
    () => archives.filter((item) => !item.protected).map((item) => item.checkpointId),
    [archives],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [relocateInput, setRelocateInput] = useState("");

  useEffect(() => {
    setSelected((prev) => {
      const next = new Set<string>();
      for (const id of prev) {
        if (selectableIds.includes(id)) {
          next.add(id);
        }
      }
      return next.size === prev.size ? prev : next;
    });
  }, [selectableIds]);

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };
  const selectAll = () => setSelected(new Set(selectableIds));
  const invertSelection = () =>
    setSelected((prev) => {
      const next = new Set<string>();
      for (const id of selectableIds) {
        if (!prev.has(id)) {
          next.add(id);
        }
      }
      return next;
    });
  const cleanSelected = () => {
    const ids = selectableIds.filter((id) => selected.has(id));
    if (ids.length) {
      onDeleteSelected(ids);
    }
  };
  const pickDirectory = async () => {
    const selectedPath = await onPickDirectory(relocateInput || directory);
    if (selectedPath) {
      setRelocateInput(selectedPath);
    }
  };
  const disabled = loading || !status;
  const selectedCount = selectableIds.filter((id) => selected.has(id)).length;

  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-composer">
      <div className="flex min-w-0 items-center gap-2">
        <Archive className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{t("settings.storage")}</h2>
        <Badge tone={maxSizeMb > 0 ? "ok" : "muted"} className="shrink-0">
          {maxSizeMb > 0 ? t("settings.limitOn") : t("settings.unlimited")}
        </Badge>
      </div>

      <div className="mt-4 grid gap-3">
        <DataLine label={t("settings.checkpointArchiveUsage")} value={usageText} />
        <DataLine label={t("settings.checkpointArchiveCount")} value={formatCount(usage?.archiveCount)} />
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <div className="min-w-0 flex-1">
            <DataLine label={t("settings.checkpointArchiveDirectory")} value={directory || "-"} />
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={!directory || !isDesktop}
            title={!isDesktop ? t("settings.checkpointArchiveOpenFolderDesktopOnly") : undefined}
            onClick={() => onOpenFolder(directory)}
          >
            <FolderOpen className="h-4 w-4 shrink-0" />
            {t("settings.checkpointArchiveOpenFolder")}
          </Button>
        </div>
      </div>

      <div className="mt-5 flex min-w-0 flex-wrap items-end gap-3">
        <label className="min-w-48 flex-1 text-sm">
          <span className="mb-1 block font-medium">{t("settings.checkpointArchiveLimit")}</span>
          <input
            type="number"
            min={0}
            step={256}
            value={limitInput}
            onChange={(event) => onLimitInputChange(event.target.value)}
            disabled={disabled}
            className="h-10 w-full min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
          />
        </label>
        <Button type="button" disabled={disabled} onClick={onSaveLimit}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t("common.save")}
        </Button>
      </div>
      <div className="mt-2 text-xs text-muted-foreground">{t("settings.checkpointArchiveLimitHint")}</div>
      {prune ? (
        <div className="mt-3 text-xs text-muted-foreground">
          {t("settings.checkpointArchivePruned", {
            count: prune.deletedCount ?? 0,
            size: formatStorageSize(prune.deletedBytes),
          })}
        </div>
      ) : null}

      <div className="mt-6 border-t border-border pt-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold">{t("settings.checkpointArchiveListTitle")}</h3>
          <Button type="button" variant="outline" disabled={disabled || !selectableIds.length} onClick={selectAll}>
            {t("settings.checkpointArchiveSelectAll")}
          </Button>
          <Button type="button" variant="outline" disabled={disabled || !selectableIds.length} onClick={invertSelection}>
            {t("settings.checkpointArchiveInvertSelection")}
          </Button>
          <Button type="button" variant="danger" disabled={disabled || selectedCount === 0} onClick={cleanSelected}>
            <Trash2 className="h-4 w-4 shrink-0" />
            {t("settings.checkpointArchiveCleanSelected")}
          </Button>
        </div>

        {selectedCount > 0 ? (
          <div className="mt-2 text-xs text-muted-foreground">
            {t("settings.checkpointArchiveSelectedCount", { count: selectedCount })}
          </div>
        ) : null}

        {archives.length ? (
          <ul className="mt-3 max-h-72 overflow-auto rounded-lg border border-border">
            {archives.map((item) => {
              const id = item.checkpointId;
              const isProtected = Boolean(item.protected);
              const checked = selected.has(id);
              return (
                <li
                  key={id}
                  className="flex min-w-0 items-center gap-3 border-b border-border/60 px-3 py-2 last:border-b-0"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled || isProtected}
                    onChange={() => toggleOne(id)}
                    className="h-4 w-4 shrink-0 accent-primary disabled:opacity-40"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">{item.label || id}</div>
                    <div className="truncate text-xs text-muted-foreground/80">{item.path || id}</div>
                  </div>
                  <div className="shrink-0 text-xs text-muted-foreground">{formatStorageSize(item.sizeBytes)}</div>
                  {isProtected ? (
                    <Badge tone="warn" className="shrink-0" title={t("settings.checkpointArchiveProtectedHint")}>
                      {t("settings.checkpointArchiveProtected")}
                    </Badge>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="mt-3 rounded-lg border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
            {t("settings.checkpointArchiveNoArchives")}
          </div>
        )}
      </div>

      <div className="mt-6 border-t border-border pt-4">
        <label className="block text-sm">
          <span className="mb-1 block font-medium">{t("settings.checkpointArchiveNewDirectory")}</span>
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <input
              type="text"
              value={relocateInput}
              onChange={(event) => setRelocateInput(event.target.value)}
              disabled={disabled}
              placeholder="D:\\VRCForge\\checkpoint-archives"
              className="h-10 min-w-48 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
            />
            <Button
              type="button"
              variant="outline"
              disabled={disabled || !isDesktop}
              title={!isDesktop ? t("settings.checkpointArchivePickFolderDesktopOnly") : undefined}
              onClick={() => void pickDirectory()}
            >
              <FolderOpen className="h-4 w-4 shrink-0" />
              {t("settings.checkpointArchivePickFolder")}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={disabled || !relocateInput.trim()}
              onClick={() => onRelocate(relocateInput)}
            >
              <FolderPlus className="h-4 w-4 shrink-0" />
              {t("settings.checkpointArchiveChangeDirectory")}
            </Button>
          </div>
        </label>
        <div className="mt-2 text-xs text-muted-foreground">{t("settings.checkpointArchiveNewDirectoryHint")}</div>
      </div>
    </div>
  );
}

function formatStorageSize(bytes?: number) {
  const value = typeof bytes === "number" && Number.isFinite(bytes) ? Math.max(0, bytes) : 0;
  if (value >= 1024 * 1024 * 1024) {
    return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
