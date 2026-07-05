import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AdjustmentCheckpoint, AgentApproval, AgentCheckpoint, AgentCheckpointPreview, InterruptedApplyRecovery, InterruptedApplyRecoveryPreview } from "./types";

export async function fetchCheckpoints(
  endpoint: string,
  projectRoot?: string,
): Promise<{ ok: boolean; checkpoints: AgentCheckpoint[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_checkpoints", {
      request: { projectRoot: projectRoot || undefined, timeoutMs: 30000 },
    });
  }
  const params = new URLSearchParams();
  if (projectRoot) {
    params.set("projectRoot", projectRoot);
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/checkpoints${suffix}`, { preferTauriIpc: true });
}

export async function previewRestoreCheckpoint(endpoint: string, checkpointId: string): Promise<AgentCheckpointPreview> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("preview_restore_checkpoint", {
      request: { checkpointId, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/checkpoints/${encodeURIComponent(checkpointId)}/preview`, {
    method: "POST",
  });
}

export async function requestRestoreCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_restore_checkpoint", {
      request: { checkpointId, timeoutMs: 180000 },
    });
  }
  return requestJson(`${endpoint}/api/app/checkpoints/${encodeURIComponent(checkpointId)}/restore`, {
    method: "POST",
  });
}

export async function fetchInterruptedApplyRecoveries(
  endpoint: string,
  options: { projectRoot?: string; includeResolved?: boolean } = {},
): Promise<{ ok: boolean; recoveries: InterruptedApplyRecovery[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_interrupted_apply_recoveries", {
      request: {
        projectRoot: options.projectRoot || undefined,
        includeResolved: options.includeResolved || undefined,
        timeoutMs: 30000,
      },
    });
  }
  const params = new URLSearchParams();
  if (options.projectRoot) params.set("projectRoot", options.projectRoot);
  if (options.includeResolved) params.set("includeResolved", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/recoveries${suffix}`, { preferTauriIpc: true });
}

export async function previewInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
): Promise<InterruptedApplyRecoveryPreview> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("preview_interrupted_apply_recovery", {
      request: { recoveryId, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/preview`, {
    method: "POST",
  });
}

export async function requestRestoreInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_restore_interrupted_apply_recovery", {
      request: { recoveryId, timeoutMs: 180000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/restore`, {
    method: "POST",
  });
}

export async function resolveInterruptedApplyRecovery(
  endpoint: string,
  recoveryId: string,
  body: { confirmResolved: boolean; note?: string },
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; result?: unknown; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("resolve_interrupted_apply_recovery", {
      request: { recoveryId, ...body, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function exportInterruptedApplyIncidentBundle(
  endpoint: string,
  recoveryId: string,
): Promise<{ ok: boolean; bundlePath?: string; path?: string; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("export_interrupted_apply_incident_bundle", {
      request: { recoveryId, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/recoveries/${encodeURIComponent(recoveryId)}/incident-bundle`, {
    method: "POST",
  });
}

export async function fetchAdjustmentCheckpoints(
  endpoint: string,
  options: { kind?: "face" | "shader"; projectRoot?: string; avatarPath?: string; includeDeleted?: boolean } = {},
): Promise<{ ok: boolean; checkpoints: AdjustmentCheckpoint[]; count: number }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_adjustment_checkpoints", {
      request: { ...options, timeoutMs: 30000 },
    });
  }
  const params = new URLSearchParams();
  if (options.kind) params.set("kind", options.kind);
  if (options.projectRoot) params.set("projectRoot", options.projectRoot);
  if (options.avatarPath) params.set("avatarPath", options.avatarPath);
  if (options.includeDeleted) params.set("includeDeleted", "true");
  const suffix = params.toString() ? `?${params.toString()}` : "";
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints${suffix}`);
}

export async function createAdjustmentCheckpoint(
  endpoint: string,
  body: Partial<AdjustmentCheckpoint> & { kind: "face" | "shader"; overwrite?: boolean },
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; baseCheckpoint?: AgentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("create_adjustment_checkpoint", {
      request: { body, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function updateAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  body: Partial<AdjustmentCheckpoint>,
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("update_adjustment_checkpoint", {
      request: { checkpointId, body, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  hardDelete = false,
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; hardDelete: boolean }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("delete_adjustment_checkpoint", {
      request: { checkpointId, hardDelete, timeoutMs: 60000 },
    });
  }
  const suffix = hardDelete ? "?hardDelete=true" : "";
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}${suffix}`, {
    method: "DELETE",
  });
}

export async function overwriteAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  body: Partial<AdjustmentCheckpoint> = {},
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; baseCheckpoint?: AgentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("overwrite_adjustment_checkpoint", {
      request: { checkpointId, body, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/overwrite`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function selectAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
  body: { slot?: "A" | "B" | "current"; compareGroup?: string } = {},
): Promise<{ ok: boolean; checkpoint: AdjustmentCheckpoint; selection: Record<string, unknown> }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("select_adjustment_checkpoint", {
      request: { checkpointId, body, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/select`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function applyAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<{ ok: boolean; status?: string; approval?: AgentApproval; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("apply_adjustment_checkpoint", {
      request: { checkpointId, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/apply`, {
    method: "POST",
  });
}

export async function previewAdjustmentCheckpoint(
  endpoint: string,
  checkpointId: string,
): Promise<AgentCheckpointPreview & { adjustmentCheckpoint?: AdjustmentCheckpoint }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("preview_adjustment_checkpoint", {
      request: { checkpointId, timeoutMs: 60000 },
    });
  }
  return requestJson(`${endpoint}/api/app/adjustment-checkpoints/${encodeURIComponent(checkpointId)}/preview`, {
    method: "POST",
  });
}
