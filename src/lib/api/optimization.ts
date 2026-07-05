import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";
import type { AgentApproval } from "./types";

export type OptimizationTargetProfile = {
  id?: string;
  label?: string;
  platform?: string;
  riskTolerance?: string;
  weights?: Record<string, number>;
};

export type OptimizationDependencyCard = {
  id?: string;
  label?: string;
  status?: "installed" | "missing" | "unknown" | string;
  installed?: boolean;
  packageIds?: string[];
  version?: string | null;
  matchedPackageId?: string;
  recommendedRole?: string;
  riskLevel?: string;
  docsLink?: string;
  installMethod?: { kind?: string; repository?: string; automatic?: boolean; supervisedRequestSupported?: boolean };
};

export type OptimizationActionCard = {
  id: string;
  title: string;
  description?: string;
  riskLevel?: string;
  dependency?: string;
  recommendedVersionStage?: string;
  level?: string;
  enabled?: boolean;
  blockedReason?: string | null;
  expectedBenefit?: string;
  whyRecommended?: string;
  nextSafeAction?: string;
  requestTool?: string;
  requestOnly?: boolean;
  affectedAssetsOrRenderers?: unknown[];
  directApplyExposed?: boolean;
};

export type OptimizationPlannerReport = {
  ok: boolean;
  schema: string;
  versionStage?: string;
  generatedAt?: string;
  readOnly?: boolean;
  planOnly?: boolean;
  noProjectWrites?: boolean;
  directApplyExposed?: boolean;
  targetProfile?: OptimizationTargetProfile;
  baseline?: {
    performanceHeadline?: Record<string, { rank?: string; triangleCount?: number; materialSlots?: number; textureMemoryBytes?: number }>;
    metrics?: Record<string, number | null | undefined>;
    validationSummary?: Record<string, unknown>;
  };
  dependencyDoctor?: {
    dependencies?: OptimizationDependencyCard[];
    summary?: Record<string, number>;
    installPolicy?: Record<string, unknown>;
  };
  audits?: Record<string, unknown>;
  plans?: Record<string, unknown>;
  topOffenders?: Array<{ id?: string; label?: string; severity?: string; count?: number }>;
  actionCards?: OptimizationActionCard[];
  recommendedOrder?: string[];
  nextSafeAction?: OptimizationActionCard | null;
  tools?: Array<{ externalName?: string; gatewayName?: string; level?: string; directApplyExposed?: boolean }>;
  futureWriteRequestTools?: Array<{ externalName?: string; versionStage?: string; directApplyExposed?: boolean }>;
  rules?: Record<string, unknown>;
};

export type AvatarListItem = {
  avatarName?: string;
  avatarPath?: string;
  sceneName?: string;
  rendererCount?: number;
  blendshapeCount?: number;
  isVrChatAvatar?: boolean;
};

export type AvatarListResult = {
  ok: boolean;
  executed?: boolean;
  exportSource?: string;
  executionMode?: string;
  summary?: Record<string, unknown>;
  avatars?: AvatarListItem[];
  avatarCount?: number;
};

export type AvatarEncryptionProfileCard = {
  id: "lite" | "standard" | "paranoid" | string;
  icon?: string;
  title?: string;
  label?: string;
  description?: string;
  recommended?: boolean;
  cost?: string;
  deviceFit?: string;
  protection?: string;
  applyStatus?: string;
};

export type AvatarEncryptionBenchmarkRow = {
  profile?: string;
  label?: string;
  triangles?: number;
  avatarScale?: string;
  baselineFps?: number;
  estimatedFps?: number;
  estimatedFpsLoss?: number;
  estimatedFrameTimeAddedMs?: number;
  estimatedImpactPercent?: number;
  gpuCost?: string;
};

export type AvatarEncryptionPlanResult = {
  ok: boolean;
  schema?: string;
  scan?: {
    summary?: Record<string, unknown>;
    targets?: Array<Record<string, unknown>>;
  };
  plan?: {
    status?: string;
    writeStatus?: string;
    writeBlockReason?: string;
    avatarPath?: string;
    selectedCandidateCount?: number;
    selectedCandidates?: Array<Record<string, unknown>>;
    targetShaderFamilies?: string[];
    profile?: AvatarEncryptionProfileCard & Record<string, unknown>;
    recommendedProfile?: string;
    profileCards?: AvatarEncryptionProfileCard[];
    benchmarkTable?: AvatarEncryptionBenchmarkRow[];
    benchmarkAssumptions?: Record<string, unknown>;
    hardGate?: { status?: string; blockingIds?: string[]; warnings?: string[] };
    futureRequestTools?: Record<string, unknown>;
    externalAddon?: Record<string, unknown>;
    platform?: Record<string, unknown>;
    layers?: Array<Record<string, unknown>>;
  };
  error?: string;
};

