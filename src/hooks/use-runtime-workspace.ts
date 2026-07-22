import { Dispatch, SetStateAction, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AgentApproval,
  AgentDesktopAction,
  AgentGoal,
  AgentMemory,
  AgentProgress,
  AgentQuestion,
  AgentRuntimeRun,
  AppBootstrap,
  DesktopBridgeStatus,
  DesktopRuntimeSnapshot,
  WorkspaceDiffSummary,
  cancelAgentDesktopAction,
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
  const [activeDesktopActions, setActiveDesktopActions] = useState<AgentDesktopAction[]>([]);
  const [desktopBridge, setDesktopBridge] = useState<DesktopBridgeStatus | null>(null);
  const [cancellingDesktopActionIds, setCancellingDesktopActionIds] = useState<string[]>([]);
  const [agentGoals, setAgentGoals] = useState<AgentGoal[]>(() => markdownSmokeGoals());
  const [agentProgress, setAgentProgress] = useState<AgentProgress[]>([]);
  const [agentQuestions, setAgentQuestions] = useState<AgentQuestion[]>([]);
  const [agentMemory, setAgentMemory] = useState<AgentMemory[]>(() => markdownSmokeMemories());
  const [memoryReviewUnreadCount, setMemoryReviewUnreadCount] = useState(0);
  const [memoryReviewNeedsAttention, setMemoryReviewNeedsAttention] = useState(false);
  const [workspaceStateError, setWorkspaceStateError] = useState("");
  const [runtimeNotice, setRuntimeNotice] = useState("");
  const runtimeRefreshSeqRef = useRef(0);
  const runtimeScopeToken = useMemo(
    () => Symbol("runtime-workspace-scope"),
    [runtimeConnected, endpoint, sessionId, activeRuntimeProjectPath],
  );
  const runtimeScopeRef = useRef({
    token: runtimeScopeToken,
    endpoint,
    runtimeConnected,
    sessionId,
    projectRoot: activeRuntimeProjectPath || "",
  });
  const runtimeSnapshotInFlightRef = useRef(new Map<string, Promise<DesktopRuntimeSnapshot>>());

  useEffect(() => {
    if (!runtimeConnected) {
      setWorkspaceDiff(null);
      setWorkspaceDiffError("");
    }
  }, [runtimeConnected, endpoint, activeProjectPath]);

  useLayoutEffect(() => {
    // Project/session changes must invalidate the visible runtime projection
    // before the next paint. Otherwise a failed or slow snapshot can leave a
    // previous project's Save-as-Skill source actionable in the new project.
    runtimeScopeRef.current = {
      token: runtimeScopeToken,
      endpoint,
      runtimeConnected,
      sessionId,
      projectRoot: activeRuntimeProjectPath || "",
    };
    runtimeRefreshSeqRef.current += 1;
    setRuntimeRuns([]);
    setRuntimeRunsError("");
    if (!runtimeConnected) {
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
    const requestScopeToken = runtimeScopeToken;
    const requestEndpoint = endpoint;
    const requestRuntimeConnected = runtimeConnected;
    const requestSessionId = sessionId;
    const requestProjectRoot = activeRuntimeProjectPath || "";
    const currentScope = runtimeScopeRef.current;
    if (
      currentScope.token !== requestScopeToken
      || target !== requestEndpoint
      || currentScope.endpoint !== requestEndpoint
      || currentScope.runtimeConnected !== requestRuntimeConnected
      || currentScope.sessionId !== requestSessionId
      || currentScope.projectRoot !== requestProjectRoot
    ) {
      return;
    }
    const refreshSeq = runtimeRefreshSeqRef.current + 1;
    runtimeRefreshSeqRef.current = refreshSeq;
    const isLatestRefresh = () => {
      const latestScope = runtimeScopeRef.current;
      return runtimeRefreshSeqRef.current === refreshSeq
        && latestScope.token === requestScopeToken
        && latestScope.endpoint === requestEndpoint
        && latestScope.runtimeConnected === requestRuntimeConnected
        && latestScope.sessionId === requestSessionId
        && latestScope.projectRoot === requestProjectRoot;
    };
    if (!requestRuntimeConnected) {
      setRuntimeRuns([]);
      setRuntimeRunsError("");
      setDesktopActions([]);
      setActiveDesktopActions([]);
      setDesktopBridge(null);
      setAgentGoals([]);
      setAgentProgress([]);
      setAgentQuestions([]);
      setAgentMemory([]);
      setMemoryReviewUnreadCount(0);
      setMemoryReviewNeedsAttention(false);
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
      setActiveDesktopActions(snapshot.activeDesktopActions?.actions ?? []);
      setDesktopBridge(snapshot.desktopBridge ?? null);
      setAgentGoals(snapshot.goals?.goals ?? []);
      setAgentProgress(snapshot.progress?.items ?? []);
      setAgentQuestions(snapshot.questions?.questions ?? []);
      setAgentMemory(snapshot.memory?.memories ?? []);
      setMemoryReviewUnreadCount(Math.max(0, Number(snapshot.memoryReview?.unreadCount) || 0));
      setMemoryReviewNeedsAttention(snapshot.memoryReview?.needsAttention === true);
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
    const actionId = action.actionId || action.id || "";
    setDesktopActions((items) => [action, ...items.filter((item) => !actionId || (item.actionId || item.id || "") !== actionId)].slice(0, 8));
    setActiveDesktopActions((items) => {
      const remaining = items.filter((item) => !actionId || (item.actionId || item.id || "") !== actionId);
      return ["requested", "claimed", "cancel_requested"].includes(action.status || "")
        ? [action, ...remaining].slice(0, 8)
        : remaining;
    });
  }

  async function cancelDesktopAction(actionId: string) {
    const normalized = actionId.trim();
    if (!normalized || cancellingDesktopActionIds.includes(normalized)) {
      return;
    }
    setCancellingDesktopActionIds((items) => [...items, normalized]);
    try {
      const payload = await cancelAgentDesktopAction(endpoint, normalized);
      if (payload.action) {
        prependDesktopAction(payload.action);
      }
      await refreshRuntimeRuns(false);
      setRuntimeNotice("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setRuntimeNotice(message);
      setError(message);
    } finally {
      setCancellingDesktopActionIds((items) => items.filter((item) => item !== normalized));
    }
  }

  function upsertAgentGoal(goal: AgentGoal) {
    setAgentGoals((items) => [goal, ...items.filter((item) => item.goalId !== goal.goalId)].slice(0, 8));
  }

  function upsertAgentProgress(progress: AgentProgress) {
    setAgentProgress((items) => [progress, ...items.filter((item) => item.progressId !== progress.progressId)].slice(0, 12));
  }

  function upsertAgentQuestion(question: AgentQuestion) {
    setAgentQuestions((items) => [question, ...items.filter((item) => item.questionId !== question.questionId)].slice(0, 8));
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
    activeDesktopActions,
    desktopBridge,
    cancellingDesktopActionIds,
    agentGoals,
    agentProgress,
    agentQuestions,
    agentMemory,
    memoryReviewUnreadCount,
    memoryReviewNeedsAttention,
    workspaceStateError,
    runtimeNotice,
    setRuntimeNotice,
    refreshUnityStatus,
    refreshWorkspaceDiff,
    refreshRuntimeRuns,
    toggleWorkspaceDiffReview,
    prependDesktopAction,
    cancelDesktopAction,
    upsertAgentGoal,
    upsertAgentProgress,
    upsertAgentQuestion,
    upsertAgentMemory,
  };
}
