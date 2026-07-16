import { AlertTriangle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "../ui/button";
import {
  createDeveloperChallengeSubmitGuard,
  DEVELOPER_OPTIONS_MINIMUM_WAIT_MS,
  developerChallengeCountdown,
  developerChallengeReady,
} from "./developer-options-challenge";

export function DeveloperOptionsWarningDialog({
  waitMs,
  onCancel,
  onConfirm,
}: {
  waitMs: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLElement | null>(null);
  const actionTakenRef = useRef(false);
  const submitGuardRef = useRef(createDeveloperChallengeSubmitGuard());
  const [deadline] = useState(() => performance.now() + Math.max(DEVELOPER_OPTIONS_MINIMUM_WAIT_MS, waitMs));
  const [now, setNow] = useState(() => performance.now());
  const ready = developerChallengeReady(deadline, now);
  const countdown = developerChallengeCountdown(deadline, now);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.querySelector<HTMLButtonElement>("[data-vrcforge-developer-cancel]")?.focus();
    const timer = window.setInterval(() => {
      const current = performance.now();
      setNow(current);
      if (developerChallengeReady(deadline, current)) {
        window.clearInterval(timer);
      }
    }, 50);
    return () => {
      window.clearInterval(timer);
      previousFocus?.focus();
    };
  }, [deadline]);

  const cancel = useCallback(() => {
    if (actionTakenRef.current) {
      return;
    }
    actionTakenRef.current = true;
    onCancel();
  }, [onCancel]);

  const confirm = useCallback(() => {
    if (actionTakenRef.current) {
      return;
    }
    const current = performance.now();
    setNow(current);
    if (!developerChallengeReady(deadline, current) || !submitGuardRef.current()) {
      return;
    }
    actionTakenRef.current = true;
    onConfirm();
  }, [deadline, onConfirm]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [cancel]);

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/55 p-5"
      role="dialog"
      aria-modal="true"
      aria-labelledby="developer-options-warning-title"
      aria-describedby="developer-options-warning-risk developer-options-warning-explicit"
      data-vrcforge-developer-warning="true"
      onMouseDown={(event) => {
        if (event.currentTarget === event.target) {
          cancel();
        }
      }}
    >
      <section ref={dialogRef} className="w-full max-w-xl rounded-xl border border-destructive/40 bg-card p-5 shadow-panel">
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-destructive">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
            <div>
              <h2 id="developer-options-warning-title" className="text-base font-semibold">
                {t("settings.developerWarningTitle")}
              </h2>
              <p id="developer-options-warning-risk" className="mt-2 text-sm font-medium leading-relaxed">
                {t("settings.developerWarningWorstRisk")}
              </p>
            </div>
          </div>
        </div>

        <p id="developer-options-warning-explicit" className="mt-4 text-sm leading-relaxed text-muted-foreground">
          {t("settings.developerWarningExplicitOnly")}
        </p>
        <div className="mt-4 rounded-lg border border-border bg-background p-3 text-sm">
          <div
            className="font-semibold tabular-nums"
            aria-live="polite"
            data-vrcforge-developer-countdown={countdown}
          >
            {ready
              ? t("settings.developerWarningReady")
              : t("settings.developerWarningCountdown", { seconds: countdown })}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">{t("settings.developerWarningWaitReason")}</div>
        </div>

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button
            type="button"
            variant="secondary"
            data-vrcforge-developer-cancel
            onClick={cancel}
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            variant="danger"
            disabled={!ready}
            data-vrcforge-developer-confirm
            onClick={confirm}
          >
            {t("settings.developerWarningConfirm")}
          </Button>
        </div>
      </section>
    </div>
  );
}
