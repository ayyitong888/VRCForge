import { useCallback, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { AppUpdateResult } from "../../lib/api/app-update";
import { Button } from "./button";

export function AppUpdatePopup({
  result,
  onDismiss,
}: {
  result: AppUpdateResult | null;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLElement | null>(null);
  const dismiss = useCallback(() => onDismiss(), [onDismiss]);

  useEffect(() => {
    if (!result) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.querySelector<HTMLButtonElement>("[data-vrcforge-app-update-dismiss]")?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dismiss();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [result, dismiss]);

  if (!result) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-5"
      role="dialog"
      aria-modal="true"
      aria-labelledby="app-update-popup-title"
      aria-describedby="app-update-popup-body"
      data-vrcforge-app-update-popup="true"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) {
          dismiss();
        }
      }}
    >
      <section ref={dialogRef} className="w-full max-w-md rounded-xl border border-border bg-card p-5 shadow-panel">
        <h2 id="app-update-popup-title" className="text-base font-semibold">
          {t("appUpdate.dialogTitle")}
        </h2>
        <p id="app-update-popup-body" className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {t("appUpdate.dialogBody", { version: result.latestVersion })}
        </p>
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="secondary" data-vrcforge-app-update-dismiss onClick={dismiss}>
            {t("common.dismiss")}
          </Button>
          <a
            href={result.releaseUrl}
            target="_blank"
            rel="noreferrer"
            data-vrcforge-app-update-open
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            {t("appUpdate.openRelease")}
          </a>
        </div>
      </section>
    </div>
  );
}
