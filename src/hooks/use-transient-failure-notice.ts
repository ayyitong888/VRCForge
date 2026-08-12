import { useCallback, useEffect, useRef, useState } from "react";

export const TRANSIENT_FAILURE_NOTICE_MS = 3_000;

export type TransientFailureKind = "vision" | "upload" | "send" | "copy";
export type TransientNoticeTone = "success" | "error";

export type TransientFailureNotice = {
  id: number;
  kind: TransientFailureKind;
  message: string;
  tone: TransientNoticeTone;
};

export function useTransientFailureNotice() {
  const [notice, setNotice] = useState<TransientFailureNotice | null>(null);
  const nextIdRef = useRef(0);

  const dismissTransientFailure = useCallback(() => setNotice(null), []);
  const showTransientNotice = useCallback((tone: TransientNoticeTone, kind: TransientFailureKind, message: string) => {
    const boundedMessage = String(message || "").trim().slice(0, 500);
    if (!boundedMessage) return;
    nextIdRef.current += 1;
    setNotice({ id: nextIdRef.current, kind, message: boundedMessage, tone });
  }, []);
  const showTransientFailure = useCallback(
    (kind: TransientFailureKind, message: string) => showTransientNotice("error", kind, message),
    [showTransientNotice],
  );

  const showTransientSuccess = useCallback(
    (kind: TransientFailureKind, message: string) => showTransientNotice("success", kind, message),
    [showTransientNotice],
  );

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(dismissTransientFailure, TRANSIENT_FAILURE_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [dismissTransientFailure, notice]);

  return {
    notice,
    showTransientFailure,
    showTransientSuccess,
    showTransientNotice,
    dismissTransientFailure,
  };
}
