import { invoke } from "@tauri-apps/api/core";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createUnityProject,
  fetchProjectCreateOptions,
  fetchProjectPrefs,
  planOutfitImport,
  recycleUnityProject,
  requestOutfitImport,
  saveProjectPrefs,
} from "../lib/api";
import type { AppBootstrap, OutfitImportPlanResult, ProjectPrefs, ProjectRecycleResult, UnityProjectCreateRequest, UnityProjectTemplate } from "../lib/api";
import type { ProjectType } from "../lib/chat-types";
import { isAbsoluteLocalPath, isTauriRuntime } from "../lib/app-runtime";
import {
  COLLAPSED_PROJECTS_KEY,
  PROJECT_UI_PREFS_KEY,
  loadProjectUiPrefs,
  type ProjectUiPrefs,
} from "../lib/app-preferences";
import { normalizeProjectPathKey, projectKey, shortPath } from "../lib/project-path";
import { loadProjectPrefsCache, saveProjectPrefsCache } from "../lib/project-prefs-cache";

type ProjectEntry = NonNullable<NonNullable<AppBootstrap["health"]["projects"]>["projects"]>[number] & {
  activeMcp?: boolean;
};

type UseProjectManagementParams = {
  endpoint: string;
  runtimeConnected: boolean;
  activeProjectPath: string;
  projects: ProjectEntry[];
  projectScanStartedAt?: string;
  refresh: (target?: string) => Promise<void>;
  refreshProjects?: (target?: string) => Promise<void>;
  startRuntime: () => Promise<string | null>;
  setError: (message: string) => void;
  onProjectAdded: (projectPath: string, projectType?: ProjectType) => void;
  onActiveProjectHidden: () => void;
};