export type PackageInstallRequestResult = {
  ok: boolean;
  status?: string;
  approval?: AgentApproval;
  error?: string;
  installPlan?: Record<string, unknown>;
};

export type OptimizationApplyRequestResult = {
  ok: boolean;
  status?: string;
  approval?: AgentApproval;
  error?: string;
  preview?: Record<string, unknown>;
  installPlan?: Record<string, unknown>;
};

export type AvatarEncryptionApplyRequestResult = OptimizationApplyRequestResult;

export type OutfitDependencyPreflight = {
  schema?: string;
  readyForImport?: boolean;
  blockingMissingCount?: number;
  blockingIssueCount?: number;
  detectedCount?: number;
  packageOrder?: {
    importQueue?: Array<{
      order?: number;
      path?: string;
      sourceType?: string;
      role?: string;
      reason?: string;
      actualPackagePath?: string;
      containerPath?: string;
      selected?: boolean;
    }>;
    skippedInstalledSupportPackages?: Array<{
      order?: number;
      path?: string;
      sourceType?: string;
      role?: string;
      reason?: string;
      actualPackagePath?: string;
      containerPath?: string;
      selected?: boolean;
      skipReason?: string;
      dependencyId?: string;
      dependencyLabel?: string;
      message?: string;
    }>;
    skippedInstalledSupportCount?: number;
    importCount?: number;
    supportPackageCount?: number;
    requiresManualExtract?: boolean;
    blockingBeforeImport?: boolean;
    warnings?: string[];
  };
  compatibility?: {
    status?: string;
    baseAvatarName?: string;
    detectedAvatarNames?: string[];
    blockingBeforeImport?: boolean;
    message?: string;
    evidence?: Record<string, string[]>;
    warnings?: string[];
  };
  entries?: Array<{
    id?: string;
    label?: string;
    kind?: string;
    status?: string;
    message?: string;
    blockingBeforeImport?: boolean;
    stage?: string;
    packageIds?: string[];
    evidence?: {
      packagePathnames?: string[];
      hints?: string[];
      project?: string[];
    };
  }>;
  warnings?: string[];
  recommendedOrder?: string[];
};

export type OutfitImportPlanResult = {
  ok: boolean;
  schema?: string;
  preview?: boolean;
  plannedAt?: string;
  error?: string;
  dependencyPreflight?: OutfitDependencyPreflight;
  inspection?: {
    ok?: boolean;
    summary?: {
      unityPackageCount?: number;
      prefabCandidateCount?: number;
      textureCount?: number;
      materialCount?: number;
      modelCount?: number;
      unsafeEntryCount?: number;
      duplicateEntryCount?: number;
      importPlanKind?: string;
    };
    unityPackages?: Array<{ path?: string; size?: number; pathnameCount?: number }>;
    prefabCandidates?: Array<{ path?: string; source?: string }>;
    textures?: Array<{ path?: string; source?: string }>;
    materials?: Array<{ path?: string; source?: string }>;
    models?: Array<{ path?: string; source?: string }>;
    warnings?: string[];
  };
  plan?: {
    id?: string;
    kind?: string;
    ok?: boolean;
    readyToApply?: boolean;
    requiresApproval?: boolean;
    requiresCheckpoint?: boolean;
    validationAfterApply?: boolean;
    rollbackProofRequired?: boolean;
    projectPath?: string;
    targetFolder?: string;
    source?: {
      type?: string;
      path?: string;
      selectedUnityPackage?: string;
      actualPackagePath?: string;
      importQueue?: Array<{
        order?: number;
        path?: string;
        sourceType?: string;
        role?: string;
        reason?: string;
        actualPackagePath?: string;
        containerPath?: string;
        selected?: boolean;
      }>;
    };
    selectedPrefab?: string;
    expectedAssetPaths?: string[];
    dependencyPreflight?: OutfitDependencyPreflight;
    writeTarget?: string;
    steps?: Array<{ id?: string; category?: string; tool?: string; description?: string; enabled?: boolean }>;
    warnings?: string[];
    error?: string;
  };
  warnings?: string[];
  privacy?: Record<string, unknown>;
};

