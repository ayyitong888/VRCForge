import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type SkillPackageEntry = {
  id?: string;
  name?: string;
  author?: string;
  author_id?: string;
  authorId?: string;
  title?: string;
  version?: string;
  source?: string;
  enabled?: boolean;
  available?: boolean;
  signature_status?: string;
  signatureStatus?: string;
  signer_fingerprint?: string;
  signerFingerprint?: string;
  permissions?: string[];
  permission_tiers?: Record<string, string[]>;
  permissionTiers?: Record<string, string[]>;
  risk_level?: string;
  riskLevel?: string;
  installed_path?: string;
  installedPath?: string;
  package_path?: string;
  packagePath?: string;
  package_sha256?: string;
  packageSha256?: string;
  lock_sha256?: string;
  lockSha256?: string;
  update_action?: string;
  updateAction?: string;
  manifest?: Record<string, unknown>;
  governance?: Record<string, unknown>;
  dryRun?: Record<string, unknown>;
  warnings?: string[];
  errors?: string[];
  changed?: boolean;
};

export type SkillPackageList = {
  ok: boolean;
  store?: string;
  governance?: Record<string, unknown>;
  audit?: Array<Record<string, unknown>>;
  registry?: unknown;
  installed: SkillPackageEntry[];
};

export type SkillPackagePreflight = SkillPackageEntry & {
  ok?: boolean;
  preview?: SkillPackageEntry;
};

export type SkillPackageImportResult = {
  ok?: boolean;
  dryRun?: boolean;
  preview?: SkillPackageEntry;
  imported?: { registry_entry?: SkillPackageEntry; registryEntry?: SkillPackageEntry; [key: string]: unknown };
  projectedSkill?: { name?: string; path?: string; [key: string]: unknown } | null;
  installed?: SkillPackageEntry;
  changed?: boolean;
};

export type SkillPackageExportResult = {
  ok?: boolean;
  exported?: SkillPackageEntry;
};

export type PathToSkillCaptureRequest = {
  summary: Record<string, unknown>;
  packageId?: string;
  skillName?: string;
  title?: string;
  version?: string;
  author?: string;
  minVrcforgeVersion?: string;
  outputPath?: string;
  writeSource?: boolean;
  useTempOutput?: boolean;
  exportVsk?: boolean;
  confirmExport?: boolean;
  packageOutputPath?: string;
};

export type PathToSkillCaptureResult = {
  ok: boolean;
  schema: string;
  dryRun: boolean;
  manifest: Record<string, unknown>;
  workflow: Record<string, unknown>;
  skillMarkdown: string;
  sourceFiles: Record<string, string>;
  files: Array<{ path: string; bytes: number }>;
  writeSuppressed?: boolean;
  writtenSource?: { path: string; files: Array<{ path: string; bytes: number }> };
  exported?: SkillPackageEntry;
};

export type SkillPackageStateResult = {
  ok?: boolean;
  state?: { registry_entry?: SkillPackageEntry; registryEntry?: SkillPackageEntry; [key: string]: unknown };
  projectedSkill?: { name?: string; missing?: boolean; skipped?: boolean; [key: string]: unknown } | null;
};

export type SkillPackageUninstallResult = {
  ok?: boolean;
  uninstalled?: { skill_id?: string; skillId?: string; removed_versions?: string[]; removedVersions?: string[]; [key: string]: unknown };
  projectedSkill?: { name?: string; deleted?: string; missing?: boolean; skipped?: boolean; [key: string]: unknown } | null;
};

export type SkillPackageGovernanceActionResult = {
  ok?: boolean;
  safeMode?: Record<string, unknown>;
  signer?: Record<string, unknown>;
  blocklist?: Record<string, unknown>;
  projectedSkills?: Array<Record<string, unknown>>;
};

export async function fetchSkillPackages(endpoint: string): Promise<SkillPackageList> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageList>("fetch_skill_packages", {});
  }
  return requestJson<SkillPackageList>(`${endpoint}/api/app/skill-packages`);
}

export async function preflightSkillPackage(
  endpoint: string,
  request: { packagePath: string; allowDowngrade?: boolean; devMode?: boolean; projectToUserSkills?: boolean },
): Promise<SkillPackagePreflight> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackagePreflight>("preflight_skill_package", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackagePreflight>(`${endpoint}/api/app/skill-packages/preflight`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function importSkillPackage(
  endpoint: string,
  request: { packagePath: string; allowDowngrade?: boolean; devMode?: boolean; projectToUserSkills?: boolean; dryRun?: boolean },
): Promise<SkillPackageImportResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageImportResult>("import_skill_package", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackageImportResult>(`${endpoint}/api/app/skill-packages/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function setSkillPackageSafeMode(
  endpoint: string,
  request: { enabled: boolean; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("set_skill_package_safe_mode", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/safe-mode`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function trustSkillPackageSigner(
  endpoint: string,
  request: { signerFingerprint: string; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("trust_skill_package_signer", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/trust-signer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function revokeSkillPackageSigner(
  endpoint: string,
  request: { signerFingerprint: string; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("revoke_skill_package_signer", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/revoke-signer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function blockSkillPackage(
  endpoint: string,
  request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string },
): Promise<SkillPackageGovernanceActionResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageGovernanceActionResult>("block_skill_package", {
      request: { body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageGovernanceActionResult>(`${endpoint}/api/app/skill-packages/block-package`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function setSkillPackageEnabled(
  endpoint: string,
  skillPackageId: string,
  request: { enabled: boolean; syncProjectedSkill?: boolean },
): Promise<SkillPackageStateResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageStateResult>("set_skill_package_enabled", {
      request: { id: skillPackageId, body: request, timeoutMs: 60000 },
    });
  }
  return requestJson<SkillPackageStateResult>(`${endpoint}/api/app/skill-packages/${encodeURIComponent(skillPackageId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function uninstallSkillPackage(
  endpoint: string,
  skillPackageId: string,
  request: { removeProjectedSkill?: boolean } = {},
): Promise<SkillPackageUninstallResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageUninstallResult>("uninstall_skill_package", {
      request: { id: skillPackageId, body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackageUninstallResult>(`${endpoint}/api/app/skill-packages/${encodeURIComponent(skillPackageId)}`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function exportSkillPackage(
  endpoint: string,
  request: { skillName: string; outputPath: string; release?: boolean; privateKeyPath?: string; privateKeyPem?: string },
): Promise<SkillPackageExportResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<SkillPackageExportResult>("export_skill_package", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<SkillPackageExportResult>(`${endpoint}/api/app/skill-packages/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function previewPathToSkill(
  endpoint: string,
  request: PathToSkillCaptureRequest,
): Promise<PathToSkillCaptureResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<PathToSkillCaptureResult>("preview_path_to_skill", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<PathToSkillCaptureResult>(`${endpoint}/api/app/path-to-skill/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

export async function writePathToSkill(
  endpoint: string,
  request: PathToSkillCaptureRequest,
): Promise<PathToSkillCaptureResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<PathToSkillCaptureResult>("write_path_to_skill", {
      request: { body: request, timeoutMs: 120000 },
    });
  }
  return requestJson<PathToSkillCaptureResult>(`${endpoint}/api/app/path-to-skill/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
