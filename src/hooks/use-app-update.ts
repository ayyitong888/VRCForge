import { useCallback, useEffect, useRef } from "react";
import { checkAppUpdate, type AppUpdateResult } from "../lib/api/app-update";

export function useAppUpdate(
  endpoint: string,
  backgroundRuntimeReady: boolean,
  automaticCheckEnabled: boolean,
  onUpdateAvailable: (result: AppUpdateResult) => void,
) {
  const startedRef = useRef(false);
  const checkForAppUpdateNow = useCallback(
    () => checkAppUpdate(endpoint, undefined, true),
    [endpoint],
  );

  useEffect(() => {
    if (!backgroundRuntimeReady || !automaticCheckEnabled || startedRef.current) return;
    startedRef.current = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const result = await checkAppUpdate(endpoint, controller.signal, false);
        if (!controller.signal.aborted && result.shouldNotify) {
          onUpdateAvailable(result);
        }
      } catch {
        // No update and every check failure are intentionally silent.
      }
    })();
    return () => {
      controller.abort();
    };
  }, [automaticCheckEnabled, backgroundRuntimeReady, endpoint, onUpdateAvailable]);

  return checkForAppUpdateNow;
}
