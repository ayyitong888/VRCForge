import { Eye, FileCheck2, Loader2, PackagePlus } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import i18n from "../../i18n";
import type { PathToSkillCaptureRequest, PathToSkillCaptureResult } from "../../lib/api";
import type { PathToSkillOperationSummary } from "../../lib/path-to-skill-context";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

type PathToSkillCapturePanelProps = {
  initialSummary?: PathToSkillOperationSummary;
  onPreview: (request: PathToSkillCaptureRequest) => Promise<PathToSkillCaptureResult>;
  onWrite: (request: PathToSkillCaptureRequest) => Promise<PathToSkillCaptureResult>;
};

const INITIAL_SUMMARY = JSON.stringify(
  {
    workflow: "captured_workflow",
    steps: [],
  },
  null,
  2,
);

export function PathToSkillCapturePanel({ initialSummary, onPreview, onWrite }: PathToSkillCapturePanelProps) {
  const [summaryText, setSummaryText] = useState(() =>
    initialSummary ? JSON.stringify(initialSummary, null, 2) : INITIAL_SUMMARY,
  );
  const [packageId, setPackageId] = useState("");
  const [skillName, setSkillName] = useState("");
  const [title, setTitle] = useState("");
  const [sourceOutputPath, setSourceOutputPath] = useState("");
  const [packageOutputPath, setPackageOutputPath] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [confirmationFingerprint, setConfirmationFingerprint] = useState("");
  const [preview, setPreview] = useState<PathToSkillCaptureResult | null>(null);
  const [previewFingerprint, setPreviewFingerprint] = useState("");
  const [result, setResult] = useState<PathToSkillCaptureResult | null>(null);
  const [busyAction, setBusyAction] = useState<"preview" | "source" | "package" | "">("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const currentFingerprint = useMemo(
    () => JSON.stringify({ summaryText, packageId, skillName, title }),
    [packageId, skillName, summaryText, title],
  );
  const currentConfirmationFingerprint = useMemo(
    () => JSON.stringify({
      preview: currentFingerprint,
      sourceOutputPath: sourceOutputPath.trim(),
      packageOutputPath: packageOutputPath.trim(),
    }),
    [currentFingerprint, packageOutputPath, sourceOutputPath],
  );
  const previewIsCurrent = Boolean(preview && previewFingerprint === currentFingerprint);
  const confirmationIsCurrent = Boolean(
    confirmed && confirmationFingerprint === currentConfirmationFingerprint,
  );
  const summaryRequirementError = operationRequirementError(summaryText);

  function invalidateConfirmation() {
    setConfirmed(false);
    setConfirmationFingerprint("");
  }

  function invalidatePreview() {
    setPreview(null);
    setPreviewFingerprint("");
    setResult(null);
    invalidateConfirmation();
    setMessage("");
    setError("");
  }

  function buildRequest(): PathToSkillCaptureRequest {
    let summary: unknown;
    try {
      summary = JSON.parse(summaryText);
    } catch (cause) {
      throw new Error(i18n.t("package.pathToSkill.invalidJson", { detail: cause instanceof Error ? cause.message : String(cause) }));
    }
    if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
      throw new Error(i18n.t("package.pathToSkill.objectRequired"));
    }
    const operationSummary = summary as Record<string, unknown>;
    if (!hasCapturedOperationOrRecipe(operationSummary)) {
      throw new Error(i18n.t("package.pathToSkill.stepsRequired"));
    }
    return {
      summary: operationSummary,
      packageId: packageId.trim() || undefined,
      skillName: skillName.trim() || undefined,
      title: title.trim() || undefined,
    };
  }

  async function runPreview() {
    setBusyAction("preview");
    setMessage("");
    setError("");
    setResult(null);
    try {
      const payload = await onPreview(buildRequest());
      setPreview(payload);
      setPreviewFingerprint(currentFingerprint);
      invalidateConfirmation();
      setMessage(i18n.t("package.pathToSkill.previewReady"));
    } catch (cause) {
      setPreview(null);
      setPreviewFingerprint("");
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  }

  async function runWrite(kind: "source" | "package") {
    if (!previewIsCurrent || !confirmationIsCurrent) {
      return;
    }
    setBusyAction(kind);
    setMessage("");
    setError("");
    try {
      const base = buildRequest();
      const payload = await onWrite(
        kind === "source"
          ? {
              ...base,
              outputPath: sourceOutputPath.trim(),
              writeSource: true,
              useTempOutput: false,
            }
          : {
              ...base,
              exportVsk: true,
              confirmExport: true,
              packageOutputPath: packageOutputPath.trim(),
            },
      );
      setResult(payload);
      setMessage(
        kind === "source"
          ? i18n.t("package.pathToSkill.sourceWritten")
          : i18n.t("package.pathToSkill.packageExported"),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyAction("");
    }
  }

  const shown = result || preview;
  const shownManifest = shown?.manifest || {};
  const shownWorkflow = shown?.workflow || {};
  const shownFiles = shown?.files || [];
  const shownSourceFiles = Object.entries(shown?.sourceFiles || {}).sort(([left], [right]) => left.localeCompare(right));
  const exported = result?.exported;
  const exportedPath = String(exported?.package_path || exported?.packagePath || "");

  return (
    <div data-vrcforge-path-to-skill-panel="true" className="grid gap-3 rounded-lg border border-border bg-background p-3">
      <div className="flex min-w-0 items-center gap-2">
        <FileCheck2 className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{i18n.t("package.pathToSkill.title")}</div>
          <div className="text-xs text-muted-foreground">{i18n.t("package.pathToSkill.description")}</div>
        </div>
        {initialSummary ? (
          <Badge tone="default" data-vrcforge-path-to-skill-prefilled="true">
            {i18n.t("package.pathToSkill.prefilledFromRun")}
          </Badge>
        ) : null}
        {previewIsCurrent ? <Badge tone="ok">{i18n.t("package.pathToSkill.sanitized")}</Badge> : null}
      </div>

      <Field label={i18n.t("package.pathToSkill.operationSummary")}>
        <textarea
          data-vrcforge-path-to-skill-operation-summary
          value={summaryText}
          onChange={(event) => {
            setSummaryText(event.target.value);
            invalidatePreview();
          }}
          rows={8}
          spellCheck={false}
          className="w-full resize-y rounded-md border border-border bg-card px-3 py-2 font-mono text-xs outline-none focus:border-primary"
        />
      </Field>

      <div className="grid gap-3 md:grid-cols-3">
        <Field label={i18n.t("package.packageId")}>
          <input
            data-vrcforge-path-to-skill-package-id
            value={packageId}
            onChange={(event) => {
              setPackageId(event.target.value);
              invalidatePreview();
            }}
            placeholder="community.path-to-skill.example"
            className="h-10 w-full rounded-md border border-border bg-card px-3 font-mono text-xs outline-none focus:border-primary"
          />
        </Field>
        <Field label={i18n.t("package.skillName")}>
          <input
            value={skillName}
            onChange={(event) => {
              setSkillName(event.target.value);
              invalidatePreview();
            }}
            className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
          />
        </Field>
        <Field label={i18n.t("package.pathToSkill.skillTitle")}>
          <input
            value={title}
            onChange={(event) => {
              setTitle(event.target.value);
              invalidatePreview();
            }}
            className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
          />
        </Field>
      </div>

      <div className="flex justify-end">
        <Button
          data-vrcforge-path-to-skill-preview
          type="button"
          variant="outline"
          disabled={Boolean(busyAction) || Boolean(summaryRequirementError)}
          onClick={() => void runPreview()}
        >
          {busyAction === "preview" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Eye className="h-4 w-4" />}
          {i18n.t("package.pathToSkill.preview")}
        </Button>
      </div>

      {shown ? (
        <div className="grid gap-3 rounded-md border border-border bg-card p-3">
          <div className="grid gap-2 md:grid-cols-3">
            <DataLine label={i18n.t("package.packageId")} value={String(shownManifest.id || "-")} />
            <DataLine label={i18n.t("package.skillName")} value={String(shownManifest.skill_name || shownManifest.skillName || "-")} />
            <DataLine label={i18n.t("package.pathToSkill.files")} value={String(shownFiles.length)} />
          </div>
          <Output label={i18n.t("package.pathToSkill.sanitizedManifest")} value={JSON.stringify(shownManifest, null, 2)} />
          <div className="grid gap-3 md:grid-cols-2">
            <Output
              label={i18n.t("package.pathToSkill.permissions")}
              value={JSON.stringify(shownManifest.permissions || [], null, 2)}
            />
            <Output
              label={i18n.t("package.pathToSkill.entrypoints")}
              value={JSON.stringify(shownManifest.entrypoints || {}, null, 2)}
            />
          </div>
          <Output label={i18n.t("package.pathToSkill.sanitizedWorkflow")} value={JSON.stringify(shownWorkflow, null, 2)} />
          <Output label={i18n.t("package.pathToSkill.skillMarkdown")} value={shown.skillMarkdown || ""} />
          <div className="grid gap-3">
            <span className="text-xs font-medium text-muted-foreground">
              {i18n.t("package.pathToSkill.sourceFiles", { count: shownSourceFiles.length })}
            </span>
            {shownSourceFiles.map(([path, content]) => (
              <Output key={path} label={path} value={content} />
            ))}
          </div>
          {result?.writtenSource?.path ? <DataLine label={i18n.t("package.pathToSkill.sourceOutput")} value={result.writtenSource.path} /> : null}
          {exportedPath ? <DataLine label={i18n.t("package.pathToSkill.packageOutput")} value={exportedPath} /> : null}
        </div>
      ) : null}

      {previewIsCurrent ? (
        <>
          <label className="flex min-w-0 items-start gap-2 rounded-md border border-border bg-card px-3 py-2 text-xs text-muted-foreground">
            <input
              data-vrcforge-path-to-skill-confirmation
              type="checkbox"
              className="mt-0.5"
              checked={confirmationIsCurrent}
              onChange={(event) => {
                setConfirmed(event.target.checked);
                setConfirmationFingerprint(event.target.checked ? currentConfirmationFingerprint : "");
              }}
            />
            <span>{i18n.t("package.pathToSkill.confirmSanitized")}</span>
          </label>
          <div className="grid gap-1">
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
              <Field label={i18n.t("package.pathToSkill.sourceOutput")}>
                <input
                  value={sourceOutputPath}
                  onChange={(event) => {
                    setSourceOutputPath(event.target.value);
                    invalidateConfirmation();
                  }}
                  className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
                />
              </Field>
              <Button
                type="button"
                variant="outline"
                className="self-end"
                disabled={Boolean(busyAction) || !confirmationIsCurrent || !sourceOutputPath.trim()}
                onClick={() => void runWrite("source")}
              >
                {busyAction === "source" ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileCheck2 className="h-4 w-4" />}
                {i18n.t("package.pathToSkill.writeSource")}
              </Button>
            </div>
            <span className="text-xs text-muted-foreground">
              {i18n.t("package.pathToSkill.sourceOutputMustBeNew")}
            </span>
          </div>
          <div className="grid gap-1">
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
              <Field label={i18n.t("package.pathToSkill.packageOutput")}>
                <input
                  value={packageOutputPath}
                  onChange={(event) => {
                    setPackageOutputPath(event.target.value);
                    invalidateConfirmation();
                  }}
                  placeholder="example.vsk"
                  className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
                />
              </Field>
              <Button
                type="button"
                className="self-end"
                disabled={Boolean(busyAction) || !confirmationIsCurrent || !packageOutputPath.trim()}
                onClick={() => void runWrite("package")}
              >
                {busyAction === "package" ? <Loader2 className="h-4 w-4 animate-spin" /> : <PackagePlus className="h-4 w-4" />}
                {i18n.t("package.pathToSkill.exportPackage")}
              </Button>
            </div>
            <span className="text-xs text-muted-foreground">
              {i18n.t("package.pathToSkill.packageOutputMustBeNew")}
            </span>
          </div>
        </>
      ) : null}

      {message ? <Badge tone="ok" className="w-fit">{message}</Badge> : null}
      {error || summaryRequirementError ? (
        <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {error || summaryRequirementError}
        </div>
      ) : null}
    </div>
  );
}

function isNonEmptyOperationItem(value: unknown): boolean {
  if (typeof value === "string") {
    return Boolean(value.trim());
  }
  if (Array.isArray(value)) {
    return value.some(isNonEmptyOperationItem);
  }
  if (value && typeof value === "object") {
    return Object.values(value as Record<string, unknown>).some(isNonEmptyOperationItem);
  }
  return false;
}

function operationRequirementError(summaryText: string): string {
  try {
    const summary = JSON.parse(summaryText) as unknown;
    if (!summary || typeof summary !== "object" || Array.isArray(summary)) {
      return "";
    }
    return hasCapturedOperationOrRecipe(summary as Record<string, unknown>)
      ? ""
      : i18n.t("package.pathToSkill.stepsRequired");
  } catch {
    return "";
  }
}

const KNOWN_RECIPE_NAMES = new Set([
  "ttt_material_group",
  "textrans_material_group",
  "ttt_atlas_group",
  "ttt_atlas_material_group",
  "booth_import_preflight",
  "booth_package_preflight",
  "outfit_import_preflight",
  "parameter_compression",
  "parameter_compressor",
  "vrcfury_parameter_compression",
  "pc_quest_upload_pass",
  "pc_android_upload_pass",
  "cross_platform_upload_gate",
]);

function normalizeRecipeName(value: unknown): string {
  return typeof value === "string"
    ? value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "")
    : "";
}

function hasCapturedOperationOrRecipe(operationSummary: Record<string, unknown>): boolean {
  const hasOperationItem = [operationSummary.steps, operationSummary.skillPath].some(
    (items) => Array.isArray(items) && items.some(isNonEmptyOperationItem),
  );
  if (hasOperationItem) {
    return true;
  }
  const explicitRecipe = operationSummary.recipeType ?? operationSummary.recipe_type;
  if (typeof explicitRecipe === "string" && explicitRecipe.trim()) {
    // The backend remains authoritative and returns the supported-name list for
    // an unknown explicit recipe. Do not make the UI reject a valid new recipe.
    return true;
  }
  return KNOWN_RECIPE_NAMES.has(normalizeRecipeName(operationSummary.workflow));
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-2 text-sm">
      <span className="truncate font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function Output({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-background p-3 text-xs">
        {value}
      </pre>
    </div>
  );
}
