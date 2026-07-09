import { useEffect, useState } from "react";
import type { ActiveView } from "../lib/app-view";
import {
  fetchAvatars,
  fetchOptimizationPlan,
  fetchOptimizationProof,
  fetchOptimizationProofs,
  requestOptimizationApply,
  requestPackageInstall,
} from "../lib/api";
import type {
  AvatarListItem,
  OptimizationPlannerReport,
  OptimizationProofDetail,
  OptimizationProofSummary,
} from "../lib/api";
import {
  buildOptimizationRequestOptions,
  type OptimizationActionCardItem,
  type OptimizationActionOptions,
} from "../lib/optimization-options";

type OptimizationDependencyItem = NonNullable<NonNullable<OptimizationPlannerReport["dependencyDoctor"]>["dependencies"]>[number];

type UseOptimizationWorkspaceControllerParams = {
  endpoint: string;
  runtimeConnected: boolean;
  unityToolsReady: boolean;
  activeView: ActiveView;
  activeProjectPath: string;
  setActiveView: (view: ActiveView) => void;
  startRuntime: () => Promise<string | null>;
  refreshSilently: (target?: string) => Promise<void>;
  setError: (message: string) => void;
};

export function useOptimizationWorkspaceController({
  endpoint,
  runtimeConnected,
  unityToolsReady,
  activeView,
  activeProjectPath,
  setActiveView,
  startRuntime,
  refreshSilently,
  setError,
}: UseOptimizationWorkspaceControllerParams) {
  const [optimizationReport, setOptimizationReport] = useState<OptimizationPlannerReport | null>(null);
  const [optimizationTargetProfile, setOptimizationTargetProfile] = useState("pc_conservative");
  const [optimizationAvatarPath, setOptimizationAvatarPath] = useState("");
  const [optimizationAvatars, setOptimizationAvatars] = useState<AvatarListItem[]>([]);
  const [loadingOptimizationAvatars, setLoadingOptimizationAvatars] = useState(false);
  const [optimizationAvatarMessage, setOptimizationAvatarMessage] = useState("");
  const [loadingOptimization, setLoadingOptimization] = useState(false);
  const [optimizationMessage, setOptimizationMessage] = useState("");
  const [requestingOptimizationAction, setRequestingOptimizationAction] = useState("");
  const [requestingOptimizationDependency, setRequestingOptimizationDependency] = useState("");
  const [optimizationActionOptions, setOptimizationActionOptions] = useState<Record<string, OptimizationActionOptions>>({});
  const [optimizationProofs, setOptimizationProofs] = useState<OptimizationProofSummary[]>([]);
  const [selectedOptimizationProof, setSelectedOptimizationProof] = useState<OptimizationProofDetail | null>(null);
  const [loadingOptimizationProofs, setLoadingOptimizationProofs] = useState(false);
  const [optimizationProofMessage, setOptimizationProofMessage] = useState("");
  const canRunUnityOptimization = runtimeConnected && unityToolsReady && Boolean(activeProjectPath);

  useEffect(() => {
    if (activeView === "optimization" && canRunUnityOptimization) {
      void loadOptimizationPlan();
    } else if (activeView === "optimization") {
      skipUnityOptimizationLoads();
    }
  }, [activeView, canRunUnityOptimization, endpoint, activeProjectPath, optimizationTargetProfile]);

  useEffect(() => {
    if (activeView === "optimization" && canRunUnityOptimization) {
      void loadOptimizationAvatars();
    }
  }, [activeView, canRunUnityOptimization, endpoint, activeProjectPath]);

  function openOptimization() {
    setActiveView("optimization");
    setError("");
    if (runtimeConnected) {
      void loadOptimizationProofs();
    } else {
      setLoadingOptimizationProofs(false);
      setOptimizationProofMessage("Core is offline. Optimizer proof history skipped.");
    }
    if (canRunUnityOptimization) {
      void Promise.allSettled([loadOptimizationPlan(), loadOptimizationAvatars()]);
    } else {
      skipUnityOptimizationLoads();
    }
  }

  function skipUnityOptimizationLoads() {
    setLoadingOptimization(false);
    setLoadingOptimizationAvatars(false);
    setOptimizationAvatars([]);
    setOptimizationAvatarPath("");
    setOptimizationReport(null);
    if (!activeProjectPath) {
      setOptimizationMessage("Select a Unity project before scanning optimization data.");
      setOptimizationAvatarMessage("Select a Unity project before scanning avatars.");
      return;
    }
    if (!runtimeConnected) {
      setOptimizationMessage("Core is offline. Connect the backend before optimization scans.");
      setOptimizationAvatarMessage("Core is offline. Avatar scan skipped.");
      return;
    }
    if (!unityToolsReady) {
      setOptimizationMessage("Unity MCP is unavailable. Optimization scan skipped.");
      setOptimizationAvatarMessage("Unity MCP is unavailable. Avatar scan skipped.");
    }
  }

  async function loadOptimizationPlan(target = endpoint, profile = optimizationTargetProfile) {
    if (target === endpoint && !canRunUnityOptimization) {
      skipUnityOptimizationLoads();
      return;
    }
    setLoadingOptimization(true);
    setOptimizationMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchOptimizationPlan(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath: optimizationAvatarPath.trim() || undefined,
        targetProfile: profile,
        includeQuest: true,
      });
      setOptimizationReport(payload);
      setOptimizationMessage(payload.ok ? "Plan refreshed" : "Planner returned warnings");
      void loadOptimizationProofs(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimization(false);
    }
  }

  async function loadOptimizationProofs(target = endpoint, runId?: string) {
    setLoadingOptimizationProofs(true);
    setOptimizationProofMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchOptimizationProofs(targetEndpoint, 8);
      const proofs = payload.proofs || [];
      setOptimizationProofs(proofs);
      const selectedRunId = runId || selectedOptimizationProof?.proof?.runId || proofs[0]?.runId || "";
      if (selectedRunId) {
        const detail = await fetchOptimizationProof(targetEndpoint, selectedRunId);
        setSelectedOptimizationProof(detail);
      } else {
        setSelectedOptimizationProof(null);
      }
      setOptimizationProofMessage(proofs.length ? `${proofs.length} proof run${proofs.length === 1 ? "" : "s"}` : "No optimizer proof runs");
    } catch (cause) {
      setOptimizationProofMessage(cause instanceof Error ? cause.message : String(cause));
      setSelectedOptimizationProof(null);
    } finally {
      setLoadingOptimizationProofs(false);
    }
  }

  async function selectOptimizationProof(runId: string) {
    await loadOptimizationProofs(endpoint, runId);
  }

  async function loadOptimizationAvatars(target = endpoint) {
    if (target === endpoint && !canRunUnityOptimization) {
      skipUnityOptimizationLoads();
      return;
    }
    setLoadingOptimizationAvatars(true);
    setOptimizationAvatarMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchAvatars(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
      });
      const avatars = (payload.avatars ?? []).filter((item) => Boolean(item.avatarPath));
      setOptimizationAvatars(avatars);
      if (!optimizationAvatarPath.trim() && avatars.length === 1 && avatars[0].avatarPath) {
        setOptimizationAvatarPath(avatars[0].avatarPath);
      }
      if (payload.ok) {
        setOptimizationAvatarMessage(
          avatars.length ? `${avatars.length} avatar${avatars.length === 1 ? "" : "s"} found` : "No scene avatars found",
        );
      } else {
        setOptimizationAvatarMessage("Avatar scan returned warnings");
      }
    } catch (cause) {
      setOptimizationAvatarMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOptimizationAvatars(false);
    }
  }

  function updateOptimizationActionOption(actionId: string, key: keyof OptimizationActionOptions, value: string) {
    setOptimizationActionOptions((current) => ({
      ...current,
      [actionId]: {
        ...(current[actionId] ?? {}),
        [key]: value,
      },
    }));
  }

  async function requestOptimizationAction(card: OptimizationActionCardItem) {
    if (!card.requestTool) {
      return;
    }
    const avatarPath = optimizationAvatarPath.trim();
    if (!avatarPath) {
      setOptimizationMessage("Set avatar path before requesting an optimizer step.");
      return;
    }
    setRequestingOptimizationAction(card.id);
    setOptimizationMessage("");
    try {
      const requestOptions = buildOptimizationRequestOptions(card, optimizationActionOptions[card.id] ?? {});
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestOptimizationApply(targetEndpoint, {
        tool: card.requestTool,
        projectPath: activeProjectPath || undefined,
        avatarPath,
        targetProfile: optimizationTargetProfile,
        options: requestOptions,
        installMissingDependencies: true,
      });
      setOptimizationMessage(payload.approval ? `Approval queued: ${payload.approval.id}` : payload.error || "Request queued.");
      await refreshSilently(targetEndpoint);
      await loadOptimizationPlan(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOptimizationAction("");
    }
  }

  async function requestOptimizationDependencyInstall(dependency: OptimizationDependencyItem) {
    const packageId = dependency.packageIds?.find((item) => item);
    if (!packageId) {
      return;
    }
    setRequestingOptimizationDependency(dependency.id || packageId);
    setOptimizationMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestPackageInstall(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        packageId,
        repository: dependency.installMethod?.repository || undefined,
        allowAgentManagedDownload: true,
      });
      setOptimizationMessage(payload.approval ? `Install approval queued: ${payload.approval.id}` : payload.error || "Install request queued.");
      await refreshSilently(targetEndpoint);
      await loadOptimizationPlan(targetEndpoint);
    } catch (cause) {
      setOptimizationMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOptimizationDependency("");
    }
  }

  return {
    optimizationReport,
    optimizationTargetProfile,
    optimizationAvatarPath,
    optimizationAvatars,
    loadingOptimizationAvatars,
    optimizationAvatarMessage,
    loadingOptimization,
    optimizationMessage,
    requestingOptimizationAction,
    requestingOptimizationDependency,
    optimizationActionOptions,
    optimizationProofs,
    selectedOptimizationProof,
    loadingOptimizationProofs,
    optimizationProofMessage,
    openOptimization,
    loadOptimizationPlan,
    loadOptimizationProofs,
    selectOptimizationProof,
    loadOptimizationAvatars,
    setOptimizationAvatarPath,
    setOptimizationTargetProfile,
    updateOptimizationActionOption,
    requestOptimizationAction,
    requestOptimizationDependencyInstall,
  };
}
