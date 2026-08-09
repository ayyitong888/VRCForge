import { Dispatch, SetStateAction, useEffect, useRef } from "react";

import { AppBootstrap, refreshUnityReadiness, selectUnityProject } from "../lib/api";
import { normalizeProjectPathKey } from "../lib/project-path";

type UseDashboardProjectSelectionParams = {
  endpoint: string;
  runtimeConnected: boolean;
  projectPath: string;
  confirmedProjectPath: string;
  setBootstrap: Dispatch<SetStateAction<AppBootstrap | null>>;
  setError: Dispatch<SetStateAction<string>>;
};

export function useDashboardProjectSelection({
  endpoint,
  runtimeConnected,
  projectPath,
  confirmedProjectPath,
  setBootstrap,
  setError,
}: UseDashboardProjectSelectionParams) {
  const selectionSequenceRef = useRef(0);
  const selectionQueueRef = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    if (
      !runtimeConnected
      || !projectPath.trim()
      || normalizeProjectPathKey(confirmedProjectPath) === normalizeProjectPathKey(projectPath)
    ) {
      return;
    }

    const sequence = selectionSequenceRef.current + 1;
    selectionSequenceRef.current = sequence;
    let active = true;
    const selection = selectionQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        const state = await selectUnityProject(endpoint, projectPath);
        if (normalizeProjectPathKey(state.selectedProjectPath) !== normalizeProjectPathKey(projectPath)) {
          throw new Error("The VRCForge runtime did not confirm the selected Unity project.");
        }
      });
    selectionQueueRef.current = selection.catch(() => undefined);

    void selection
      .then(async () => {
        if (!active || sequence !== selectionSequenceRef.current) {
          return;
        }
        const payload = await refreshUnityReadiness(endpoint);
        if (!active || sequence !== selectionSequenceRef.current) {
          return;
        }
        setBootstrap((current) => (current ? { ...current, health: payload.health } : current));
        setError((current) => (current.toLowerCase().includes("unity") ? "" : current));
      })
      .catch((cause) => {
        if (!active || sequence !== selectionSequenceRef.current) {
          return;
        }
        setError(cause instanceof Error ? cause.message : String(cause));
      });

    return () => {
      active = false;
    };
  }, [confirmedProjectPath, endpoint, projectPath, runtimeConnected, setBootstrap, setError]);
}
