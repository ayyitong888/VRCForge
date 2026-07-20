import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AdvancedSettingsState, ApiConfig, AppBootstrap, AppHealth, AppSessionHandshake, DeveloperOptionsChallenge, DiagnosticLogLevel, DiagnosticsStatus, DoctorFixMode, DoctorFixResult, DoctorReport, PermissionState, ProjectSnapshot, ProviderModelInfo, SupportBundleResult, UnityMcpRepairResult, UnityReadinessRefresh, VisionConfig, WorkspaceDiffSummary } from "./types";

export async function fetchBootstrap(endpoint: string, options: { refreshProjects?: boolean } = {}): Promise<AppBootstrap> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AppBootstrap>("fetch_app_bootstrap", {
      request: { refreshProjects: Boolean(options.refreshProjects), timeoutMs: 30000 },
    });
  }
  const url = new URL(`${endpoint}/api/app/bootstrap`);
  if (options.refreshProjects) {
    url.searchParams.set("refreshProjects", "true");
  }
  return requestJson<AppBootstrap>(url.toString(), { preferTauriIpc: true });
}

export async function fetchAppHealth(endpoint: string): Promise<AppHealth> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AppHealth>("fetch_app_health", {
      request: { timeoutMs: 20000 },
    });
  }
  return requestJson<AppHealth>(`${endpoint}/api/health`, { timeoutMs: 20000 });
}

export async function refreshProjects(endpoint: string): Promise<ProjectSnapshot> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProjectSnapshot>("refresh_projects", {
      request: { timeoutMs: 30000 },
    });
  }
  return requestJson<ProjectSnapshot>(`${endpoint}/api/projects/refresh`, { method: "POST", timeoutMs: 30000 });
}

export async function refreshUnityReadiness(endpoint: string): Promise<UnityReadinessRefresh> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<UnityReadinessRefresh>("refresh_unity_readiness", {
      request: { timeoutMs: 20000 },
    });
  }
  return requestJson<UnityReadinessRefresh>(`${endpoint}/api/app/unity/readiness/refresh`, { method: "POST", timeoutMs: 20000 });
}

export async function fetchAppSession(endpoint: string): Promise<AppSessionHandshake> {
  return requestJson<AppSessionHandshake>(`${endpoint}/api/app/session`, { timeoutMs: 5000 });
}

export async function fetchWorkspaceDiff(endpoint: string, root = "", includePatch = false): Promise<WorkspaceDiffSummary> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<WorkspaceDiffSummary>("fetch_workspace_diff", {
      request: { root: root.trim() || undefined, includePatch, timeoutMs: 30000 },
    });
  }
  const url = new URL(`${endpoint}/api/app/workspace/diff`);
  if (root.trim()) {
    url.searchParams.set("root", root.trim());
  }
  if (includePatch) {
    url.searchParams.set("includePatch", "true");
  }
  return requestJson<WorkspaceDiffSummary>(url.toString(), { preferTauriIpc: true });
}

export async function fetchDoctor(endpoint: string): Promise<DoctorReport> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DoctorReport>("fetch_doctor", {
      request: { timeoutMs: 30000 },
    });
  }
  return requestJson<DoctorReport>(`${endpoint}/api/app/doctor`);
}

