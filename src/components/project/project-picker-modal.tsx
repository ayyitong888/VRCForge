import { Eye, EyeOff, Folder, FolderPlus, Loader2, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ProjectSnapshot } from "../../lib/api";
import { projectKey, shortPath } from "../../lib/project-path";
import { Button } from "../ui/button";

type ProjectEntry = NonNullable<ProjectSnapshot["projects"]>[number];

export function ProjectPickerModal({
  open,
  projects,
  hiddenProjects,
  customPathSet,
  saving,
  newProjectPath,
  error,
  onClose,
  onSelectProject,
  onRemoveCustomProject,
  onRestoreProject,
  onNewProjectPathChange,
  onClearError,
  onAddProjectPath,
}: {
  open: boolean;
  projects: ProjectEntry[];
  hiddenProjects: ProjectEntry[];
  customPathSet: Set<string>;
  saving: boolean;
  newProjectPath: string;
  error: string;
  onClose: () => void;
  onSelectProject: (projectKey: string) => void;
  onRemoveCustomProject: (path: string) => void;
  onRestoreProject: (path: string) => void;
  onNewProjectPathChange: (value: string) => void;
  onClearError: () => void;
  onAddProjectPath: () => void;
}) {
  const { t } = useTranslation();
  if (!open) {
    return null;
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
      <section className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-border bg-card p-6 shadow-panel">
        <div className="flex min-w-0 items-center gap-2">
          <FolderPlus className="h-5 w-5 shrink-0 text-primary" />
          <h2 className="truncate text-lg font-semibold">{t("onboarding.step3Title")}</h2>
          <button
            type="button"
            className="ml-auto shrink-0 rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="app-scrollbar mt-4 min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
          <div>
            <div className="mb-2 text-xs font-medium text-muted-foreground">{t("project.scannedProjects")}</div>
            {projects.length > 0 ? (
              <div className="space-y-1">
                {projects.map((project) => {
                  const key = projectKey(project);
                  const isCustom = customPathSet.has((project.path || "").toLowerCase());
                  return (
                    <div key={key} className="group flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 transition-colors hover:bg-muted">
                      <button
                        type="button"
                        className="flex min-w-0 flex-1 items-center gap-2 text-left text-sm"
                        onClick={() => onSelectProject(key)}
                      >
                        <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="min-w-0 truncate">{project.name || shortPath(project.path || "")}</span>
                        <span className="min-w-0 truncate text-xs text-muted-foreground">{project.path}</span>
                      </button>
                      {isCustom ? (
                        <button
                          type="button"
                          title={t("project.removeFromList")}
                          disabled={saving}
                          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-colors hover:bg-background hover:text-destructive group-hover:opacity-100"
                          onClick={() => onRemoveCustomProject(project.path || "")}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">{t("project.noProjectsHint")}</p>
            )}
          </div>
          {hiddenProjects.length > 0 ? (
            <div>
              <div className="mb-2 text-xs font-medium text-muted-foreground">{t("project.hiddenProjects")}</div>
              <div className="space-y-1">
                {hiddenProjects.map((project) => (
                  <div key={project.path} className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground">
                    <EyeOff className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate">{project.name || shortPath(project.path || "")}</span>
                    <Button type="button" variant="outline" className="h-7 shrink-0 px-2 text-xs" disabled={saving} onClick={() => onRestoreProject(project.path || "")}>
                      <Eye className="mr-1 h-3.5 w-3.5" />
                      {t("project.restore")}
                    </Button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <div>
            <div className="mb-2 text-xs font-medium text-muted-foreground">{t("project.addProjectFolder")}</div>
            <div className="flex gap-2">
              <input
                value={newProjectPath}
                onChange={(event) => {
                  onNewProjectPathChange(event.target.value);
                  onClearError();
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    onAddProjectPath();
                  }
                }}
                placeholder={t("project.pathPlaceholder")}
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
              <Button type="button" disabled={saving || !newProjectPath.trim()} onClick={onAddProjectPath}>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {t("common.add")}
              </Button>
            </div>
            {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
            <p className="mt-2 text-xs text-muted-foreground">{t("project.pathHint")}</p>
          </div>
        </div>
      </section>
    </div>
  );
}
