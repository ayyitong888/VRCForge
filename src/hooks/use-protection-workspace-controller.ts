import { useEffect, useState } from "react";
import type { ActiveView } from "../lib/app-view";
import { fetchAvatars, planAvatarEncryption, requestAvatarEncryptionApply } from "../lib/api";
import type { AvatarEncryptionPlanResult, AvatarListItem } from "../lib/api";
import { protectionPlanPayload } from "../lib/protection-plan";

type UseProtectionWorkspaceControllerParams = {
  endpoint: string;
  runtimeConnected: boolean;
  activeView: ActiveView;
  activeProjectPath: string;
  setActiveView: (view: ActiveView) => void;
  startRuntime: () => Promise<string | null>;
  refreshSilently: (target?: string) => Promise<void>;
  setError: (message: string) => void;
};

export function useProtectionWorkspaceController({
  endpoint,
  runtimeConnected,
  activeView,
  activeProjectPath,
  setActiveView,
  startRuntime,
  refreshSilently,
  setError,
}: UseProtectionWorkspaceControllerParams) {
  const [protectionPlan, setProtectionPlan] = useState<AvatarEncryptionPlanResult | null>(null);
  const [protectionProfile, setProtectionProfile] = useState("standard");
  const [protectionAvatarPath, setProtectionAvatarPath] = useState("");
  const [protectionAvatars, setProtectionAvatars] = useState<AvatarListItem[]>([]);
  const [protectionOwnsAssets, setProtectionOwnsAssets] = useState(false);
  const [loadingProtection, setLoadingProtection] = useState(false);
  const [loadingProtectionAvatars, setLoadingProtectionAvatars] = useState(false);
  const [protectionMessage, setProtectionMessage] = useState("");
  const [protectionAvatarMessage, setProtectionAvatarMessage] = useState("");
  const [requestingProtectionFamily, setRequestingProtectionFamily] = useState("");

  useEffect(() => {
    if (activeView === "protection" && runtimeConnected) {
      void loadProtectionPlan();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath, protectionProfile, protectionOwnsAssets]);

  useEffect(() => {
    if (activeView === "protection" && runtimeConnected) {
      void loadProtectionAvatars();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  async function openProtection() {
    setActiveView("protection");
    setError("");
    await loadProtectionPlan();
    await loadProtectionAvatars();
  }

  async function loadProtectionPlan(target = endpoint, profile = protectionProfile) {
    setLoadingProtection(true);
    setProtectionMessage("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await planAvatarEncryption(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath: protectionAvatarPath.trim() || undefined,
        profile,
        protectionProfile: profile,
        confirmCreatorOwnedAssets: protectionOwnsAssets,
      });
      setProtectionPlan(payload);
      const plan = protectionPlanPayload(payload);
      const candidateCount = Number(plan.selectedCandidateCount ?? 0);
      const connector = (plan.externalAddon || {}) as Record<string, unknown>;
      const connectorConfigured = Boolean(connector.configured);
      const writeStatus = String(plan.writeStatus || "");
      setProtectionMessage(
        payload.ok
          ? connectorConfigured && writeStatus !== "blocked"
            ? `${candidateCount} target${candidateCount === 1 ? "" : "s"} ready for private addon request`
            : `${candidateCount} target${candidateCount === 1 ? "" : "s"} found; private addon required`
          : payload.error || "Protection plan returned warnings",
      );
    } catch (cause) {
      setProtectionMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProtection(false);
    }
  }

  async function loadProtectionAvatars(target = endpoint) {
    setLoadingProtectionAvatars(true);
    setProtectionAvatarMessage("");
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
      setProtectionAvatars(avatars);
      if (!protectionAvatarPath.trim() && avatars.length === 1 && avatars[0].avatarPath) {
        setProtectionAvatarPath(avatars[0].avatarPath);
      }
      setProtectionAvatarMessage(
        payload.ok
          ? avatars.length
            ? `${avatars.length} avatar${avatars.length === 1 ? "" : "s"} found`
            : "No scene avatars found"
          : "Avatar scan returned warnings",
      );
    } catch (cause) {
      setProtectionAvatarMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingProtectionAvatars(false);
    }
  }

  async function requestProtectionApply(targetFamily: "liltoon" | "poiyomi") {
    const avatarPath = protectionAvatarPath.trim();
    if (!avatarPath) {
      setProtectionMessage("Set avatar path before requesting protection.");
      return;
    }
    if (!protectionOwnsAssets) {
      setProtectionMessage("Confirm asset ownership before requesting protection.");
      return;
    }
    setRequestingProtectionFamily(targetFamily);
    setProtectionMessage("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestAvatarEncryptionApply(targetEndpoint, {
        projectPath: activeProjectPath || undefined,
        avatarPath,
        profile: protectionProfile,
        protectionProfile,
        targetShaderFamily: targetFamily,
        targetShaderFamilies: [targetFamily],
        confirmCreatorOwnedAssets: true,
      });
      setProtectionMessage(payload.approval ? `Approval queued: ${payload.approval.id}` : payload.error || "Request queued.");
      await refreshSilently(targetEndpoint);
      await loadProtectionPlan(targetEndpoint);
    } catch (cause) {
      setProtectionMessage(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingProtectionFamily("");
    }
  }

  return {
    protectionPlan,
    protectionProfile,
    protectionAvatarPath,
    protectionAvatars,
    protectionOwnsAssets,
    loadingProtection,
    loadingProtectionAvatars,
    protectionMessage,
    protectionAvatarMessage,
    requestingProtectionFamily,
    openProtection,
    loadProtectionPlan,
    loadProtectionAvatars,
    requestProtectionApply,
    setProtectionProfile,
    setProtectionAvatarPath,
    setProtectionOwnsAssets,
  };
}