export async function fixDoctorCheck(
  endpoint: string,
  checkId: string,
  request: { mode?: DoctorFixMode; projectPath?: string } = {},
): Promise<DoctorFixResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DoctorFixResult>("fix_doctor_check", {
      request: { checkId, ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<DoctorFixResult>(`${endpoint}/api/app/doctor/fix/${encodeURIComponent(checkId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    timeoutMs: 120000,
  });
}

export async function repairUnityMcpBridge(
  endpoint: string,
  request: { projectPath?: string; allowUnityRelaunch?: boolean; waitSeconds?: number; closeTimeoutSeconds?: number } = {},
): Promise<UnityMcpRepairResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<UnityMcpRepairResult>("repair_unity_mcp_bridge", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<UnityMcpRepairResult>(`${endpoint}/api/app/doctor/unity-mcp/repair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function fetchDiagnostics(endpoint: string): Promise<DiagnosticsStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DiagnosticsStatus>("fetch_diagnostics", {
      request: { timeoutMs: 30000 },
    });
  }
  return requestJson<DiagnosticsStatus>(`${endpoint}/api/app/diagnostics`);
}

export async function updateDiagnostics(
  endpoint: string,
  request: { logLevel?: DiagnosticLogLevel; debugLogging?: boolean },
): Promise<DiagnosticsStatus> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DiagnosticsStatus>("update_diagnostics", {
      request: { ...request, timeoutMs: 30000 },
    });
  }
  return requestJson<DiagnosticsStatus>(`${endpoint}/api/app/diagnostics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function exportSupportBundle(endpoint: string, request: { includeFullPaths?: boolean; logLimit?: number } = {}): Promise<SupportBundleResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SupportBundleResult>("export_support_bundle", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<SupportBundleResult>(`${endpoint}/api/app/support-bundle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function updatePermission(
  endpoint: string,
  executionMode: PermissionState["executionMode"],
  acknowledgeRoslynRisk = false,
): Promise<{ ok: boolean; permission: PermissionState }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok: boolean; permission: PermissionState }>("update_permission_mode", {
      request: { execution_mode: executionMode, acknowledge_roslyn_risk: acknowledgeRoslynRisk, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/permission`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      execution_mode: executionMode,
      acknowledge_roslyn_risk: acknowledgeRoslynRisk,
    }),
  });
}

export async function fetchAdvancedSettings(
  endpoint: string,
): Promise<{ ok: boolean; schema: string; settings: AdvancedSettingsState }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("fetch_advanced_settings", {
      request: { timeoutMs: 15000 },
    });
  }
  return requestJson(`${endpoint}/api/app/advanced-settings`, { timeoutMs: 15000 });
}

export async function beginDeveloperOptionsChallenge(endpoint: string): Promise<DeveloperOptionsChallenge> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<DeveloperOptionsChallenge>("begin_developer_options_challenge", {
      request: { timeoutMs: 15000 },
    });
  }
  return requestJson<DeveloperOptionsChallenge>(`${endpoint}/api/app/advanced-settings/developer-challenge`, {
    method: "POST",
    timeoutMs: 15000,
  });
}

export async function cancelDeveloperOptionsChallenge(
  endpoint: string,
  challengeId: string,
): Promise<{ ok: boolean; schema?: string; cancelled?: boolean }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("cancel_developer_options_challenge", {
      request: { challengeId, timeoutMs: 15000 },
    });
  }
  return requestJson(
    `${endpoint}/api/app/advanced-settings/developer-challenge/${encodeURIComponent(challengeId)}`,
    { method: "DELETE", timeoutMs: 15000 },
  );
}

export async function updateAdvancedSettings(
  endpoint: string,
  settings: Pick<AdvancedSettingsState, "developerOptionsEnabled" | "computerUseEnabled"> & {
    developerChallengeId?: string;
  },
): Promise<{ ok: boolean; schema: string; settings: AdvancedSettingsState }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("update_advanced_settings", {
      request: { ...settings, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/app/advanced-settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

export type ChatAttachmentImportBody = {
  payloadHash: string;
  projectPath?: string;
  targetFolder?: string;
  selectedUnityPackage?: string;
  selectedPrefab?: string;
  baseAvatarName?: string;
  maxEntries?: number;
};

/**
 * Ask the backend to stage a vault-stored chat attachment for import. The
 * response is an approval request routed through the normal apply lane; no
 * bytes are written until the user approves it there.
 */
export async function requestChatAttachmentImport(
  endpoint: string,
  body: ChatAttachmentImportBody,
): Promise<{ ok?: boolean; approval?: Record<string, unknown>; preview?: Record<string, unknown> }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_chat_attachment_import", {
      request: { body, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/chat-attachments/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 120000,
  });
}

export async function updateApiConfig(
  endpoint: string,
  config: { provider: string; api_key: string; base_url?: string; model?: string; thinking_level?: string },
) {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok?: boolean; apiConfig: ApiConfig; visionConfig?: VisionConfig }>("update_api_config", {
      request: { ...config, timeoutMs: 30000 },
    });
  }
  return requestJson<{ ok?: boolean; apiConfig: ApiConfig; visionConfig?: VisionConfig }>(`${endpoint}/api/config`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function updateVisionConfig(
  endpoint: string,
  config: { provider: string; api_key: string; base_url?: string; model?: string; enabled: boolean },
) {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<{ ok?: boolean; apiConfig: ApiConfig; visionConfig: VisionConfig }>("update_vision_config", {
      request: { ...config, timeoutMs: 30000 },
    });
  }
  return requestJson<{ ok?: boolean; apiConfig: ApiConfig; visionConfig: VisionConfig }>(`${endpoint}/api/config/vision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export type ProviderModelList = {
  provider: string;
  providerLabel?: string;
  baseUrl?: string;
  models: ProviderModelInfo[];
  modelCount: number;
  selectedModel?: string;
};

export type ProviderTestResult = {
  ok: boolean;
  status: "ok" | "warning" | "error" | "skipped" | string;
  capability: "text" | "structured" | "vision" | string;
  provider: string;
  providerLabel?: string;
  model?: string;
  message: string;
  responsePreview?: string;
  skipped?: boolean;
};

export type ProviderReasoningVariants = {
  schema: "vrcforge.reasoning_variants.v1" | string;
  provider: string;
  model: string;
  transport: string;
  defaultKey: "default" | string;
  variants: Array<{
    key: string;
    level: string;
    displayKey: string;
    requestMode: string;
  }>;
};

export async function fetchProviderModels(
  endpoint: string,
  config: { provider: string; api_key?: string; base_url?: string; model?: string },
): Promise<ProviderModelList> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProviderModelList>("fetch_provider_models", {
      request: { ...config, timeoutMs: 30000 },
    });
  }
  return requestJson(`${endpoint}/api/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
}

export async function fetchReasoningVariants(
  endpoint: string,
  request: { provider: string; model: string },
): Promise<ProviderReasoningVariants> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProviderReasoningVariants>("fetch_reasoning_variants", {
      request: { ...request, timeoutMs: 10000 },
    });
  }
  return requestJson<ProviderReasoningVariants>(`${endpoint}/api/app/provider/reasoning-variants`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    timeoutMs: 10000,
  });
}

export async function testProviderCapability(
  endpoint: string,
  request: { provider: string; api_key?: string; base_url?: string; model?: string; thinking_level?: string; capability: "text" | "structured" | "vision" },
): Promise<ProviderTestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProviderTestResult>("test_provider_capability", {
      request: { ...request, timeoutMs: 30000 },
    });
  }
  return requestJson<ProviderTestResult>(`${endpoint}/api/app/provider/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