export async function fetchOptimizationPlan(
  endpoint: string,
  request: {
    projectPath?: string;
    avatarPath?: string;
    targetProfile?: string;
    customProfile?: Record<string, unknown>;
    includeQuest?: boolean;
    maxErrors?: number;
  },
): Promise<OptimizationPlannerReport> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationPlannerReport>("fetch_optimization_plan", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<OptimizationPlannerReport>(`${endpoint}/api/app/optimization/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export type OptimizationProofSummary = {
  runId: string;
  schema?: string;
  ok?: boolean;
  status?: string;
  tool?: string;
  checkpointId?: string;
  rollbackDone?: boolean;
  changedFileCount?: number;
  failedSteps?: string[];
  startedAt?: string;
  finishedAt?: string;
  modifiedAt?: string;
  visualRegression?: Record<string, unknown>;
  rollbackProof?: Record<string, unknown>;
  profileDiff?: Record<string, unknown>;
  profileDiffUnavailable?: boolean;
  parameterBudgetDelta?: Record<string, unknown>;
  reportPath?: string;
  error?: string;
};

export type OptimizationProofList = {
  ok: boolean;
  schema: string;
  readOnly: boolean;
  artifactRoot?: string;
  count: number;
  proofs: OptimizationProofSummary[];
};

export type OptimizationProofDetail = {
  ok: boolean;
  schema: string;
  readOnly: boolean;
  proof: OptimizationProofSummary;
  report: Record<string, unknown>;
};

export async function fetchOptimizationProofs(endpoint: string, limit = 8): Promise<OptimizationProofList> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationProofList>("fetch_optimization_proofs", {
      request: { limit, timeoutMs: 30000 },
    });
  }
  return requestJson<OptimizationProofList>(`${endpoint}/api/app/optimization/proofs?limit=${encodeURIComponent(String(limit))}`);
}

export async function fetchOptimizationProof(endpoint: string, runId: string): Promise<OptimizationProofDetail> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationProofDetail>("fetch_optimization_proof", {
      request: { id: runId, body: {}, timeoutMs: 30000 },
    });
  }
  return requestJson<OptimizationProofDetail>(`${endpoint}/api/app/optimization/proofs/${encodeURIComponent(runId)}`);
}

export async function fetchAvatars(
  endpoint: string,
  request: { projectPath?: string } = {},
): Promise<AvatarListResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AvatarListResult>("fetch_avatars", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<AvatarListResult>(`${endpoint}/api/app/avatars`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function planAvatarEncryption(
  endpoint: string,
  request: {
    projectPath?: string;
    avatarPath?: string;
    profile?: string;
    protectionProfile?: string;
    platform?: string;
    targetShaderFamilies?: string[];
    materialIds?: string[];
    rendererPaths?: string[];
    targets?: Array<Record<string, unknown>>;
    confirmCreatorOwnedAssets?: boolean;
  },
): Promise<AvatarEncryptionPlanResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AvatarEncryptionPlanResult>("plan_avatar_encryption", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<AvatarEncryptionPlanResult>(`${endpoint}/api/avatar-encryption/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestAvatarEncryptionApply(
  endpoint: string,
  request: {
    projectPath?: string;
    avatarPath?: string;
    profile?: string;
    protectionProfile?: string;
    targetShaderFamily: string;
    targetShaderFamilies?: string[];
    materialIds?: string[];
    rendererPaths?: string[];
    targets?: Array<Record<string, unknown>>;
    outputFolder?: string;
    confirmCreatorOwnedAssets: boolean;
    saveAssets?: boolean;
  },
): Promise<AvatarEncryptionApplyRequestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<AvatarEncryptionApplyRequestResult>("request_avatar_encryption_apply", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<AvatarEncryptionApplyRequestResult>(`${endpoint}/api/avatar-encryption/apply-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestPackageInstall(
  endpoint: string,
  request: {
    projectPath?: string;
    packageId: string;
    repository?: string;
    preferredManager?: string;
    allowAgentManagedDownload?: boolean;
    includePrerelease?: boolean;
  },
): Promise<PackageInstallRequestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<PackageInstallRequestResult>("request_package_install", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<PackageInstallRequestResult>(`${endpoint}/api/app/package-install/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestOptimizationApply(
  endpoint: string,
  request: {
    tool: string;
    projectPath?: string;
    avatarPath?: string;
    targetProfile?: string;
    profile?: string;
    options?: Record<string, unknown>;
    installMissingDependencies?: boolean;
    allowExperimental?: boolean;
    includePrerelease?: boolean;
  },
): Promise<OptimizationApplyRequestResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OptimizationApplyRequestResult>("request_optimization_apply", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<OptimizationApplyRequestResult>(`${endpoint}/api/app/optimization/apply-request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function planOutfitImport(
  endpoint: string,
  request: {
    packagePath: string;
    projectPath?: string;
    targetFolder?: string;
    selectedUnityPackage?: string;
    selectedPrefab?: string;
    baseAvatarName?: string;
    maxEntries?: number;
  },
): Promise<OutfitImportPlanResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<OutfitImportPlanResult>("plan_outfit_import", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<OutfitImportPlanResult>(`${endpoint}/api/app/outfit-imports/plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function requestOutfitImport(
  endpoint: string,
  request: {
    packagePath: string;
    projectPath?: string;
    targetFolder?: string;
    selectedUnityPackage?: string;
    selectedPrefab?: string;
    baseAvatarName?: string;
    maxEntries?: number;
  },
): Promise<{ ok: boolean; approval?: AgentApproval; error?: string }> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort("request_outfit_import", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson(`${endpoint}/api/app/outfit-imports/request`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
