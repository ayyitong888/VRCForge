import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api/http";
import {
  cancelMemoryReviewRun,
  fetchMemoryReviewSnapshot,
  mutateMemoryReviewCandidate,
  normalizeMemoryReviewMode,
  runMemoryReview,
  updateMemoryReviewConfig,
  type MemoryReviewCandidateAction,
  type MemoryReviewConfigMutation,
  type MemoryReviewScope,
  type MemoryReviewSnapshot,
} from "../lib/api/memory-review";

export type MemoryReviewUiError = "stale_revision" | "request_failed" | null;
export type MemoryReviewConfigDraft = Omit<MemoryReviewConfigMutation, "expectedRevision">;

function finiteRevision(value: unknown): number {
  const revision = Number(value);
  return Number.isInteger(revision) && revision >= 0 ? revision : 0;
}

function normalizeSnapshot(snapshot: MemoryReviewSnapshot): MemoryReviewSnapshot {
  return {
    ...snapshot,
    mode: normalizeMemoryReviewMode(snapshot.mode),
    revision: finiteRevision(snapshot.revision),
    unreadCount: Math.max(0, Number(snapshot.unreadCount) || 0),
    candidates: Array.isArray(snapshot.candidates) ? snapshot.candidates : [],
  };
}

function isRevisionConflict(cause: unknown): boolean {
  if (cause instanceof ApiError) {
    return cause.status === 409;
  }
  return Boolean(
    cause
      && typeof cause === "object"
      && Number((cause as { status?: unknown }).status) === 409,
  );
}

