import { useCallback, useEffect, useRef, useState } from "react";

export const TRANSIENT_FAILURE_NOTICE_MS = 3_000;

export type TransientFailureKind = "vision" | "upload" | "send";

export type TransientFailureNotice = {
  id: number;
  kind: TransientFailureKind;
  message: string;
};

export function useTransientFailureNotice() {
  const [notice, setNotice] = useState<TransientFailureNotice | null>(null);
  const nextIdRef = useRef(0);

  const dismissTransientFailure = useCallback(() => setNotice(null), []);
  const showTransientFailure = useCallback((kind: TransientFailureKind, message: string) => {
    const boundedMessage = String(message || "").trim().slice(0, 500);
    if (!boundedMessage) return;
    nextIdRef.current += 1;
    setNotice({ id: nextIdRef.current, kind, message: boundedMessage });
  }, []);

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(dismissTransientFailure, TRANSIENT_FAILURE_NOTICE_MS);
    return () => window.clearTimeout(timer);
  }, [dismissTransientFailure, notice]);

  return { notice, showTransientFailure, dismissTransientFailure };
}
