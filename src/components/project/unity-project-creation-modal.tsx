import { invoke } from "@tauri-apps/api/core";
import { ArrowLeft, Copy, Folder, FolderPlus, Loader2, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { registerUnityProjectManagers } from "../../lib/api";
import type { UnityProjectCreateRequest, UnityProjectCreateResult, UnityProjectTemplate } from "../../lib/api";
import { Button } from "../ui/button";

export type UnityProjectCreation = {
  options: { templates: UnityProjectTemplate[]; loading: boolean; error: string };
  loadOptions: () => void;
  create: (request: UnityProjectCreateRequest) => Promise<UnityProjectCreateResult>;
  select: (path: string) => void;
};

const managerLabels = { unityHub: "Unity Hub", vcc: "VCC", alcom: "ALCOM" };
const managerGuides = {
  unityHub: "https://docs.unity.com/en-us/hub/project-manage",
  vcc: "https://vcc.docs.vrchat.com/guides/getting-started/#updating-existing-projects",
  alcom: "https://vrc-get.anatawa12.com/alcom/",
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
  const [result, setResult] = useState<UnityProjectCreateResult | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const selectedTemplate = (templates.find((template) => template.path === templatePath)
    ?? templates.find((template) => template.name === "Avatar") ?? templates[0])?.path ?? "";
  const destination = parentPath && name.trim() ? `${parentPath.replace(/[\\/]+$/, "")}\\${name.trim()}` : "";
  const needsRetry = result?.catalogs.some(({ status }) => status !== "registered" && status !== "already_registered");

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
      setResult(await creation.create({ name: name.trim(), parentPath, templatePath: selectedTemplate }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function retryRegistration(projectPath: string) {
    setSubmitting(true);
    setError("");
    try {
      const registration = await registerUnityProjectManagers(projectPath);
      if (!registration.ok) throw new Error(t("project.creation.registrationUnconfirmed"));
      setResult(registration);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSubmitting(false);
    }
  }

  async function copyPath(projectPath: string) {
    try {
      await navigator.clipboard.writeText(projectPath);
      setCopied(true);
    } catch {
      setError(t("project.creation.copyFailed"));
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
            {t(result ? "project.creation.created" : "project.creation.open")}
          </h2>
          <Button type="button" variant="ghost" className="h-8 w-8 shrink-0 p-0" aria-label={t("common.close")} disabled={submitting || pickingFolder} onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
        <div className="app-scrollbar mt-4 min-h-0 flex-1 overflow-y-auto pr-1">
          {result ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <p className="min-w-0 flex-1 select-text break-all text-sm text-muted-foreground">{result.projectPath}</p>
                <Button type="button" variant="outline" className="h-8 shrink-0 px-2 text-xs" onClick={() => void copyPath(result.projectPath)}>
                  <Copy className="h-3.5 w-3.5" />
                  {t(copied ? "project.creation.copied" : "project.creation.copyPath")}
                </Button>
              </div>
              <dl className="divide-y divide-border border-y border-border text-sm">
                {result.catalogs.map(({ catalog, status }) => (
                  <div key={catalog} className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-3 py-2">
                    <dt className="font-medium">{managerLabels[catalog]}</dt>
                    <dd>
                      <p>{t(`project.creation.status.${status}`)}</p>
                      {(status === "manual_required" || status === "failed") && (
                        <p className="mt-1 text-xs text-muted-foreground">{t(`project.creation.handoff.${catalog}`)}</p>
                      )}
                      {status !== "registered" && status !== "already_registered" && (
                        <a className="mt-1 inline-block text-xs text-primary underline" href={managerGuides[catalog]} target="_blank" rel="noreferrer" aria-label={t("project.creation.guideFor", { manager: managerLabels[catalog] })}>
                          {t("project.creation.guide")}
                        </a>
                      )}
                    </dd>
                  </div>
                ))}
              </dl>
              {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
              <div className="flex flex-wrap justify-end gap-2">
                {needsRetry && (
                  <Button type="button" variant="outline" disabled={submitting} onClick={() => void retryRegistration(result.projectPath)}>
                    {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                    {t("project.creation.retryRegistration")}
                  </Button>
                )}
                <Button type="button" disabled={submitting} onClick={() => creation.select(result.projectPath)}>
                  {t("project.selectProjectTitle")}
                </Button>
              </div>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
              <label className="block space-y-1.5 text-sm font-medium">
                <span>{t("project.creation.name")}</span>
                <input autoFocus className={fieldClass} value={name} disabled={submitting} onChange={(event) => setName(event.target.value)} required />
              </label>
              <label className="block space-y-1.5 text-sm font-medium">
                <span>{t("project.creation.template")}</span>
                <select className={fieldClass} value={selectedTemplate} disabled={submitting || loading || !templates.length} onChange={(event) => setTemplatePath(event.target.value)}>
                  {!templates.length && <option value="">{t(loading ? "common.loading" : "project.creation.noTemplates")}</option>}
                  {templates.map(({ name, path, unityVersion }) => {
                    const label = unityVersion ? `${name} (${unityVersion})` : name;
                    const duplicate = templates.some((template) => template.path !== path && template.name === name && template.unityVersion === unityVersion);
                    return <option key={path} value={path}>{duplicate ? `${label} (${path})` : label}</option>;
                  })}
                </select>
              </label>
              {optionsError && (
                <div className="flex flex-wrap items-center gap-2">
                  <p role="alert" className="min-w-0 flex-1 break-words text-sm text-destructive">{optionsError}</p>
                  <Button type="button" variant="outline" className="h-8 px-2 text-xs" onClick={creation.loadOptions}>
                    {t("startupSplash.retry")}
                  </Button>
                </div>
              )}
              <div className="space-y-1.5">
                <p className="text-sm font-medium">{t("project.creation.parentFolder")}</p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button type="button" variant="outline" className="h-9 shrink-0" disabled={submitting || pickingFolder} onClick={() => void chooseFolder()}>
                    {pickingFolder ? <Loader2 className="h-4 w-4 animate-spin" /> : <Folder className="h-4 w-4" />}
                    {t("project.creation.chooseFolder")}
                  </Button>
                  {parentPath && <p className="min-w-0 flex-1 break-all text-xs text-muted-foreground">{destination || parentPath}</p>}
                </div>
              </div>
              {error && <p role="alert" className="break-words text-sm text-destructive">{error}</p>}
              <div className="flex justify-end">
                <Button type="submit" disabled={submitting || pickingFolder || loading || !destination || !selectedTemplate}>
                  {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                  {t(submitting ? "project.creation.creating" : "project.creation.submit")}
                </Button>
              </div>
            </form>
          )}
        </div>
      </section>
    </div>
  );
}
