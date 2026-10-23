import { Dispatch, SetStateAction, useEffect, useRef } from "react";

import { AppBootstrap, refreshUnityReadiness, selectUnityProject } from "../lib/api";
import { normalizeProjectPathKey } from "../lib/project-path";

type UseDashboardProjectSelectionParams = {
  endpoint: string;
  runtimeConnected: boolean;
  projectPath: string;
  projectType: "general" | "unity";
  activeChatId: string;
  confirmedProjectPath: string;
  onConfirmedProjectPath?: (projectPath: string) => void;
  setBootstrap: Dispatch<SetStateAction<AppBootstrap | null>>;
  setError: Dispatch<SetStateAction<string>>;
};

export function useDashboardProjectSelection({
  endpoint,
  runtimeConnected,
  projectPath,
  projectType,
  activeChatId,
  confirmedProjectPath,
  onConfirmedProjectPath,
  setBootstrap,
  setError,
}: UseDashboardProjectSelectionParams) {
  const selectionSequenceRef = useRef(0);
  const selectionQueueRef = useRef<Promise<void>>(Promise.resolve());
  const pendingSelectionPathsRef = useRef(new Map<string, number>());
  const selfSelectedProjectPathsRef = useRef(new Set<string>());
  const pendingUserSelectionPathRef = useRef<string | null>(null);
  const previousProjectPathRef = useRef(projectPath);
  const previousConfirmedProjectPathRef = useRef("");
  const onConfirmedProjectPathRef = useRef(onConfirmedProjectPath);
  onConfirmedProjectPathRef.current = onConfirmedProjectPath;

  useEffect(() => {
    const normalizedProjectPath = normalizeProjectPathKey(projectPath);
    const normalizedConfirmedProjectPath = normalizeProjectPathKey(confirmedProjectPath);
    const projectPathChanged = normalizeProjectPathKey(previousProjectPathRef.current) !== normalizedProjectPath;
    const confirmedProjectPathChanged = normalizeProjectPathKey(previousConfirmedProjectPathRef.current) !== normalizedConfirmedProjectPath;
    previousProjectPathRef.current = projectPath;
    previousConfirmedProjectPathRef.current = confirmedProjectPath;

    if (normalizedConfirmedProjectPath === normalizedProjectPath) {
      pendingUserSelectionPathRef.current = null;
      selfSelectedProjectPathsRef.current.delete(normalizedConfirmedProjectPath);
    } else if (projectPathChanged && normalizedProjectPath) {
      pendingUserSelectionPathRef.current = normalizedProjectPath;
    }
    if (
      confirmedProjectPathChanged
      && normalizedConfirmedProjectPath
      && normalizedConfirmedProjectPath !== normalizedProjectPath
    ) {
      pendingUserSelectionPathRef.current = null;
      const selfSelected = pendingSelectionPathsRef.current.has(normalizedConfirmedProjectPath)
        || selfSelectedProjectPathsRef.current.has(normalizedConfirmedProjectPath);
      if (selfSelected) {
        pendingSelectionPathsRef.current.delete(normalizedConfirmedProjectPath);
        selfSelectedProjectPathsRef.current.delete(normalizedConfirmedProjectPath);
        return;
      }
      selectionSequenceRef.current += 1;
      if (!activeChatId && projectType === "unity" && projectPath.trim()) {
        onConfirmedProjectPathRef.current?.(confirmedProjectPath.trim());
      }
      return;
    }
    const pendingUserSelection = pendingUserSelectionPathRef.current === normalizedProjectPath;
    if (
      (!projectPathChanged && !pendingUserSelection)
      || !runtimeConnected
      || projectType !== "unity"
      || !projectPath.trim()
      || normalizedConfirmedProjectPath === normalizedProjectPath
    ) {
      return;
    }

    const sequence = selectionSequenceRef.current + 1;
    selectionSequenceRef.current = sequence;
    const selectedProjectPathKey = normalizedProjectPath;
    if (pendingUserSelectionPathRef.current === selectedProjectPathKey) {
      pendingUserSelectionPathRef.current = null;
    }
    pendingSelectionPathsRef.current.set(selectedProjectPathKey, sequence);
    let active = true;
    const selection = selectionQueueRef.current
      .catch(() => undefined)
      .then(async () => {
        if (sequence !== selectionSequenceRef.current) {
          if (pendingSelectionPathsRef.current.get(selectedProjectPathKey) === sequence) {
            pendingSelectionPathsRef.current.delete(selectedProjectPathKey);
          }
          return;
        }
        try {
          const state = await selectUnityProject(endpoint, projectPath);
          if (normalizeProjectPathKey(state.selectedProjectPath) !== selectedProjectPathKey) {
            throw new Error("The VRCForge runtime did not confirm the selected Unity project.");
          }
          if (pendingSelectionPathsRef.current.get(selectedProjectPathKey) === sequence) {
            pendingSelectionPathsRef.current.delete(selectedProjectPathKey);
          }
          selfSelectedProjectPathsRef.current.add(selectedProjectPathKey);
          if (sequence !== selectionSequenceRef.current) {
            return;
          }
        } catch (cause) {
          if (pendingSelectionPathsRef.current.get(selectedProjectPathKey) === sequence) {
            pendingSelectionPathsRef.current.delete(selectedProjectPathKey);
          }
          throw cause;
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
  }, [activeChatId, confirmedProjectPath, endpoint, projectPath, projectType, runtimeConnected, setBootstrap, setError]);
}
