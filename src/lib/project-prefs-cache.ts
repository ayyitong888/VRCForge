import type { ProjectPrefs } from "./api";

export const PROJECT_PREFS_CACHE_KEY = "vrcforge_project_prefs_cache";

const EMPTY_PROJECT_PREFS: ProjectPrefs = { customPaths: [], customProjects: [], hiddenPaths: [] };

export function loadProjectPrefsCache(): ProjectPrefs {
  try {
    const raw = window.localStorage.getItem(PROJECT_PREFS_CACHE_KEY);
    const parsed = raw ? JSON.parse(raw) as Partial<ProjectPrefs> : {};
    return {
      customPaths: Array.isArray(parsed.customPaths) ? parsed.customPaths.filter((value): value is string => typeof value === "string") : [],
      customProjects: Array.isArray(parsed.customProjects) ? parsed.customProjects.filter((value): value is ProjectPrefs["customProjects"][number] => (
        Boolean(value) && typeof value === "object" && typeof value.path === "string" && (value.projectType === "general" || value.projectType === "unity")
      )) : [],
      hiddenPaths: Array.isArray(parsed.hiddenPaths) ? parsed.hiddenPaths.filter((value): value is string => typeof value === "string") : [],
    };
  } catch {
    return EMPTY_PROJECT_PREFS;
  }
}

export function saveProjectPrefsCache(prefs: ProjectPrefs): void {
  try {
    window.localStorage.setItem(PROJECT_PREFS_CACHE_KEY, JSON.stringify(prefs));
  } catch {
    // A blocked or full preference store must not affect the authoritative save.
  }
}
