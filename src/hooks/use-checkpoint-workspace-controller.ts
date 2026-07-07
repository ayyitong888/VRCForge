import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ActiveView } from "../lib/app-view";
import {
  applyAdjustmentCheckpoint,
  createAdjustmentCheckpoint,
  deleteAdjustmentCheckpoint,
  exportInterruptedApplyIncidentBundle,
  fetchAdjustmentCheckpoints,
  fetchCheckpoints,
  fetchInterruptedApplyRecoveries,
  overwriteAdjustmentCheckpoint,
  previewAdjustmentCheckpoint,
  previewInterruptedApplyRecovery,
  previewRestoreCheckpoint,
  requestRestoreCheckpoint,
  requestRestoreInterruptedApplyRecovery,
  resolveInterruptedApplyRecovery,
  selectAdjustmentCheckpoint,
  updateAdjustmentCheckpoint,
} from "../lib/api";
import type {
  AdjustmentCheckpoint,
  AgentCheckpoint,
  AgentCheckpointPreview,
  InterruptedApplyRecovery,
  InterruptedApplyRecoveryPreview,
} from "../lib/api";

type AdjustmentCheckpointPreview = AgentCheckpointPreview & { adjustmentCheckpoint?: AdjustmentCheckpoint };

type UseCheckpointWorkspaceControllerParams = {
  endpoint: string;
  runtimeConnected: boolean;
  activeView: ActiveView;
  activeProjectPath: string;
  setActiveView: (view: ActiveView) => void;
  startRuntime: () => Promise<string | null>;
  refresh: (target?: string) => Promise<void>;
  setError: (message: string) => void;
};