export function useProjectManagement({
  endpoint,
  runtimeConnected,
  activeProjectPath,
  projects,
  projectScanStartedAt,
  refresh,
  refreshProjects,
  startRuntime,
  setError,
  onProjectAdded,
  onActiveProjectHidden,
}: UseProjectManagementParams) {
  const { t } = useTranslation();
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [newProjectPath, setNewProjectPath] = useState("");
  const [pickingProjectFolder, setPickingProjectFolder] = useState(false);
  const [newProjectType, setNewProjectType] = useState<ProjectType>("unity");
  const [savingProjectPrefs, setSavingProjectPrefs] = useState(false);
  const [projectModalError, setProjectModalError] = useState("");
  const [creationOptions, setCreationOptions] = useState<{ templates: UnityProjectTemplate[]; loading: boolean; error: string }>({
    templates: [], loading: false, error: "",
  });
  const [projectPrefs, setProjectPrefs] = useState<ProjectPrefs>(() => loadProjectPrefsCache());
  const [projectPrefsReady, setProjectPrefsReady] = useState(false);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [projectListError, setProjectListError] = useState("");
  const [projectMenu, setProjectMenu] = useState<{ projectPath: string; x: number; y: number } | null>(null);
  const [projectRemovedAt, setProjectRemovedAt] = useState<Map<string, number>>(() => new Map());
  const [projectUiPrefs, setProjectUiPrefs] = useState<ProjectUiPrefs>(() => loadProjectUiPrefs());
  const [renamingProjectPath, setRenamingProjectPath] = useState("");
  const [projectRenameDraft, setProjectRenameDraft] = useState("");
  const [editingProjectPath, setEditingProjectPath] = useState("");
  const [editProjectPathDraft, setEditProjectPathDraft] = useState("");
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
  const projectOrder = useMemo(() => projectUiPrefs.projectOrder || [], [projectUiPrefs.projectOrder]);
  const allProjectItems = useMemo(() => {
    const scanStartedAt = Date.parse(projectScanStartedAt ?? "");
    const byPath = new Map(
      projects
        .filter((project) => project.projectType !== "general")
        .map((project) => [normalizeProjectPathKey(project.path || ""), project]),
    );
    for (const [key, removedAt] of projectRemovedAt) {
      // Only a completed scan that began after removal can rediscover this path.
      if (!(scanStartedAt > removedAt)) byPath.delete(key);
    }
    for (const customProject of projectPrefs.customProjects) {
      const key = normalizeProjectPathKey(customProject.path);
      if (!key || byPath.has(key) || projectRemovedAt.has(key)) {
        continue;
      }
      byPath.set(key, {
        name: shortPath(customProject.path),
        path: customProject.path,
        projectType: customProject.projectType,
        sources: ["custom"],
      });
    }
    return [...byPath.values()];
  }, [projectPrefs.customProjects, projects, projectRemovedAt, projectScanStartedAt]);
  useEffect(() => {
    if (!projectRemovedAt.size || !projectScanStartedAt) return;
    const rediscoveredKeys = allProjectItems
      .map((project) => normalizeProjectPathKey(project.path || ""))
      .filter((key) => projectRemovedAt.has(key));
    if (!rediscoveredKeys.length) return;

    setProjectRemovedAt((current) => {
      const next = new Map(current);
      for (const key of rediscoveredKeys) {
        if (current.get(key) === projectRemovedAt.get(key)) next.delete(key);
      }
      return next;
    });
  }, [allProjectItems, projectRemovedAt, projectScanStartedAt]);
  const projectItems = useMemo(
    () =>
      allProjectItems
        .filter((project) => !hiddenPathSet.has(normalizeProjectPathKey(project.path || "")))
        .map((project) => ({ ...project, lastOpenedAt: projectUiPrefs.lastOpenedAt?.[normalizeProjectPathKey(projectKey(project))] }))
        .sort((a, b) => Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(b)))) - Number(pinnedProjectSet.has(normalizeProjectPathKey(projectKey(a))))),
    [allProjectItems, hiddenPathSet, pinnedProjectSet, projectUiPrefs.lastOpenedAt],
  );
  const hiddenProjects = useMemo(
    () => allProjectItems.filter((project) => hiddenPathSet.has(normalizeProjectPathKey(project.path || ""))),
    [allProjectItems, hiddenPathSet],
  );

  const recordProjectOpen = useCallback((projectPath: string) => {
    const key = normalizeProjectPathKey(projectPath);
    if (!key) return;
    const openedAt = new Date().toISOString();
    setProjectUiPrefs((current) => ({
      ...current,
      lastOpenedAt: { ...current.lastOpenedAt, [key]: openedAt },
    }));
  }, []);

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

  async function fetchProjectPrefsOnce() {
    try {
      const prefs = await fetchProjectPrefs(endpoint);
      setProjectPrefs(prefs);
      saveProjectPrefsCache(prefs);
    } finally {
      setProjectPrefsReady(true);
    }
  }

  async function persistProjectPrefs(next: ProjectPrefs): Promise<ProjectPrefs | null> {
    setSavingProjectPrefs(true);
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          setProjectModalError(t("project.runtimeRequired"));
          return null;
        }
        targetEndpoint = readyEndpoint;
      }
      const saved = await saveProjectPrefs(targetEndpoint, next);
      setProjectPrefs(saved);
      saveProjectPrefsCache(saved);
      return saved;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setProjectModalError(message);
      return null;
    } finally {
      setSavingProjectPrefs(false);
    }
  }

  function startEditProject(path: string) {
    setEditingProjectPath(path);
    setEditProjectPathDraft(path);
    setProjectModalError("");
  }

  function cancelEditProject() {
    setEditingProjectPath("");
    setEditProjectPathDraft("");
  }

  async function saveEditProject() {
    const previous = editingProjectPath.trim();
    const nextPath = editProjectPathDraft.trim();
    if (!previous || !nextPath || savingProjectPrefs) return;
    if (normalizeProjectPathKey(previous) === normalizeProjectPathKey(nextPath)) {
      cancelEditProject();
      return;
    }
    const existing = projectPrefs.customProjects.find((item) => normalizeProjectPathKey(item.path) === normalizeProjectPathKey(previous)) || projectItems.find((item) => normalizeProjectPathKey(item.path || "") === normalizeProjectPathKey(previous));
    const saved = await persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(nextPath)), previous],
      customPaths: [...projectPrefs.customPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(previous)), nextPath],
      customProjects: [
        ...projectPrefs.customProjects.filter((item) => normalizeProjectPathKey(item.path) !== normalizeProjectPathKey(previous) && normalizeProjectPathKey(item.path) !== normalizeProjectPathKey(nextPath)),
        { path: nextPath, projectType: existing?.projectType || "unity" },
      ],
    });
    if (!saved) return;
    if (!saved.customProjects.some((item) => normalizeProjectPathKey(item.path) === normalizeProjectPathKey(nextPath))) {
      setProjectModalError(t("project.invalidProjectRoot"));
      return;
    }
    cancelEditProject();
    try { if (refreshProjects) await refreshProjects(); }
    catch (cause) { setProjectListError(cause instanceof Error ? cause.message : String(cause)); }
    if (normalizeProjectPathKey(activeProjectPath) === normalizeProjectPathKey(previous)) {
      onProjectAdded(nextPath, existing?.projectType || "unity");
    }
  }

  async function addProjectPath(value = newProjectPath) {
    const path = value.trim();
    if (!path || savingProjectPrefs) {
      return;
    }
    setProjectModalError("");
    const saved = await persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
      customPaths: [...projectPrefs.customPaths, path],
      customProjects: [...projectPrefs.customProjects.filter((item) => normalizeProjectPathKey(item.path) !== normalizeProjectPathKey(path)), { path, projectType: newProjectType }],
    });
    if (!saved) {
      return;
    }
    const accepted = saved.customPaths.some((item) => item.replace(/\//g, "\\").toLowerCase() === path.replace(/\//g, "\\").toLowerCase());
    if (!accepted) {
      setProjectModalError(t(newProjectType === "general" ? "project.invalidGeneralProjectRoot" : "project.invalidProjectRoot"));
      return;
    }
    setProjectRemovedAt((current) => {
      const next = new Map(current);
      next.delete(normalizeProjectPathKey(path));
      return next;
    });
    setNewProjectPath("");
    setShowProjectModal(false);
    if (newProjectType === "unity") {
      try {
        await refresh();
      } catch {
        // The cached Unity list stays usable until the next background refresh.
      }
    }
    onProjectAdded(path, newProjectType);
  }

  async function browseProjectFolder() {
    if (pickingProjectFolder || savingProjectPrefs) return;
    setPickingProjectFolder(true);
    setProjectModalError("");
    try {
      const selected = await invoke<string | null>("select_folder", { initialPath: newProjectPath || undefined });
      if (selected) await addProjectPath(selected);
    } catch (cause) {
      setProjectModalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPickingProjectFolder(false);
    }
  }

  async function loadCreationOptions() {
    setCreationOptions({ templates: [], loading: true, error: "" });
    try {
      if (!runtimeConnected && !await startRuntime()) throw new Error(t("project.runtimeRequired"));
      const templates = await fetchProjectCreateOptions();
      setCreationOptions({ templates, loading: false, error: "" });
    } catch (cause) {
      setCreationOptions({ templates: [], loading: false, error: cause instanceof Error ? cause.message : String(cause) });
    }
  }

  async function createProjectFromTemplate(request: UnityProjectCreateRequest) {
    const result = await createUnityProject(request);
    if (!result.ok || !result.projectPath) throw new Error(t("project.creation.unconfirmed"));
    const path = result.projectPath;
    const key = normalizeProjectPathKey(path);
    setProjectRemovedAt((current) => {
      if (!current.has(key)) return current;
      const next = new Map(current);
      next.delete(key);
      return next;
    });
    setProjectPrefs((current) => ({
      ...current,
      customPaths: [...current.customPaths.filter((item) => normalizeProjectPathKey(item) !== key), path],
      customProjects: [...current.customProjects.filter((item) => normalizeProjectPathKey(item.path) !== key), { path, projectType: "unity" }],
    }));
    return result;
  }

  function removeCustomProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      customPaths: projectPrefs.customPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
      customProjects: projectPrefs.customProjects.filter((item) => normalizeProjectPathKey(item.path) !== normalizeProjectPathKey(path)),
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)), path],
    }).then((saved) => {
      if (saved && normalizeProjectPathKey(activeProjectPath) === normalizeProjectPathKey(path)) onActiveProjectHidden();
    });
  }

  function hideProject(path: string) {
    if (!path) return;
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: [...projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)), path],
    }).then((saved) => {
      if (saved && normalizeProjectPathKey(activeProjectPath) === normalizeProjectPathKey(path)) onActiveProjectHidden();
    });
  }

  function unhideProject(path: string) {
    void persistProjectPrefs({
      ...projectPrefs,
      hiddenPaths: projectPrefs.hiddenPaths.filter((item) => normalizeProjectPathKey(item) !== normalizeProjectPathKey(path)),
    });
  }

  function canRecycleProject(path: string) {
    const key = normalizeProjectPathKey(path);
    return projectItems.some((project) =>
      project.projectType !== "general" && Boolean(project.path) &&
      normalizeProjectPathKey(project.path) === key && isAbsoluteLocalPath(project.path || ""),
    );
  }

  async function recycleProject(path: string, confirmation: string): Promise<ProjectRecycleResult> {
    if (!canRecycleProject(path)) throw new Error(t("project.invalidProjectRoot"));
    const target = runtimeConnected ? endpoint : await startRuntime();
    if (!target) throw new Error(t("project.runtimeRequired"));
    const result = await recycleUnityProject(path, confirmation);
    const keys = new Set([path, result.path].map(normalizeProjectPathKey));
    const keepPath = (value: string) => !keys.has(normalizeProjectPathKey(value));
    const withoutRecycledProject = (prefs: ProjectPrefs): ProjectPrefs => ({
      customPaths: prefs.customPaths.filter(keepPath),
      customProjects: prefs.customProjects.filter((entry) => keepPath(entry.path)),
      hiddenPaths: prefs.hiddenPaths.filter(keepPath),
    });
    const removedAt = Date.now();
    setProjectRemovedAt((current) => {
      const next = new Map(current);
      for (const key of keys) next.set(key, removedAt);
      return next;
    });
    setProjectPrefs(withoutRecycledProject);
    saveProjectPrefsCache(withoutRecycledProject(projectPrefs));
    return result;
  }

  function projectDisplayName(project?: { path?: string; name?: string }): string {
    if (!project) {
      return "";
    }
    const key = projectKey(project);
    return projectUiPrefs.aliases[normalizeProjectPathKey(key)] || project.name || project.path || "Unity Project";
  }

  function projectDisplayDetail(project?: { path?: string; name?: string }): string {
    const base = projectDisplayName(project);
    if (!project?.path || !base) {
      return "";
    }
    const duplicatePaths = projectItems
      .filter((candidate) => projectDisplayName(candidate) === base && candidate.path)
      .map((candidate) => candidate.path as string);
    if (duplicatePaths.length < 2) {
      return "";
    }
    const normalizedParts = project.path.replace(/\\/g, "/").split("/").filter(Boolean);
    for (let depth = 1; depth <= normalizedParts.length; depth += 1) {
      const suffix = normalizedParts.slice(-depth).join("/");
      const matches = duplicatePaths.filter((candidate) => candidate.replace(/\\/g, "/").split("/").filter(Boolean).slice(-depth).join("/") === suffix);
      if (matches.length === 1) {
        return suffix;
      }
    }
    return project.path;
  }

  function projectDisplayLabel(project?: { path?: string; name?: string }): string {
    const base = projectDisplayName(project);
    const detail = projectDisplayDetail(project);
    return detail ? `${base} (${detail})` : base;
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

  function reorderProjects(orderedPaths: string[]) {
    const available = new Set(projectItems.map((project) => normalizeProjectPathKey(projectKey(project))));
    const ordered = Array.from(new Set(orderedPaths.map(normalizeProjectPathKey))).filter((key) => available.has(key));
    if (ordered.length < 2) return;
    const moved = new Set(ordered);
    setProjectUiPrefs((current) => ({ ...current,
      projectOrder: [...ordered, ...(current.projectOrder || []).filter((key) => !moved.has(normalizeProjectPathKey(key)))],
    }));
  }

  function resetProjectOrder(paths: string[]) {
    const reset = new Set(paths.map(normalizeProjectPathKey));
    setProjectUiPrefs((current) => ({ ...current,
      projectOrder: (current.projectOrder || []).filter((key) => !reset.has(normalizeProjectPathKey(key))),
    }));
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
    pickingProjectFolder,
    browseProjectFolder,
    creationOptions,
    loadCreationOptions,
    createProjectFromTemplate,
    setNewProjectPath,
    newProjectType,
    setNewProjectType,
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
    outfitPackagePath,
    setOutfitPackagePath,
    outfitImportPlan,
    outfitImportStatus,
    loadingOutfitImportPlan,
    requestingOutfitImport,
    collapsedProjects,
    customPathSet,
    pinnedProjectSet,
    projectOrder,
    reorderProjects,
    resetProjectOrder,
    recordProjectOpen,
    projectItems,
    hiddenProjects,
    addProjectPath,
    removeCustomProject,
    hideProject,
    unhideProject,
    canRecycleProject,
    recycleProject,
    projectDisplayName,
    projectDisplayLabel,
    togglePinProject,
    startRenameProject,
    commitRenameProject,
    openProjectFolder,
    planActiveOutfitImport,
    requestActiveOutfitImport,
    editingProjectPath,
    editProjectPathDraft,
    setEditProjectPathDraft,
    startEditProject,
    cancelEditProject,
    saveEditProject,
    projectListError,
    refreshProjectList: async () => {
      setProjectListError("");
      setLoadingProjects(true);
      try {
        if (refreshProjects) {
          let target = endpoint;
          if (!runtimeConnected) {
            target = (await startRuntime()) || "";
            if (!target) throw new Error(t("project.runtimeRequired"));
          }
          await refreshProjects(target);
        }
        else await refresh();
      } catch (cause) {
        setProjectListError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setLoadingProjects(false);
      }
    },
    toggleProjectCollapse,
    expandProjectGroup,
  };
}
