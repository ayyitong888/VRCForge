import { invoke } from "@tauri-apps/api/core";
import { ArrowLeft, Folder, FolderPlus, Loader2, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { UnityProjectCreateRequest, UnityProjectCreateResult, UnityProjectTemplate } from "../../lib/api";
import { Button } from "../ui/button";
import { defaultUnityProjectTemplate, UnityProjectTemplatePicker } from "./unity-project-template-picker";

export type UnityProjectCreation = {
  options: { templates: UnityProjectTemplate[]; loading: boolean; error: string };
  loadOptions: () => void;
  create: (request: UnityProjectCreateRequest) => Promise<UnityProjectCreateResult>;
  select: (path: string) => void;
};

const fieldClass = "h-9 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:opacity-60";

export function UnityProjectCreationModal({ creation, onBack, onClose }: {
  creation: UnityProjectCreation;
  onBack: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { templates, loading, error: optionsError } = creation.options;
  const [name, setName] = useState("");
  const [parentPath, setParentPath] = useState("");
  const [templatePath, setTemplatePath] = useState("");
  const [pickingFolder, setPickingFolder] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const selectedTemplate = templates.find((template) => template.path === templatePath)
    ?? defaultUnityProjectTemplate(templates);
  const destination = parentPath && name.trim() ? `${parentPath.replace(/[\\/]+$/, "")}\\${name.trim()}` : "";

  async function chooseFolder() {
    setPickingFolder(true);
    setError("");
    try {
      const path = await invoke<string | null>("select_folder", { initialPath: parentPath || undefined });
      if (path) setParentPath(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPickingFolder(false);
    }
  }

  async function save() {
    if (submitting || pickingFolder || loading || !destination || !selectedTemplate) return;
    setSubmitting(true);
    setError("");
    try {
      const { projectPath } = await creation.create({ name: name.trim(), parentPath, templatePath: selectedTemplate.path });
      creation.select(projectPath);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-6">
      <section role="dialog" aria-modal="true" aria-labelledby="unity-project-create-title" className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-border bg-card p-6 shadow-panel">
        <div className="flex min-w-0 items-center gap-2">
          <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" aria-label={t("project.creation.back")} disabled={submitting || pickingFolder} onClick={onBack}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <FolderPlus className="h-5 w-5 shrink-0 text-primary" />
          <h2 id="unity-project-create-title" className="min-w-0 flex-1 truncate text-lg font-semibold">
            {t("project.creation.open")}
          </h2>
          <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" aria-label={t("common.close")} disabled={submitting || pickingFolder} onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="app-scrollbar mt-4 min-h-0 flex-1 overflow-y-auto pr-1">
          <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
            {selectedTemplate ? (
              <UnityProjectTemplatePicker
                templates={templates}
                selected={selectedTemplate}
                disabled={submitting || loading}
                onSelect={setTemplatePath}
              />
            ) : (
              <div className="space-y-1.5">
                <p className="text-sm font-medium">{t("project.creation.template")}</p>
                <p className="text-sm text-muted-foreground">
                  {t(loading ? "common.loading" : "project.creation.noTemplates")}
                </p>
              </div>
            )}
            {optionsError && (
              <div className="flex flex-wrap items-center gap-2">
                <p role="alert" className="min-w-0 flex-1 break-words text-sm text-destructive">{optionsError}</p>
                <Button type="button" variant="outline" className="h-8 px-2 text-xs" onClick={creation.loadOptions}>
                  {t("startupSplash.retry")}
                </Button>
              </div>
            )}
            <label className="block space-y-1.5 text-sm font-medium">
              <span>{t("project.creation.name")}</span>
              <input autoFocus className={fieldClass} value={name} disabled={submitting} onChange={(event) => setName(event.target.value)} required />
            </label>
            <div className="space-y-1.5">
              <p className="text-sm font-medium">{t("project.creation.parentFolder")}</p>
              <Button type="button" variant="outline" className="h-9 w-full justify-start gap-2 px-3" disabled={submitting || pickingFolder} onClick={() => void chooseFolder()}>
                {pickingFolder ? <Loader2 className="h-4 w-4 shrink-0 animate-spin" /> : <Folder className="h-4 w-4 shrink-0" />}
                {parentPath && <span className="min-w-0 flex-1 truncate text-left text-muted-foreground" title={parentPath}>{parentPath}</span>}
                <span className="shrink-0">{t("project.creation.chooseFolder")}</span>
              </Button>
              {destination && <p className="select-text break-all text-xs text-muted-foreground">{t("project.creation.destination", { path: destination })}</p>}
            </div>
            {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
            <div className="flex justify-end">
              <Button type="submit" disabled={submitting || pickingFolder || loading || !destination || !selectedTemplate}>
                {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                {t(submitting ? "project.creation.creating" : "project.creation.submit")}
              </Button>
            </div>
          </form>
        </div>
      </section>
    </div>
  );
}
