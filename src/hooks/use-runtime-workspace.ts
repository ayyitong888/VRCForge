import { Dispatch, SetStateAction, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentApproval,
  AgentDesktopAction,
  AgentGoal,
  AgentMemory,
  AgentRuntimeRun,
  AppBootstrap,
  DesktopRuntimeSnapshot,
  WorkspaceDiffSummary,
  fetchDesktopRuntimeSnapshot,
  fetchWorkspaceDiff,
  refreshUnityReadiness,
} from "../lib/api";
import { isTauriRuntime } from "../lib/app-runtime";
import { markdownSmokeGoals, markdownSmokeMemories } from "../lib/markdown-smoke";

type UseRuntimeWorkspaceParams = {
  endpoint: string;
  runtimeConnected: boolean;
  sessionId: string;
  activeRuntimeProjectPath: string;
  activeProjectPath: string;
  rightSidebarCollapsed: boolean;
  sending: boolean;
  pendingApprovals: number;
  setBootstrap: Dispatch<SetStateAction<AppBootstrap | null>>;
  setAgentApprovals: Dispatch<SetStateAction<AgentApproval[] | null>>;
  setError: Dispatch<SetStateAction<string>>;
};

export function useRuntimeWorkspace({
  endpoint,
  runtimeConnected,
  sessionId,
  activeRuntimeProjectPath,
  activeProjectPath,
  rightSidebarCollapsed,
  sending,
  pendingApprovals,
  setBootstrap,
  setAgentApprovals,
  setError,
}: UseRuntimeWorkspaceParams) {
  const { t } = useTranslation();
  const [workspaceDiff, setWorkspaceDiff] = useState<WorkspaceDiffSummary | null>(null);
  const [loadingWorkspaceDiff, setLoadingWorkspaceDiff] = useState(false);
  const [workspaceDiffError, setWorkspaceDiffError] = useState("");
  const [workspaceDiffReviewOpen, setWorkspaceDiffReviewOpen] = useState(false);
  const [loadingWorkspaceDiffPatch, setLoadingWorkspaceDiffPatch] = useState(false);
  const [loadingUnityStatus, setLoadingUnityStatus] = useState(false);
  const [runtimeRuns, setRuntimeRuns] = useState<AgentRuntimeRun[]>([]);
  const [runtimeRunsError, setRuntimeRunsError] = useState("");
  const [desktopActions, setDesktopActions] = useState<AgentDesktopAction[]>([]);
  const [agentGoals, setAgentGoals] = useState<AgentGoal[]>(() => markdownSmokeGoals());
  const [agentMemory, setAgentMemory] = useState<AgentMemory[]>(() => markdownSmokeMemories());
  const [workspaceStateError, setWorkspaceStateError] = useState("");
  const [runtimeNotice, setRuntimeNotice] = useState("");
  const runtimeRefreshSeqRef = useRef(0);
  const runtimeSnapshotInFlightRef = useRef(new Map<string, Promise<DesktopRuntimeSnapshot>>());

  useEffect(() => {
    if (!runtimeConnected) {
      setWorkspaceDiff(null);
      setWorkspaceDiffError("");
    }
  }, [runtimeConnected, endpoint, activeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected) {
      setRuntimeRuns([]);
      setRuntimeRunsError("");
      return;
    }
    void refreshRuntimeRuns(false);
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath]);

  useEffect(() => {
    if (!runtimeConnected || rightSidebarCollapsed) {
      return;
    }
    const intervalMs = isTauriRuntime() ? (sending || pendingApprovals > 0 ? 5000 : 15000) : sending || pendingApprovals > 0 ? 2500 : 5000;
    const timer = window.setInterval(() => {
      void refreshRuntimeRuns(false);
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath, rightSidebarCollapsed, sending, pendingApprovals]);

  async function refreshUnityStatus(target = endpoint) {
    if (!runtimeConnected || loadingUnityStatus) {
      return;
    }
    setLoadingUnityStatus(true);
    try {
      const payload = await refreshUnityReadiness(target);
      setBootstrap((current) => (current ? { ...current, health: payload.health } : current));
      setWorkspaceStateError("");
      setError((current) => (current.toLowerCase().includes("unity") ? "" : current));
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setWorkspaceStateError(message);
      setError(message);
    } finally {
      setLoadingUnityStatus(false);
    }
  }

  async function refreshWorkspaceDiff(showLoading = true, includePatch = workspaceDiffReviewOpen) {
    if (!runtimeConnected) {
      return;
    }
    if (showLoading) {
      setLoadingWorkspaceDiff(true);
    }
    if (includePatch) {
      setLoadingWorkspaceDiffPatch(true);
    }
    try {
      const payload = await fetchWorkspaceDiff(endpoint, activeProjectPath, includePatch);
      setWorkspaceDiff(payload);
      setWorkspaceDiffError(payload.ok ? "" : payload.error || t("workspace.diffUnavailable"));
    } catch (cause) {
      setWorkspaceDiffError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (showLoading) {
        setLoadingWorkspaceDiff(false);
      }
      if (includePatch) {
        setLoadingWorkspaceDiffPatch(false);
      }
    }
  }

  function fetchRuntimeSnapshotOnce(
    key: string,
    target: string,
    params: { sessionId?: string; projectRoot?: string; includePatch?: boolean; globalOnly?: boolean },
  ) {
    const existing = runtimeSnapshotInFlightRef.current.get(key);
    if (existing) {
      return existing;
    }
    const request = fetchDesktopRuntimeSnapshot(target, params).finally(() => {
      if (runtimeSnapshotInFlightRef.current.get(key) === request) {
        runtimeSnapshotInFlightRef.current.delete(key);
      }
    });
    runtimeSnapshotInFlightRef.current.set(key, request);
    return request;
  }

  async function refreshRuntimeRuns(showError = false, target = endpoint) {
    const refreshSeq = runtimeRefreshSeqRef.current + 1;
    runtimeRefreshSeqRef.current = refreshSeq;
    const requestSessionId = sessionId;
    const requestProjectRoot = activeRuntimeProjectPath || "";
    const isLatestRefresh = () =>
      runtimeRefreshSeqRef.current === refreshSeq && requestSessionId === sessionId && requestProjectRoot === (activeRuntimeProjectPath || "");
    if (!runtimeConnected) {
      setRuntimeRuns([]);
      setRuntimeRunsError("");
      setDesktopActions([]);
      setAgentGoals([]);
      setAgentMemory([]);
      setAgentApprovals(null);
      setWorkspaceStateError("");
      return;
    }
    try {
      const projectRoot = requestProjectRoot || undefined;
      const includePatch = workspaceDiffReviewOpen;
      const snapshotKey = JSON.stringify([target, requestSessionId || "", projectRoot || "", includePatch ? "patch" : "summary"]);
      const snapshot = await fetchRuntimeSnapshotOnce(snapshotKey, target, {
        sessionId: requestSessionId || undefined,
        projectRoot,
        globalOnly: !projectRoot,
        includePatch,
      });
      if (!isLatestRefresh()) {
        return;
      }
      if (snapshot.workspaceDiff) {
        setWorkspaceDiff(snapshot.workspaceDiff);
        setWorkspaceDiffError(snapshot.workspaceDiff.ok ? "" : snapshot.workspaceDiff.error || t("workspace.diffUnavailable"));
      }
      setAgentApprovals(snapshot.approvals?.approvals ?? []);
      setRuntimeRuns(snapshot.runs?.runs ?? []);
      setDesktopActions(snapshot.desktopActions?.actions ?? []);
      setAgentGoals(snapshot.goals?.goals ?? []);
      setAgentMemory(snapshot.memory?.memories ?? []);
      setRuntimeRunsError("");
      setWorkspaceStateError("");
    } catch (cause) {
      if (!isLatestRefresh()) {
        return;
      }
      const message = cause instanceof Error ? cause.message : String(cause);
      setRuntimeRunsError(message);
      setWorkspaceStateError(message);
      if (showError) {
        setRuntimeNotice(message);
      }
    }
  }

  function toggleWorkspaceDiffReview() {
    setWorkspaceDiffReviewOpen((open) => {
      const next = !open;
      if (next && runtimeConnected && !workspaceDiff?.patch) {
        void refreshWorkspaceDiff(false, true);
      }
      return next;
    });
  }

  function prependDesktopAction(action: AgentDesktopAction) {
    setDesktopActions((items) => [action, ...items].slice(0, 8));
  }

  function upsertAgentGoal(goal: AgentGoal) {
    setAgentGoals((items) => [goal, ...items.filter((item) => item.goalId !== goal.goalId)].slice(0, 8));
  }

  function upsertAgentMemory(memory: AgentMemory) {
    setAgentMemory((items) => [memory, ...items.filter((item) => item.memoryId !== memory.memoryId)].slice(0, 8));
  }

  return {
    workspaceDiff,
    loadingWorkspaceDiff,
    workspaceDiffError,
    workspaceDiffReviewOpen,
    loadingWorkspaceDiffPatch,
    loadingUnityStatus,
    runtimeRuns,
    runtimeRunsError,
    desktopActions,
    agentGoals,
    agentMemory,
    workspaceStateError,
    runtimeNotice,
    setRuntimeNotice,
    refreshUnityStatus,
    refreshWorkspaceDiff,
    refreshRuntimeRuns,
    toggleWorkspaceDiffReview,
    prependDesktopAction,
    upsertAgentGoal,
    upsertAgentMemory,
  };
}
