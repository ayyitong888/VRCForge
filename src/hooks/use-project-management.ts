import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  fetchProjectPrefs,
  planOutfitImport,
  requestOutfitImport,
  saveProjectPrefs,
  scanProjectIndex,
} from "../lib/api";
import type { AppBootstrap, OutfitImportPlanResult, ProjectIndexScanResult, ProjectPrefs } from "../lib/api";
import { isAbsoluteLocalPath, isTauriRuntime } from "../lib/app-runtime";
import {
  COLLAPSED_PROJECTS_KEY,
  PROJECT_UI_PREFS_KEY,
  loadProjectUiPrefs,
  type ProjectUiPrefs,
} from "../lib/app-preferences";
import { normalizeProjectPathKey, projectKey, shortPath } from "../lib/project-path";

const PROJECT_INDEX_BACKGROUND_DELAY_MS = 1200;

type ProjectEntry = NonNullable<NonNullable<AppBootstrap["health"]["projects"]>["projects"]>[number] & {
  activeMcp?: boolean;
};

type UseProjectManagementParams = {
  endpoint: string;
  runtimeConnected: boolean;
  activeProjectPath: string;
  projects: ProjectEntry[];
  refresh: (target?: string) => Promise<void>;
  refreshSilently: (target?: string) => Promise<void>;
  startRuntime: () => Promise<string | null>;
  setError: (message: string) => void;
  onProjectAdded: (projectPath: string) => void;
  onActiveProjectHidden: () => void;
};

