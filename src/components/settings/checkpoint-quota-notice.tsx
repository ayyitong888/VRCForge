import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { fetchCheckpointArchiveUsage } from "../../lib/api/connectors";
import { Button } from "../ui/button";
import { checkpointQuotaWarning, shouldShowCheckpointQuotaWarning } from "./checkpoint-quota-warning";
import type { CheckpointQuotaWarning } from "./checkpoint-quota-warning";

type Props = {
  endpoint: string;
  connected: boolean;
  refreshTrigger: unknown;
  onManage: () => void;
};

export function CheckpointQuotaNotice({ endpoint, connected, refreshTrigger, onManage }: Props) {
  const { t } = useTranslation();
  const [notice, setNotice] = useState<CheckpointQuotaWarning | null>(null);
  const acknowledged = useRef<CheckpointQuotaWarning | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    acknowledged.current = null;
    setNotice(null);
  }, [endpoint, connected]);

  useEffect(() => {
    if (!connected || !endpoint) return;
    // App-owned, read-only quota checks use the existing authenticated IPC/API.
    // One request at a time, no connector scan; stop/abort on disconnect/unmount.
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    async function check() {
      try {
        const usage = await fetchCheckpointArchiveUsage(endpoint, abort.signal);
        if (stopped || usage.ok !== true) return;
        const next = checkpointQuotaWarning(usage);
        if (!next) {
          acknowledged.current = null;
          setNotice(null);
        } else if (shouldShowCheckpointQuotaWarning(next, acknowledged.current)) {
          setNotice(next);
        }
      } catch {
        // Missing/temporarily unavailable measurements are not zero usage.
      } finally {
        if (!stopped) timer = setTimeout(() => void check(), 60_000);
      }
    }
    void check();
    return () => {
      stopped = true;
      abort.abort();
      clearTimeout(timer);
    };
  }, [endpoint, connected, refreshTrigger]);

  useEffect(() => {
    if (notice && !dialog.current?.open) dialog.current?.showModal();
    if (!notice && dialog.current?.open) dialog.current.close();
  }, [notice]);

  function dismiss() {
    acknowledged.current = notice;
    setNotice(null);
  }

  const size = (bytes: number) => `${(bytes / 1024 / 1024).toLocaleString(undefined, { maximumFractionDigits: 2 })} MB`;
  return (
    <dialog ref={dialog} aria-labelledby="checkpoint-quota-title" aria-describedby="checkpoint-quota-description"
      onCancel={(event) => { event.preventDefault(); dismiss(); }}
      className="m-auto w-[min(32rem,90vw)] rounded-xl border border-border bg-card p-6 text-foreground shadow-xl backdrop:bg-black/40">
      {notice ? <>
        <h2 id="checkpoint-quota-title" className="text-lg font-semibold">{t("settings.checkpointQuotaTitle")}</h2>
        <p id="checkpoint-quota-description" className="mt-3 text-sm text-muted-foreground">
          {t("settings.checkpointQuotaDescription", { used: size(notice.sizeBytes), limit: `${notice.maxSizeMb} MB`, excess: size(notice.excessBytes) })}
        </p>
        <p className="mt-3 text-sm text-muted-foreground">{t("settings.checkpointQuotaRetentionHint")}</p>
        <div className="mt-5 flex justify-end gap-2">
          <Button autoFocus variant="outline" onClick={dismiss}>{t("settings.checkpointQuotaLater")}</Button>
          <Button onClick={() => { dismiss(); onManage(); }}>{t("settings.checkpointQuotaManage")}</Button>
        </div>
      </> : null}
    </dialog>
  );
}
