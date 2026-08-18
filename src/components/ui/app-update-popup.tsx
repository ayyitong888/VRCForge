import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { openAppReleaseUrl, type AppUpdatePromptState } from "../../lib/api/app-update";
import { Button } from "./button";

export function AppUpdatePopup({
  prompt,
  automaticCheckEnabled,
  onAutomaticCheckEnabledChange,
  onDismiss,
}: {
  prompt: AppUpdatePromptState | null;
  automaticCheckEnabled: boolean;
  onAutomaticCheckEnabledChange: (enabled: boolean) => void;
  onDismiss: () => void;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLElement | null>(null);
  const [openingRelease, setOpeningRelease] = useState(false);
  const [openReleaseError, setOpenReleaseError] = useState("");
  const dismiss = useCallback(() => onDismiss(), [onDismiss]);

  useEffect(() => {
    if (!prompt) return;
    setOpeningRelease(false);
    setOpenReleaseError("");
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
  }, [prompt, dismiss]);

  if (!prompt) return null;

  const result = prompt.result;
  const updateAvailable = result?.status === "update_available";
  const upToDate = result?.status === "up_to_date";
  const titleKey = updateAvailable
    ? "appUpdate.dialogTitle"
    : upToDate
      ? "appUpdate.upToDateTitle"
      : "appUpdate.failedTitle";
  const bodyKey = updateAvailable
    ? "appUpdate.dialogBody"
    : upToDate
      ? "appUpdate.upToDateBody"
      : "appUpdate.failedBody";

  const openRelease = async () => {
    if (!result?.releaseUrl || openingRelease) return;
    setOpeningRelease(true);
    setOpenReleaseError("");
    try {
      await openAppReleaseUrl(result.releaseUrl);
    } catch {
      setOpenReleaseError(t("appUpdate.openReleaseFailed"));
    } finally {
      setOpeningRelease(false);
    }
  };

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
          {t(titleKey)}
        </h2>
        <p id="app-update-popup-body" className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {t(bodyKey, { version: result?.latestVersion || result?.currentVersion || "" })}
        </p>
        {prompt.source === "startup" ? (
          <label className="mt-4 flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
            <input
              type="checkbox"
              checked={!automaticCheckEnabled}
              data-vrcforge-disable-automatic-update-check
              onChange={(event) => onAutomaticCheckEnabledChange(!event.currentTarget.checked)}
              className="h-4 w-4 accent-primary"
            />
            {t("appUpdate.disableAutomaticCheck")}
          </label>
        ) : null}
        {openReleaseError ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            {openReleaseError}
          </p>
        ) : null}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="secondary" data-vrcforge-app-update-dismiss onClick={dismiss}>
            {t("common.dismiss")}
          </Button>
          {updateAvailable ? (
            <Button
              type="button"
              data-vrcforge-app-update-open
              disabled={openingRelease}
              onClick={() => void openRelease()}
            >
              {t("appUpdate.openRelease")}
            </Button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