export function useProjectManagement({
  endpoint,
  runtimeConnected,
  activeProjectPath,
  projects,
  refresh,
  refreshSilently,
  startRuntime,
  setError,
  onProjectAdded,
  onActiveProjectHidden,
}: UseProjectManagementParams) {
  const { t } = useTranslation();
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [newProjectPath, setNewProjectPath] = useState("");
  const [savingProjectPrefs, setSavingProjectPrefs] = useState(false);
  const [projectModalError, setProjectModalError] = useState("");
  const [projectPrefs, setProjectPrefs] = useState<ProjectPrefs>({ customPaths: [], hiddenPaths: [] });
  const [projectPrefsReady, setProjectPrefsReady] = useState(false);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [projectMenu, setProjectMenu] = useState<{ projectPath: string; x: number; y: number } | null>(null);
  const [projectUiPrefs, setProjectUiPrefs] = useState<ProjectUiPrefs>(() => loadProjectUiPrefs());
  const [renamingProjectPath, setRenamingProjectPath] = useState("");
  const [projectRenameDraft, setProjectRenameDraft] = useState("");
  const [projectIndex, setProjectIndex] = useState<ProjectIndexScanResult | null>(null);
  const [projectIndexProject, setProjectIndexProject] = useState("");
  const [loadingProjectIndex, setLoadingProjectIndex] = useState(false);
  const [projectIndexError, setProjectIndexError] = useState("");
  const [outfitPackagePath, setOutfitPackagePath] = useState("");
  const [outfitImportPlan, setOutfitImportPlan] = useState<OutfitImportPlanResult | null>(null);
  const [outfitImportStatus, setOutfitImportStatus] = useState("");
  const [loadingOutfitImportPlan, setLoadingOutfitImportPlan] = useState(false);
  const [requestingOutfitImport, setRequestingOutfitImport] = useState(false);
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>(() => {
    try {
      const raw = window.localStorage.getItem(COLLAPSED_PROJECTS_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return parsed && typeof parsed === "object" ? (parsed as Record<string, boolean>) : {};
    } catch {
      return {};
    }
  });

  const hiddenPathSet = useMemo(
    () => new Set(projectPrefs.hiddenPaths.map(normalizeProjectPathKey)),
    [projectPrefs.hiddenPaths],
  );
  const customPathSet = useMemo(
    () => new Set(projectPrefs.customPaths.map(normalizeProjectPathKey)),
    [projectPrefs.customPaths],
  );
  const pinnedProjectSet = useMemo(
    () => new Set(projectUiPrefs.pinnedPaths.map(normalizeProjectPathKey)),
    [projectUiPrefs.pinnedPaths],
  );
  const projectItems = useMemo(
    () =>
      projects
        .filter((project) => !hiddenPathSet.has(normalizeProjectPathKey(project.path || "")))
        .sort((a, b) => Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(b)))) - Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(a))))),
    [projects, hiddenPathSet, pinnedProjectSet],
  );
  const hiddenProjects = useMemo(
    () => projects.filter((project) => hiddenPathSet.has(normalizeProjectPathKey(project.path || ""))),
    [projects, hiddenPathSet],
  );

  useEffect(() => {
    try {
      window.localStorage.setItem(COLLAPSED_PROJECTS_KEY, JSON.stringify(collapsedProjects));
    } catch {
      // Best-effort local UI state.
    }
  }, [collapsedProjects]);

  useEffect(() => {
    try {
      window.localStorage.setItem(PROJECT_UI_PREFS_KEY, JSON.stringify(projectUiPrefs));
    } catch {
      // Project display preferences are best-effort local UI state.
    }
  }, [projectUiPrefs]);

  useEffect(() => {
    if (!runtimeConnected || projectPrefsReady) {
      return;
    }
    void fetchProjectPrefsOnce();
  }, [runtimeConnected, endpoint, projectPrefsReady]);

  useEffect(() => {
    if (!runtimeConnected || !activeProjectPath) {
      setProjectIndex(null);
      setProjectIndexProject("");
      setProjectIndexError("");
      return;
    }
    const timer = window.setTimeout(() => {
      void scanActiveProjectIndex(activeProjectPath, true);
    }, PROJECT_INDEX_BACKGROUND_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [runtimeConnected, endpoint, activeProjectPath]);

  async function fetchProjectPrefsOnce() {
    try {
      const prefs = await fetchProjectPrefs(endpoint);
      setProjectPrefs(prefs);
    } finally {
      setProjectPrefsReady(true);
    }
  }

  async function persistProjectPrefs(next: ProjectPrefs): Promise<ProjectPrefs | null> {
    setSavingProjectPrefs(true);
    try {
      const saved = await saveProjectPrefs(endpoint, next);
      setProjectPrefs(saved);
      await refreshSilently();
      return saved;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setSavingProjectPrefs(false);
    }
  }

  async function addProjectPath() {
    const path = newProjectPath.trim();
    if (!path || savingProjectPrefs) {
      return;
    }
    setProjectModalError("");
    const saved = await persistProjectPrefs({
      ...projectPrefs,
      customPaths: [...projectPrefs.customPaths, path],
    });
    if (!saved) {
      return;
    }
    const accepted = saved.customPaths.some((item) => item.replace(/\//g, "\\").toLowerCase() === path.replace(/\//g, "\\").toLowerCase());
    if (!accepted) {
      setProjectModalError(t("project.invalidProjectRoot"));
      return;
    }
    setNewProjectPath("");
    setShowProjectModal(false);
    try {
      await refresh();
    } catch {
      // The project list will refresh on the next poll.
    }
    onProjectAdded(path);
  }

  function removeCustomProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      customPaths: projectPrefs.customPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function hideProject(path: string) {
    if (!path) {
      return;
    }
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)), path],
    });
    if (normalizeProjectPathKey(activeProjectPath) === normalizeProjectPathKey(path)) {
      onActiveProjectHidden();
    }
  }

  function unhideProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function projectDisplayName(project?: { path?: string; name?: string }): string {
    if (!project) {
      return "";
    }
    const key = projectKey(project);
    return projectUiPrefs.aliases[normalizeProjectPathKey(key)] || project.name || project.path || "Unity Project";
  }

  function togglePinProject(path: string) {
    const key = normalizeProjectPathKey(path);
    if (!key) {
      return;
    }
    setProjectUiPrefs((current) => {
      const pinned = new Set(current.pinnedPaths.map(normalizeProjectPathKey));
      if (pinned.has(key)) {
        pinned.delete(key);
      } else {
        pinned.add(key);
      }
      return { ...current, pinnedPaths: Array.from(pinned) };
    });
  }

  function startRenameProject(path: string) {
    setRenamingProjectPath(path);
    const project = projectItems.find((item) => normalizeProjectPathKey(projectKey(item)) === normalizeProjectPathKey(path));
    setProjectRenameDraft(projectDisplayName(project) || shortPath(path));
  }

  function commitRenameProject(cancel = false) {
    if (!cancel && renamingProjectPath) {
      const key = normalizeProjectPathKey(renamingProjectPath);
      const title = projectRenameDraft.trim();
      setProjectUiPrefs((current) => {
        const aliases = { ...current.aliases };
        if (title) {
          aliases[key] = title;
        } else {
          delete aliases[key];
        }
        return { ...current, aliases };
      });
    }
    setRenamingProjectPath("");
    setProjectRenameDraft("");
  }

  function resolveOpenableProjectPath(path: string): string {
    const raw = path.trim();
    const normalized = normalizeProjectPathKey(raw);
    const candidates: string[] = [raw];
    const pushCandidate = (candidate?: string) => {
      const value = (candidate || "").trim();
      if (value && !candidates.some((item) => normalizeProjectPathKey(item) === normalizeProjectPathKey(value))) {
        candidates.push(value);
      }
    };
    const matchingProject = projectItems.find((project) => {
      const identifiers = [project.path, project.name, projectKey(project), projectDisplayName(project)];
      return identifiers.some((identifier) => normalizeProjectPathKey(identifier || "") === normalized);
    });
    pushCandidate(matchingProject?.path);
    if (normalized && normalized === normalizeProjectPathKey(activeProjectPath)) {
      pushCandidate(activeProjectPath);
    }
    if (!isAbsoluteLocalPath(raw)) {
      const activeMcpProjects = projectItems.filter((project) => Boolean((project as { activeMcp?: boolean }).activeMcp && project.path));
      if (activeMcpProjects.length === 1) {
        pushCandidate(activeMcpProjects[0].path);
      }
    }
    return candidates.find(isAbsoluteLocalPath) || raw;
  }

  async function openProjectFolder(path: string) {
    const targetPath = resolveOpenableProjectPath(path);
    if (!targetPath) {
      return;
    }
    if (!isAbsoluteLocalPath(targetPath)) {
      setError("Cannot open this project because it does not have an absolute Unity project path.");
      return;
    }
    try {
      if (!isTauriRuntime()) {
        throw new Error("Open folder is available in the desktop app.");
      }
      await invoke("open_folder", { path: targetPath });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function scanActiveProjectIndex(projectPath = activeProjectPath, silent = false) {
    if (!projectPath) {
      return;
    }
    setLoadingProjectIndex(true);
    if (!silent) {
      setProjectIndexError("");
    }
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await scanProjectIndex(targetEndpoint, { projectPath });
      if (normalizeProjectPathKey(projectPath) === normalizeProjectPathKey(activeProjectPath)) {
        setProjectIndex(payload);
        setProjectIndexProject(projectPath);
        setProjectIndexError(payload.ok ? "" : payload.error || "Project index scan failed.");
      }
    } catch (cause) {
      if (normalizeProjectPathKey(projectPath) === normalizeProjectPathKey(activeProjectPath)) {
        setProjectIndexError(cause instanceof Error ? cause.message : String(cause));
      }
    } finally {
      setLoadingProjectIndex(false);
    }
  }

  async function planActiveOutfitImport() {
    const packagePath = outfitPackagePath.trim();
    if (!packagePath) {
      setOutfitImportStatus("Package path is required.");
      return;
    }
    setLoadingOutfitImportPlan(true);
    setOutfitImportStatus("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setOutfitImportStatus("VRCForge runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await planOutfitImport(targetEndpoint, {
        packagePath,
        projectPath: activeProjectPath,
      });
      setOutfitImportPlan(payload);
      setOutfitImportStatus(payload.ok ? "Import plan ready." : payload.error || payload.plan?.error || "Import plan needs review.");
    } catch (cause) {
      setOutfitImportStatus(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoadingOutfitImportPlan(false);
    }
  }

  async function requestActiveOutfitImport() {
    const packagePath = outfitPackagePath.trim();
    if (!packagePath) {
      setOutfitImportStatus("Package path is required.");
      return;
    }
    setRequestingOutfitImport(true);
    setOutfitImportStatus("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setOutfitImportStatus("VRCForge runtime is not connected.");
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await requestOutfitImport(targetEndpoint, {
        packagePath,
        projectPath: activeProjectPath,
      });
      setOutfitImportStatus(payload.approval ? `Approval queued: ${payload.approval.id}` : "Approval queued.");
      await refresh(targetEndpoint);
    } catch (cause) {
      setOutfitImportStatus(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRequestingOutfitImport(false);
    }
  }

  function toggleProjectCollapse(key: string) {
    setCollapsedProjects((current) => ({ ...current, [key]: !current[key] }));
  }

  const expandProjectGroup = useCallback((key: string) => {
    setCollapsedProjects((current) => (current[key] ? { ...current, [key]: false } : current));
  }, []);

  return {
    showProjectModal,
    setShowProjectModal,
    newProjectPath,
    setNewProjectPath,
    savingProjectPrefs,
    projectModalError,
    setProjectModalError,
    projectPrefs,
    projectPrefsReady,
    loadingProjects,
    setLoadingProjects,
    projectMenu,
    setProjectMenu,
    renamingProjectPath,
    projectRenameDraft,
    setProjectRenameDraft,
    projectIndex,
    projectIndexProject,
    loadingProjectIndex,
    projectIndexError,
    outfitPackagePath,
    setOutfitPackagePath,
    outfitImportPlan,
    outfitImportStatus,
    loadingOutfitImportPlan,
    requestingOutfitImport,
    collapsedProjects,
    customPathSet,
    pinnedProjectSet,
    projectItems,
    hiddenProjects,
    addProjectPath,
    removeCustomProject,
    hideProject,
    unhideProject,
    projectDisplayName,
    togglePinProject,
    startRenameProject,
    commitRenameProject,
    openProjectFolder,
    scanActiveProjectIndex,
    planActiveOutfitImport,
    requestActiveOutfitImport,
    toggleProjectCollapse,
    expandProjectGroup,
  };
}
