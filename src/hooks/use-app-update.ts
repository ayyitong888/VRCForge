import { useEffect, useRef } from "react";
import { checkAppUpdate, type AppUpdateResult } from "../lib/api/app-update";

export function useAppUpdate(
  endpoint: string,
  runtimeConnected: boolean,
  onUpdateAvailable: (result: AppUpdateResult) => void,
) {
  const startedRef = useRef(false);

  useEffect(() => {
    if (!runtimeConnected || startedRef.current) return;
    startedRef.current = true;
    const controller = new AbortController();
    void (async () => {
      try {
        const result = await checkAppUpdate(endpoint, controller.signal);
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
  }, [endpoint, runtimeConnected, onUpdateAvailable]);
}
