import { Archive, FolderOpen, FolderPlus, Loader2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ExternalAgentConnectorStatus } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

type ArchiveUsage = NonNullable<ExternalAgentConnectorStatus["gateway"]["checkpointArchiveUsage"]>;
type ArchiveEntry = NonNullable<ArchiveUsage["archives"]>[number];
type ArchiveEntryWithCheckpointId = ArchiveEntry & { checkpointId: string };

function isActiveRecoveryProtection(entry: ArchiveEntry) {
  return entry.protectionReason === "active_recovery" || (entry.protected === true && entry.protectionReason !== "recent");
}

function isAutoCleanupProtected(entry: ArchiveEntry) {
  return entry.autoCleanupProtected === true;
}

function getProtectionReason(entry: ArchiveEntry): "recent" | "active_recovery" {
  return entry.protectionReason === "active_recovery" ? "active_recovery" : "recent";
}

type CheckpointStoragePanelProps = {
  status: ExternalAgentConnectorStatus | null;
  loading: boolean;
  isDesktop: boolean;
  limitInput: string;
  countInput: string;
  onLimitInputChange: (value: string) => void;
  onCountInputChange: (value: string) => void;
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
  countInput,
  onLimitInputChange,
  onCountInputChange,
  onSaveLimit,
  onOpenFolder,
  onPickDirectory,
  onDeleteSelected,
  onRelocate,
}: CheckpointStoragePanelProps) {
  const { t, i18n } = useTranslation();
  const usage = status?.gateway?.checkpointArchiveUsage;
  const prune = status?.gateway?.checkpointArchivePrune;
  const maxSizeMb = status?.gateway?.checkpointArchiveMaxSizeMb ?? usage?.maxSizeMb;
  const maxCount = status?.gateway?.checkpointArchiveMaxCount ?? usage?.maxCount;
  const maxBytes = maxSizeMb === undefined ? undefined : maxSizeMb * BYTES_PER_MB;
  const archiveCount = usage?.archiveCount;
  const sizeBytes = usage?.sizeBytes;
  const sizeUnit = (sizeBytes ?? 0) >= BYTES_PER_GB ? BYTES_PER_GB : BYTES_PER_MB;
  const formatCount = new Intl.NumberFormat(i18n.language);
  const capacityState = archiveLimitState(sizeBytes, maxBytes);
  const countState = archiveLimitState(archiveCount, maxCount);
  const limitLabel = {
    at: t("settings.checkpointArchiveAtLimit"),
    over: t("settings.checkpointArchiveOverLimit"),
    within: t("settings.checkpointArchiveWithinLimit"),
    unknown: t("common.unavailable"),
    unlimited: t("settings.unlimited"),
  };
  const capacityText = sizeBytes === undefined ? "" : formatStorageSize(sizeBytes, sizeUnit, i18n.language);
  const capacityLimitText = maxBytes ? formatStorageSize(maxBytes, sizeUnit, i18n.language) : "";
  const countText = archiveCount === undefined ? "" : formatCount.format(archiveCount);
  const countLimitText = maxCount ? formatCount.format(maxCount) : "";
  const directory = usage?.directory || "";

  const archives = useMemo(
    () => (usage?.archives ?? []).filter(
      (item): item is ArchiveEntryWithCheckpointId => Boolean(item?.checkpointId),
    ),
    [usage?.archives],
  );
  const selectableIds = useMemo(
    () => archives.filter((item) => !isActiveRecoveryProtection(item)).map((item) => item.checkpointId),
    [archives],
  );

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pendingDeleteIds, setPendingDeleteIds] = useState<string[]>([]);
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

  useEffect(() => {
    setPendingDeleteIds((prev) => {
      if (!prev.length) {
        return prev;
      }
      const next = prev.filter((id) => selected.has(id) && selectableIds.includes(id));
      return next.length === prev.length ? prev : [];
    });
  }, [selectableIds, selected]);

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
    if (ids.length === 0 || disabled) {
      return;
    }
    const sameIds = ids.length === pendingDeleteIds.length && ids.every((id) => pendingDeleteIds.includes(id));
    if (!sameIds) {
      setPendingDeleteIds(ids);
      return;
    }
    setPendingDeleteIds([]);
    onDeleteSelected(ids);
  };
  const pickDirectory = async () => {
    const selectedPath = await onPickDirectory(relocateInput || directory);
    if (selectedPath) {
      setRelocateInput(selectedPath);
    }
  };
  const disabled = loading || !status;
  const selectedCount = selectableIds.filter((id) => selected.has(id)).length;
  const hasPendingDelete = pendingDeleteIds.length > 0;
  const pendingDeleteCount = hasPendingDelete ? pendingDeleteIds.length : selectedCount;

  return (
    <div className="rounded-xl border border-border bg-card p-5">
      <div className="flex min-w-0 items-center gap-2">
        <Archive className="h-4 w-4 shrink-0 text-primary" />
        <h2 className="min-w-0 flex-1 truncate text-base font-semibold">{t("settings.storage")}</h2>
      </div>

      <dl className="mt-4 grid gap-3 sm:grid-cols-2">
        <ArchiveQuota label={t("settings.checkpointArchiveCapacity")} value={capacityText}
          limit={capacityLimitText} state={capacityState} stateLabel={limitLabel[capacityState]} kind="capacity" />
        <ArchiveQuota label={t("settings.checkpointArchiveCount")} value={countText}
          limit={countLimitText} state={countState} stateLabel={limitLabel[countState]} kind="count" />
      </dl>
      <div className="mt-4 grid gap-3">
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
        <label className="min-w-48 flex-1 text-sm">
          <span className="mb-1 block font-medium">{t("settings.checkpointArchiveCountLimit")}</span>
          <input
            type="number"
            min={0}
            step={1}
            value={countInput}
            onChange={(event) => onCountInputChange(event.target.value)}
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
      <div className="mt-1 text-xs text-muted-foreground">{t("settings.checkpointArchiveCountLimitHint")}</div>
      {usage?.overCountLimit ? <p className="mt-2 text-xs text-muted-foreground">{t("settings.checkpointArchiveCountProtected")}</p> : null}
      {prune ? (
        <div className="mt-3 text-xs text-muted-foreground">
          {t("settings.checkpointArchivePruned", {
            count: prune.deletedCount ?? 0,
            size: formatStorageSize(prune.deletedBytes, undefined, i18n.language),
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
            {hasPendingDelete
              ? t("settings.checkpointArchiveDeleteConfirm", { count: pendingDeleteCount })
              : t("settings.checkpointArchiveCleanSelected")}
          </Button>
          {hasPendingDelete ? (
            <Button type="button" variant="outline" disabled={disabled} onClick={() => setPendingDeleteIds([])}>
              {t("common.cancel")}
            </Button>
          ) : null}
        </div>

        {archives.length ? (
          <div className="mt-2 text-xs text-muted-foreground">
            {t("settings.checkpointArchiveSelection", { count: selectedCount, eligible: selectableIds.length })}
          </div>
        ) : null}
        {hasPendingDelete ? (
          <div className="mt-2 text-xs text-destructive">{t("settings.checkpointArchiveDeletePendingWarning")}</div>
        ) : null}

        {archives.length ? (
          <ul className="mt-3 max-h-72 overflow-auto rounded-lg border border-border">
            {archives.map((item) => {
              const id = item.checkpointId;
              const isProtected = isActiveRecoveryProtection(item);
              const isAutoProtected = isAutoCleanupProtected(item);
              const protectionReason = getProtectionReason(item);
              const protectionLabel = protectionReason === "active_recovery"
                ? t("settings.checkpointArchiveRecoveryProtected")
                : t("settings.checkpointArchiveRecentProtected");
              const protectionHint = protectionReason === "active_recovery"
                ? t("settings.checkpointArchiveRecoveryProtectedHint")
                : t("settings.checkpointArchiveRecentProtectedHint");
              const checked = selected.has(id);
              const { action, timestamp } = getArchiveDisplay(item);
              const title = action || t("settings.checkpointArchiveFile");
              const localTime = timestamp?.toLocaleString(i18n.language, { dateStyle: "medium", timeStyle: "short" });
              return (
                <li
                  key={id}
                  className="flex min-w-0 flex-wrap items-start gap-3 border-b border-border/60 px-3 py-2 last:border-b-0"
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    aria-label={localTime ? title + ", " + localTime : title}
                    disabled={disabled || isProtected}
                    onChange={() => toggleOne(id)}
                    className="h-4 w-4 shrink-0 accent-primary disabled:opacity-40"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="break-words text-sm font-medium">{title}</div>
                    {localTime ? <time dateTime={timestamp?.toISOString()} className="text-xs text-muted-foreground">{localTime}</time> : null}
                    <details className="text-xs text-muted-foreground">
                      <summary className="w-fit cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                        {t("settings.checkpointArchiveFile")}
                      </summary>
                      <div className="mt-1 break-all font-mono">{item.path || id}</div>
                    </details>
                  </div>
                  <div className="shrink-0 text-xs text-muted-foreground">{item.sizeBytes === undefined ? t("common.unavailable") : formatStorageSize(item.sizeBytes, undefined, i18n.language)}</div>
                  {(isProtected || isAutoProtected) ? (
                    <Badge tone="warn" className="shrink-0" title={protectionHint}>
                      {protectionLabel}
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

const BYTES_PER_MB = 1024 * 1024;
const BYTES_PER_GB = 1024 * BYTES_PER_MB;

function archiveLimitState(used: number | undefined, limit: number | undefined) {
  if (limit === 0) return "unlimited";
  if (used === undefined || limit === undefined) return "unknown";
  if (used > limit) return "over";
  if (used === limit) return "at";
  return "within";
}

function ArchiveQuota({ label, value, limit, state, stateLabel, kind }: {
  label: string;
  value: string;
  limit: string;
  state: ReturnType<typeof archiveLimitState>;
  stateLabel: string;
  kind: "capacity" | "count";
}) {
  return (
    <div className="min-w-0 rounded-lg border border-border px-3 py-2" data-checkpoint-archive-quota={kind} data-limit-state={state}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {value ? <span className="text-sm font-semibold tabular-nums">{value}{limit ? " / " + limit : ""}</span> : null}
        <span className={state === "at" || state === "over" ? "text-xs font-medium text-amber-800 dark:text-amber-300" : "text-xs text-muted-foreground"}>{stateLabel}</span>
      </dd>
    </div>
  );
}

function getArchiveDisplay(item: ArchiveEntry) {
  const label = item.label?.trim() || "";
  const separator = label.lastIndexOf(" · ");
  const dateLabel = separator < 0 ? label : label.slice(separator + 3);
  const createdAt = /^\d{4}-\d{2}-\d{2}T/.test(dateLabel) ? Date.parse(dateLabel) : NaN;
  const hasCreatedAt = Number.isFinite(createdAt);
  const date = new Date(hasCreatedAt ? createdAt : (item.modifiedAt ?? NaN) * 1000);
  const rawAction = hasCreatedAt ? (separator < 0 ? "" : label.slice(0, separator)) : label;
  const action = /^(vrcforge|unity)_[a-z0-9_]+$/.test(rawAction)
    ? rawAction.replace(/^[^_]+_/, "").replace(/_/g, " ").replace(/^./, (first) => first.toUpperCase())
    : rawAction;
  return { action, timestamp: Number.isFinite(date.getTime()) ? date : undefined };
}

function formatStorageSize(bytes?: number, unit?: number, locale?: string) {
  const value = typeof bytes === "number" && Number.isFinite(bytes) ? Math.max(0, bytes) : 0;
  const divisor = unit ?? (value >= BYTES_PER_GB ? BYTES_PER_GB : BYTES_PER_MB);
  const digits = divisor === BYTES_PER_GB ? 2 : 1;
  const amount = value / divisor;
  const minimum = 10 ** -digits;
  const format = new Intl.NumberFormat(locale, { maximumFractionDigits: digits });
  const label = divisor === BYTES_PER_GB ? "GB" : "MB";
  return amount > 0 && amount < minimum
    ? "<" + format.format(minimum) + " " + label
    : format.format(amount) + " " + label;
}