export function useCheckpointWorkspaceController({
  endpoint,
  runtimeConnected,
  activeView,
  activeProjectPath,
  setActiveView,
  startRuntime,
  refresh,
  setError,
}: UseCheckpointWorkspaceControllerParams) {
  const { t } = useTranslation();
  const [checkpoints, setCheckpoints] = useState<AgentCheckpoint[]>([]);
  const [interruptedRecoveries, setInterruptedRecoveries] = useState<InterruptedApplyRecovery[]>([]);
  const [adjustmentCheckpoints, setAdjustmentCheckpoints] = useState<AdjustmentCheckpoint[]>([]);
  const [checkpointPreview, setCheckpointPreview] = useState<AgentCheckpointPreview | null>(null);
  const [recoveryPreview, setRecoveryPreview] = useState<InterruptedApplyRecoveryPreview | null>(null);
  const [adjustmentPreview, setAdjustmentPreview] = useState<AdjustmentCheckpointPreview | null>(null);
  const [loadingCheckpoints, setLoadingCheckpoints] = useState(false);
  const [restoringCheckpointId, setRestoringCheckpointId] = useState("");
  const [recoveryBusyId, setRecoveryBusyId] = useState("");
  const [adjustmentBusyId, setAdjustmentBusyId] = useState("");
  const [checkpointMessage, setCheckpointMessage] = useState("");
  const [recoveryMessage, setRecoveryMessage] = useState("");
  const [adjustmentMessage, setAdjustmentMessage] = useState("");

  useEffect(() => {
    if (activeView === "checkpoints" && runtimeConnected) {
      void loadCheckpoints();
    }
  }, [activeView, runtimeConnected, endpoint, activeProjectPath]);

  async function openCheckpoints() {
    setActiveView("checkpoints");
    await loadCheckpoints();
  }

  async function loadCheckpoints(target = endpoint) {
    setLoadingCheckpoints(true);
    try {
      let targetEndpoint = target;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const [payload, recoveryPayload, adjustmentPayload] = await Promise.all([
        fetchCheckpoints(targetEndpoint, activeProjectPath || undefined),
        fetchInterruptedApplyRecoveries(targetEndpoint, { projectRoot: activeProjectPath || undefined }),
        fetchAdjustmentCheckpoints(targetEndpoint, { projectRoot: activeProjectPath || undefined }),
      ]);
      const nextCheckpoints = payload.checkpoints || [];
      const nextRecoveries = recoveryPayload.recoveries || [];
      const nextAdjustmentCheckpoints = adjustmentPayload.checkpoints || [];
      setCheckpoints(nextCheckpoints);
      setInterruptedRecoveries(nextRecoveries);
      setAdjustmentCheckpoints(nextAdjustmentCheckpoints);
      if (checkpointPreview?.checkpoint?.id && !nextCheckpoints.some((item) => item.id === checkpointPreview.checkpoint?.id)) {
        setCheckpointPreview(null);
      }
      const recoveryId = recoveryPreview?.recovery?.id;
      if (recoveryId && !nextRecoveries.some((item) => item.id === recoveryId)) {
        setRecoveryPreview(null);
      }
      const adjustmentId = adjustmentPreview?.adjustmentCheckpoint?.id;
      if (adjustmentId && !nextAdjustmentCheckpoints.some((item) => item.id === adjustmentId)) {
        setAdjustmentPreview(null);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingCheckpoints(false);
    }
  }

  async function previewCheckpoint(checkpointId: string) {
    setLoadingCheckpoints(true);
    setCheckpointMessage("");
    try {
      const payload = await previewRestoreCheckpoint(endpoint, checkpointId);
      setCheckpointPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Checkpoint preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingCheckpoints(false);
    }
  }

  async function restoreCheckpoint(checkpointId: string) {
    setRestoringCheckpointId(checkpointId);
    setCheckpointMessage("");
    setError("");
    try {
      const payload = await requestRestoreCheckpoint(endpoint, checkpointId);
      if (payload.status === "pending" || payload.status === "pending_approval") {
        setCheckpointMessage("Restore approval is pending.");
      } else if (payload.ok) {
        setCheckpointMessage("Checkpoint restored.");
      } else {
        setCheckpointMessage(String(payload.error || "Restore request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRestoringCheckpointId("");
    }
  }

  async function previewRecovery(recoveryId: string) {
    setRecoveryBusyId(recoveryId);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await previewInterruptedApplyRecovery(endpoint, recoveryId);
      setRecoveryPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Recovery preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function restoreRecovery(recoveryId: string) {
    setRecoveryBusyId(`restore:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await requestRestoreInterruptedApplyRecovery(endpoint, recoveryId);
      if (payload.status === "pending" || payload.status === "pending_approval") {
        setRecoveryMessage("Restore approval is pending.");
      } else if (payload.ok) {
        setRecoveryMessage("Interrupted write restored.");
      } else {
        setRecoveryMessage(String(payload.error || "Restore request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function exportRecoveryBundle(recoveryId: string) {
    setRecoveryBusyId(`bundle:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await exportInterruptedApplyIncidentBundle(endpoint, recoveryId);
      if (payload.ok) {
        setRecoveryMessage(`Incident bundle exported: ${payload.bundlePath || payload.path || "-"}`);
      } else {
        setRecoveryMessage(String(payload.error || "Incident bundle export failed."));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function resolveRecovery(recoveryId: string) {
    if (!window.confirm("Mark this interrupted write as resolved without restoring its checkpoint?")) {
      return;
    }
    setRecoveryBusyId(`resolve:${recoveryId}`);
    setRecoveryMessage("");
    setError("");
    try {
      const payload = await resolveInterruptedApplyRecovery(endpoint, recoveryId, {
        confirmResolved: true,
        note: "Resolved from the desktop Checkpoints view.",
      });
      if (payload.status === "pending" || payload.status === "pending_approval") {
        setRecoveryMessage("Resolve approval is pending.");
      } else if (payload.ok) {
        setRecoveryMessage("Interrupted write resolved.");
      } else {
        setRecoveryMessage(String(payload.error || "Resolve request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryBusyId("");
    }
  }

  async function createAdjustment(kind: "face" | "shader") {
    setAdjustmentBusyId(`create:${kind}`);
    setAdjustmentMessage("");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await createAdjustmentCheckpoint(targetEndpoint, {
        kind,
        projectRoot: activeProjectPath || undefined,
        label: kind === "face" ? "Face adjustment" : "Shader adjustment",
      });
      setAdjustmentMessage(`${kind === "face" ? t("checkpoint.face") : t("checkpoint.shader")} checkpoint created.`);
      setAdjustmentPreview(payload.baseCheckpoint ? { ok: true, checkpoint: payload.baseCheckpoint, adjustmentCheckpoint: payload.checkpoint } : null);
      await loadCheckpoints(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function renameAdjustment(checkpoint: AdjustmentCheckpoint) {
    const currentLabel = checkpoint.label || checkpoint.id;
    const nextLabel = window.prompt("Checkpoint label", currentLabel);
    if (nextLabel === null || nextLabel.trim() === currentLabel) {
      return;
    }
    setAdjustmentBusyId(checkpoint.id);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await updateAdjustmentCheckpoint(endpoint, checkpoint.id, { label: nextLabel.trim() });
      setAdjustmentMessage("Adjustment checkpoint updated.");
      setAdjustmentPreview((previous) =>
        previous?.adjustmentCheckpoint?.id === checkpoint.id
          ? { ...previous, adjustmentCheckpoint: payload.checkpoint }
          : previous,
      );
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function previewAdjustment(checkpointId: string) {
    setAdjustmentBusyId(checkpointId);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await previewAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentPreview(payload);
      if (!payload.ok) {
        setError(payload.error || "Adjustment preview failed.");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function selectAdjustment(checkpointId: string, slot: "A" | "B") {
    setAdjustmentBusyId(`${checkpointId}:${slot}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await selectAdjustmentCheckpoint(endpoint, checkpointId, { slot });
      setAdjustmentMessage(`Selected ${slot}.`);
      setAdjustmentPreview((previous) =>
        previous?.adjustmentCheckpoint?.id === checkpointId
          ? { ...previous, adjustmentCheckpoint: payload.checkpoint }
          : previous,
      );
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function applyAdjustment(checkpointId: string) {
    setAdjustmentBusyId(`apply:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await applyAdjustmentCheckpoint(endpoint, checkpointId);
      if (payload.status === "pending" || payload.status === "pending_approval") {
        setAdjustmentMessage("Apply approval is pending.");
      } else if (payload.ok) {
        setAdjustmentMessage("Adjustment checkpoint applied.");
      } else {
        setAdjustmentMessage(String(payload.error || "Apply request failed."));
      }
      await refresh();
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function overwriteAdjustment(checkpointId: string) {
    if (!window.confirm("Overwrite this adjustment checkpoint with the current project state?")) {
      return;
    }
    setAdjustmentBusyId(`overwrite:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      const payload = await overwriteAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentMessage("Adjustment checkpoint overwritten.");
      setAdjustmentPreview(payload.baseCheckpoint ? { ok: true, checkpoint: payload.baseCheckpoint, adjustmentCheckpoint: payload.checkpoint } : null);
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  async function removeAdjustment(checkpointId: string) {
    if (!window.confirm("Delete this adjustment checkpoint?")) {
      return;
    }
    setAdjustmentBusyId(`delete:${checkpointId}`);
    setAdjustmentMessage("");
    setError("");
    try {
      await deleteAdjustmentCheckpoint(endpoint, checkpointId);
      setAdjustmentMessage("Adjustment checkpoint deleted.");
      if (adjustmentPreview?.adjustmentCheckpoint?.id === checkpointId) {
        setAdjustmentPreview(null);
      }
      await loadCheckpoints();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setAdjustmentBusyId("");
    }
  }

  return {
    checkpoints,
    interruptedRecoveries,
    adjustmentCheckpoints,
    checkpointPreview,
    recoveryPreview,
    adjustmentPreview,
    loadingCheckpoints,
    restoringCheckpointId,
    recoveryBusyId,
    adjustmentBusyId,
    checkpointMessage,
    recoveryMessage,
    adjustmentMessage,
    openCheckpoints,
    loadCheckpoints,
    previewCheckpoint,
    restoreCheckpoint,
    previewRecovery,
    restoreRecovery,
    exportRecoveryBundle,
    resolveRecovery,
    createAdjustment,
    renameAdjustment,
    previewAdjustment,
    selectAdjustment,
    applyAdjustment,
    overwriteAdjustment,
    removeAdjustment,
  };
}