export function useMemoryReview({
  endpoint,
  runtimeConnected,
  selectedProjectPath,
  refreshSignal = 0,
}: {
  endpoint: string;
  runtimeConnected: boolean;
  selectedProjectPath: string;
  refreshSignal?: number;
}) {
  const [snapshot, setSnapshot] = useState<MemoryReviewSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyKey, setBusyKey] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<MemoryReviewUiError>(null);
  const snapshotRef = useRef<MemoryReviewSnapshot | null>(null);
  const busyKeyRef = useRef("");
  const requestSerial = useRef(0);
  const lastAppliedRequest = useRef(0);
  const contextEpoch = useRef(0);
  const contextKey = `${endpoint}\u0000${runtimeConnected ? "connected" : "disconnected"}\u0000${selectedProjectPath}`;
  const previousContextKey = useRef(contextKey);
  if (previousContextKey.current !== contextKey) {
    previousContextKey.current = contextKey;
    contextEpoch.current += 1;
  }

  const applyAuthoritativeSnapshot = useCallback((
    incoming: MemoryReviewSnapshot,
    requestId: number,
    expectedEpoch: number,
  ): boolean => {
    if (expectedEpoch !== contextEpoch.current) {
      return false;
    }
    const next = normalizeSnapshot(incoming);
    const currentRevision = finiteRevision(snapshotRef.current?.revision);
    if (next.revision < currentRevision) {
      return false;
    }
    if (next.revision === currentRevision && requestId < lastAppliedRequest.current) {
      return false;
    }
    lastAppliedRequest.current = requestId;
    snapshotRef.current = next;
    setSnapshot(next);
    setError(null);
    return true;
  }, []);

  const refresh = useCallback(async (showLoading = true) => {
    const expectedEpoch = contextEpoch.current;
    const requestId = ++requestSerial.current;
    if (!runtimeConnected) {
      snapshotRef.current = null;
      setSnapshot(null);
      setLoading(false);
      return;
    }
    if (showLoading) {
      setLoading(true);
    }
    try {
      const next = await fetchMemoryReviewSnapshot(endpoint, {
        projectRoot: selectedProjectPath || undefined,
      });
      applyAuthoritativeSnapshot(next, requestId, expectedEpoch);
    } catch {
      if (expectedEpoch === contextEpoch.current && requestId >= lastAppliedRequest.current) {
        setError("request_failed");
      }
    } finally {
      if (showLoading && expectedEpoch === contextEpoch.current && requestId >= lastAppliedRequest.current) {
        setLoading(false);
      }
    }
  }, [applyAuthoritativeSnapshot, endpoint, runtimeConnected, selectedProjectPath]);

  const performMutation = useCallback(async (
    key: string,
    mutation: (current: MemoryReviewSnapshot) => Promise<MemoryReviewSnapshot>,
  ): Promise<boolean> => {
    const current = snapshotRef.current;
    if (!runtimeConnected || !current || busyKeyRef.current) {
      return false;
    }
    const expectedEpoch = contextEpoch.current;
    const requestId = ++requestSerial.current;
    busyKeyRef.current = key;
    setBusyKey(key);
    setError(null);
    try {
      const next = await mutation(current);
      return applyAuthoritativeSnapshot(next, requestId, expectedEpoch);
    } catch (cause) {
      if (expectedEpoch === contextEpoch.current && requestId >= lastAppliedRequest.current) {
        if (isRevisionConflict(cause)) {
          setError("stale_revision");
          void refresh(false);
        } else {
          setError("request_failed");
        }
      }
      return false;
    } finally {
      if (expectedEpoch === contextEpoch.current) {
        busyKeyRef.current = "";
        setBusyKey("");
      }
    }
  }, [applyAuthoritativeSnapshot, refresh, runtimeConnected]);

  const saveConfig = useCallback((draft: MemoryReviewConfigDraft) => performMutation(
    "config",
    (current) => updateMemoryReviewConfig(endpoint, {
      ...draft,
      projectRoot: draft.scope === "project" ? draft.projectRoot || selectedProjectPath || undefined : undefined,
      expectedRevision: finiteRevision(current.revision),
    }),
  ), [endpoint, performMutation, selectedProjectPath]);

  const startReview = useCallback((scope?: MemoryReviewScope) => performMutation(
    "run",
    (current) => {
      const effectiveScope = scope || current.scope;
      return runMemoryReview(endpoint, {
        scope: effectiveScope,
        projectRoot: effectiveScope === "project" ? current.projectRoot || selectedProjectPath || undefined : undefined,
        expectedRevision: finiteRevision(current.revision),
      });
    },
  ), [endpoint, performMutation, selectedProjectPath]);

  const cancelRun = useCallback(async (): Promise<boolean> => {
    const current = snapshotRef.current;
    const runId = String(current?.lastRun?.runId || "");
    if (!runtimeConnected || !runId || cancelling) {
      return false;
    }
    const expectedEpoch = contextEpoch.current;
    const requestId = ++requestSerial.current;
    setCancelling(true);
    try {
      const next = await cancelMemoryReviewRun(endpoint, { runId });
      return applyAuthoritativeSnapshot(next, requestId, expectedEpoch);
    } catch {
      if (expectedEpoch === contextEpoch.current && requestId >= lastAppliedRequest.current) {
        setError("request_failed");
      }
      return false;
    } finally {
      if (expectedEpoch === contextEpoch.current) {
        setCancelling(false);
      }
    }
  }, [applyAuthoritativeSnapshot, cancelling, endpoint, runtimeConnected]);

  const decideCandidate = useCallback((
    candidateId: string,
    action: MemoryReviewCandidateAction,
    editedText?: string,
  ) => performMutation(
    `candidate:${candidateId}:${action}`,
    (current) => {
      const candidate = current.candidates.find((item) => item.candidateId === candidateId);
      const projectBoundMutation = candidate?.scope === "project";
      return mutateMemoryReviewCandidate(endpoint, candidateId, action, {
        expectedRevision: finiteRevision(current.revision),
        ...(projectBoundMutation
          ? { projectRoot: current.projectRoot || selectedProjectPath || undefined }
          : {}),
        ...(action === "accept" && editedText?.trim() ? { editedText: editedText.trim() } : {}),
      });
    },
  ), [endpoint, performMutation, selectedProjectPath]);

  useEffect(() => {
    snapshotRef.current = null;
    lastAppliedRequest.current = 0;
    busyKeyRef.current = "";
    setSnapshot(null);
    setBusyKey("");
    setCancelling(false);
    setError(null);
    void refresh(true);
  }, [contextKey, refresh]);

  const previousRefreshSignal = useRef(refreshSignal);
  useEffect(() => {
    if (previousRefreshSignal.current === refreshSignal) {
      return;
    }
    previousRefreshSignal.current = refreshSignal;
    void refresh(false);
  }, [refresh, refreshSignal]);

  return {
    snapshot,
    loading,
    busyKey,
    cancelling,
    error,
    refresh,
    saveConfig,
    startReview,
    cancelRun,
    decideCandidate,
  };
}
