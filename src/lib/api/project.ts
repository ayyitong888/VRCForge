import { hasTauriInternals, invokeTauriWithAbort, requestJson } from "./http";

export type ProjectPrefs = {
  customPaths: string[];
  hiddenPaths: string[];
};

export type ProjectIndexPathEntry = {
  path: string;
  category?: string;
  size?: number;
  sha256?: string;
};

export type ProjectIndexScanResult = {
  ok: boolean;
  schema: string;
  projectId?: string;
  projectName?: string;
  indexPath?: string;
  error?: string;
  summary?: {
    firstScan?: boolean;
    totalFiles?: number;
    unchangedFiles?: number;
    addedFiles?: number;
    modifiedFiles?: number;
    deletedFiles?: number;
    guidChangeCount?: number;
    hashesComputed?: number;
    hashesReused?: number;
    truncated?: boolean;
    changed?: boolean;
    scannerFamilies?: string[];
  };
  changes?: {
    added?: ProjectIndexPathEntry[];
    modified?: ProjectIndexPathEntry[];
    deleted?: ProjectIndexPathEntry[];
    guidChanges?: Array<{ path?: string; oldGuid?: string; newGuid?: string }>;
  };
  packageFingerprints?: Record<string, unknown>;
  metaGuidCount?: number;
  staleDataPolicy?: string;
  privacy?: Record<string, unknown>;
};

export async function fetchProjectPrefs(endpoint: string): Promise<ProjectPrefs> {
  if (hasTauriInternals()) {
    const payload = await invokeTauriWithAbort<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>("fetch_project_prefs", {
      request: { timeoutMs: 30000 },
    });
    return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
  }
  const payload = await requestJson<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>(
    `${endpoint}/api/app/projects/prefs`,
  );
  return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
}

export async function saveProjectPrefs(endpoint: string, prefs: ProjectPrefs): Promise<ProjectPrefs> {
  if (hasTauriInternals()) {
    const payload = await invokeTauriWithAbort<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>("save_project_prefs", {
      request: { customPaths: prefs.customPaths, hiddenPaths: prefs.hiddenPaths, timeoutMs: 30000 },
    });
    return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
  }
  const payload = await requestJson<{ ok: boolean; customPaths?: string[]; hiddenPaths?: string[] }>(
    `${endpoint}/api/app/projects/prefs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ customPaths: prefs.customPaths, hiddenPaths: prefs.hiddenPaths }),
    },
  );
  return { customPaths: payload.customPaths || [], hiddenPaths: payload.hiddenPaths || [] };
}

export async function scanProjectIndex(
  endpoint: string,
  request: { projectPath: string; maxFiles?: number },
): Promise<ProjectIndexScanResult> {
  if (hasTauriInternals()) {
    return invokeTauriWithAbort<ProjectIndexScanResult>("scan_project_index", {
      request: { ...request, timeoutMs: 120000 },
    });
  }
  return requestJson<ProjectIndexScanResult>(`${endpoint}/api/app/project-index/scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}
