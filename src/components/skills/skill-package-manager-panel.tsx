import { Check, Copy, Eye, EyeOff, FileUp, Loader2, Plus, RefreshCw, Shield, Trash2, X } from "lucide-react";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { DragDropEvent } from "@tauri-apps/api/webview";
import i18n from "../../i18n";
import type {
  PathToSkillCaptureRequest,
  PathToSkillCaptureResult,
  OfficialKeyStatus,
  SkillPackageEntry,
  SkillPackagePreflight,
} from "../../lib/api";
import { exportOfficialKey, fetchOfficialKey, importOfficialKey } from "../../lib/api/skill-packages";
import type { PathToSkillDraftSeed } from "../../lib/path-to-skill-context";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";
import { PathToSkillCapturePanel } from "./path-to-skill-capture-panel";
import { SkillPackageAuditList } from "./skill-package-audit-list";
import { hasTauriInternals } from "../../lib/api/http";

export function SkillPackageManagerPanel({
  packages,
  endpoint,
  packageStore,
  loading,
  message,
  error,
  governance,
  audit,
  pathToSkillDraftSeed,
  onRefresh,
  onPreflight,
  onImport,
  onExport,
  onSetEnabled,
  onUninstall,
  onSetSafeMode,
  onTrustSigner,
  onRevokeSigner,
  onBlockPackage,
  onPreviewPathToSkill,
  onWritePathToSkill,
}: {
  packages: SkillPackageEntry[];
  endpoint: string;
  packageStore: string;
  loading: boolean;
  message: string;
  error: string;
  governance: Record<string, unknown>;
  audit: Array<Record<string, unknown>>;
  pathToSkillDraftSeed: PathToSkillDraftSeed | null;
  onRefresh: () => void | Promise<void>;
  onPreflight: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImport: (packagePath: string) => Promise<unknown>;
  onExport: (skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) => Promise<unknown>;
  onSetEnabled: (skillPackageId: string, enabled: boolean) => Promise<unknown>;
  onUninstall: (skillPackageId: string) => Promise<unknown>;
  onSetSafeMode: (enabled: boolean, reason?: string) => Promise<unknown>;
  onTrustSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onRevokeSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onBlockPackage: (request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) => Promise<unknown>;
  onPreviewPathToSkill: (request: PathToSkillCaptureRequest) => Promise<PathToSkillCaptureResult>;
  onWritePathToSkill: (request: PathToSkillCaptureRequest) => Promise<PathToSkillCaptureResult>;
}) {
  const [packagePath, setPackagePath] = useState("");
  const [exportSkillName, setExportSkillName] = useState("");
  const [exportPath, setExportPath] = useState("");
  const [exportPrivateKeyPath, setExportPrivateKeyPath] = useState("");
  const [releaseExport, setReleaseExport] = useState(false);
  const [preflight, setPreflight] = useState<SkillPackagePreflight | null>(null);
  const [localMessage, setLocalMessage] = useState("");
  const [localError, setLocalError] = useState("");
  const [packageActionId, setPackageActionId] = useState("");
  const [governanceReason, setGovernanceReason] = useState("");
  const [signerFingerprint, setSignerFingerprint] = useState("");
  const [blockPackageId, setBlockPackageId] = useState("");
  const [draggingPackage, setDraggingPackage] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [officialKey, setOfficialKey] = useState<OfficialKeyStatus | null>(null);
  const [officialKeyOutputPath, setOfficialKeyOutputPath] = useState("");
  const [officialKeyBackupPath, setOfficialKeyBackupPath] = useState("");
  const [officialKeyExportPassphrase, setOfficialKeyExportPassphrase] = useState("");
  const [officialKeyImportPassphrase, setOfficialKeyImportPassphrase] = useState("");
  const [officialKeyBusy, setOfficialKeyBusy] = useState(false);
  const packageDropzoneRef = useRef<HTMLDivElement | null>(null);
  function isInsidePackageDropzone(position: { x: number; y: number }) {
    const element = packageDropzoneRef.current;
    if (!element) return false;
    const ratio = window.devicePixelRatio || 1;
    const bounds = element.getBoundingClientRect();
    const x = position.x / ratio;
    const y = position.y / ratio;
    return x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom;
  }
  const preview = normalizeSkillPackagePreview(preflight);
  const safeModeEnabled = skillPackageSafeModeEnabled(governance);
  useEffect(() => {
    let active = true;
    void fetchOfficialKey(endpoint).then((payload) => {
      if (active) setOfficialKey(payload);
    }).catch(() => {
      if (active) setOfficialKey(null);
    });
    return () => { active = false; };
  }, [endpoint]);
  async function runOfficialKeyExport() {
    if (officialKeyExportPassphrase.length < 8 || !officialKeyOutputPath.trim()) return;
    setOfficialKeyBusy(true); setLocalMessage(""); setLocalError("");
    try {
      await exportOfficialKey(endpoint, { outputPath: officialKeyOutputPath.trim(), passphrase: officialKeyExportPassphrase });
      setOfficialKeyExportPassphrase("");
      setLocalMessage(i18n.t("package.messages.officialKeyExported"));
    } catch (cause) { setLocalError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setOfficialKeyBusy(false); }
  }
  async function runOfficialKeyImport() {
    if (officialKeyImportPassphrase.length < 8 || !officialKeyBackupPath.trim()) return;
    setOfficialKeyBusy(true); setLocalMessage(""); setLocalError("");
    try {
      const payload = await importOfficialKey(endpoint, { backupPath: officialKeyBackupPath.trim(), passphrase: officialKeyImportPassphrase });
      setOfficialKey((current) => ({
        exists: payload.exists === true || current?.exists === true,
        fingerprint: payload.fingerprint ?? current?.fingerprint ?? null,
        publisher: payload.publisher ?? current?.publisher ?? null,
        publicKeyPath: payload.publicKeyPath ?? current?.publicKeyPath ?? null,
      }));
      setOfficialKeyImportPassphrase("");
      setLocalMessage(i18n.t("package.messages.officialKeyImported"));
      await Promise.resolve(onRefresh());
    } catch (cause) { setLocalError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setOfficialKeyBusy(false); }
  }
  async function runPreflight() {
    if (!packagePath.trim()) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      const payload = await onPreflight(packagePath.trim());
      setPreflight(payload);
      setLocalMessage(i18n.t("package.messages.preflightComplete"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  async function runImport() {
    if (!packagePath.trim()) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      await onImport(packagePath.trim());
      setLocalMessage(i18n.t("package.messages.packageImported"));
      setPreflight(null);
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  useEffect(() => {
    if (!hasTauriInternals()) {
      return;
    }
    let unlisten: (() => void) | undefined;
    void getCurrentWebview().onDragDropEvent((event: { payload: DragDropEvent }) => {
      const payload = event.payload;
      if (payload.type === "enter" || payload.type === "over") {
        setDraggingPackage(isInsidePackageDropzone(payload.position));
        return;
      }
      if (payload.type === "leave") {
        setDraggingPackage(false);
        return;
      }
      if (payload.type !== "drop" || !isInsidePackageDropzone(payload.position)) return;
      setDraggingPackage(false);
      const path = payload.paths.find((candidate) => candidate.toLowerCase().endsWith(".vsk"));
      if (!path) {
        setLocalError(i18n.t("package.dropInvalid"));
        return;
      }
      setPackagePath(path);
      setLocalMessage("");
      setLocalError("");
      void (async () => {
        try {
          const previewPayload = await onPreflight(path);
          setPreflight(previewPayload);
          await onImport(path);
          setPreflight(null);
          setLocalMessage(i18n.t("package.messages.packageImported"));
        } catch (cause) {
          setLocalError(cause instanceof Error ? cause.message : String(cause));
        }
      })();
    }).then((cleanup) => { unlisten = cleanup; });
    return () => { unlisten?.(); };
  }, [onImport, onPreflight]);
  async function runExport() {
    const privateKeyPath = exportPrivateKeyPath.trim();
    if (!exportSkillName.trim() || !exportPath.trim() || (releaseExport && !privateKeyPath)) {
      return;
    }
    setLocalMessage("");
    setLocalError("");
    try {
      await onExport(exportSkillName.trim(), exportPath.trim(), releaseExport, privateKeyPath || undefined);
      setLocalMessage(releaseExport ? i18n.t("package.messages.releaseExported") : i18n.t("package.messages.devExported"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    }
  }
  function preparePackageExport(pkg: SkillPackageEntry) {
    setExportSkillName(skillPackageTitle(pkg));
    setShowAdvanced(true);
  }
  async function runSetEnabled(skillPackageIdValue: string, enabled: boolean) {
    if (!skillPackageIdValue || skillPackageIdValue === "-") {
      return;
    }
    setPackageActionId(skillPackageIdValue);
    setLocalMessage("");
    setLocalError("");
    try {
      await onSetEnabled(skillPackageIdValue, enabled);
      setLocalMessage(enabled ? i18n.t("package.messages.packageEnabled") : i18n.t("package.messages.packageDisabled"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runUninstall(skillPackageIdValue: string) {
    if (!skillPackageIdValue || skillPackageIdValue === "-") {
      return;
    }
    setPackageActionId(skillPackageIdValue);
    setLocalMessage("");
    setLocalError("");
    try {
      await onUninstall(skillPackageIdValue);
      setLocalMessage(i18n.t("package.messages.packageUninstalled"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runSetSafeMode(enabled: boolean) {
    setPackageActionId("safe-mode");
    setLocalMessage("");
    setLocalError("");
    try {
      await onSetSafeMode(enabled, governanceReason.trim() || undefined);
      setLocalMessage(enabled ? i18n.t("package.messages.safeModeEnabled") : i18n.t("package.labels.safeModeDisabled"));
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runTrustSigner(value = signerFingerprint) {
    const fingerprint = value.trim();
    if (!fingerprint || fingerprint === "-") {
      return;
    }
    setPackageActionId(`signer-${fingerprint}`);
    setLocalMessage("");
    setLocalError("");
    try {
      await onTrustSigner(fingerprint, governanceReason.trim() || undefined);
      setLocalMessage(i18n.t("package.messages.signerTrusted"));
      setSignerFingerprint("");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runRevokeSigner(value = signerFingerprint) {
    const fingerprint = value.trim();
    if (!fingerprint || fingerprint === "-") {
      return;
    }
    setPackageActionId(`signer-${fingerprint}`);
    setLocalMessage("");
    setLocalError("");
    try {
      await onRevokeSigner(fingerprint, governanceReason.trim() || undefined);
      setLocalMessage(i18n.t("package.messages.signerRevoked"));
      setSignerFingerprint("");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  async function runBlockPackage(pkg?: SkillPackageEntry) {
    const id = (pkg ? skillPackageId(pkg) : blockPackageId.trim()).trim();
    const packageSha256 = pkg ? skillPackagePackageSha(pkg) : "";
    if ((!id || id === "-") && !packageSha256) {
      return;
    }
    setPackageActionId(`block-${id || packageSha256}`);
    setLocalMessage("");
    setLocalError("");
    try {
      await onBlockPackage({
        packageId: id && id !== "-" ? id : undefined,
        packageSha256: packageSha256 || undefined,
        reason: governanceReason.trim() || undefined,
      });
      setLocalMessage(i18n.t("package.messages.packageBlocked"));
      setBlockPackageId("");
    } catch (cause) {
      setLocalError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setPackageActionId("");
    }
  }
  const displayMessage = localMessage || message;
  const displayError = localError || error;
  return (
    <details open className="order-first min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel" data-vrcforge-installed-skills>
      <summary className="flex min-w-0 cursor-pointer list-none items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{i18n.t("package.title")}</div>
        <Badge tone="muted" className="shrink-0">
          {packages.length}
        </Badge>
        <Button
          type="button"
          variant="ghost"
          className="h-7 px-2 text-xs"
          onClick={(event) => {
            event.preventDefault();
            event.stopPropagation();
            onRefresh();
          }}
          disabled={loading}
        >
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
      </summary>

      <div className="mt-5 grid gap-4">
        <div className="grid gap-3">
          {displayMessage ? <Badge tone={skillPackageMessageTone(displayMessage)} className="w-fit">{displayMessage}</Badge> : null}
          {displayError ? <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">{displayError}</div> : null}
        </div>

        <div className="grid gap-2">
          {packages.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">{i18n.t("package.noPackages")}</div>
          ) : null}
          {packages.map((pkg, index) => {
            const id = skillPackageId(pkg);
            const enabled = skillPackageEnabled(pkg);
            const busy = loading || packageActionId === id;
            return (
              <div key={`${id}-${index}`} className="grid min-w-0 gap-3 rounded-lg border border-border/60 bg-card px-3 py-3 text-xs sm:grid-cols-[minmax(0,1fr)_auto]" data-vrcforge-installed-skill-card>
                <div className="min-w-0">
                  <div className="break-words font-medium">{skillPackageTitle(pkg)}</div>
                  <div className="break-all text-muted-foreground">{id}</div>
                  <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
                    <Badge tone={skillPackageLabelTone(skillPackageRisk(pkg))} className="h-5 px-1.5 text-[10px]">{skillPackageRiskLabel(pkg)}</Badge>
                    {skillPackageLabels(pkg).map((label) => (
                      <Badge key={label} title={skillPackageExecutionDescription(pkg)} tone={skillPackageLabelTone(label)} className="h-5 px-1.5 text-[10px]">{label}</Badge>
                    ))}
                  </div>
                </div>
                <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
                  <SkillSwitch
                    checked={enabled}
                    busy={busy}
                    label={enabled ? i18n.t("package.disable") : i18n.t("package.enable")}
                    onChange={() => void runSetEnabled(id, !enabled)}
                  />
                  <Button type="button" variant="outline" className="h-8 px-2 text-xs" disabled={busy || id === "-"} onClick={() => preparePackageExport(pkg)}>
                    <Copy className="h-3.5 w-3.5" />
                    {i18n.t("package.export")}
                  </Button>
                  <details className="relative">
                    <summary className="flex h-8 cursor-pointer list-none items-center rounded-md border border-border px-2 text-xs text-muted-foreground">{i18n.t("package.advancedActions")}</summary>
                    <div className="absolute right-0 z-10 mt-1 flex min-w-40 flex-col gap-1 rounded-md border border-border bg-card p-1 shadow-panel">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2 text-xs"
                    disabled={busy || skillPackageSigner(pkg) === "-"}
                    onClick={() => void runTrustSigner(skillPackageSigner(pkg))}
                  >
                    <Check className="h-3.5 w-3.5" />
                    {i18n.t("package.trust")}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2 text-xs"
                    disabled={busy || skillPackageSigner(pkg) === "-"}
                    onClick={() => void runRevokeSigner(skillPackageSigner(pkg))}
                  >
                    <X className="h-3.5 w-3.5" />
                    {i18n.t("package.revoke")}
                  </Button>
                  <Button
                    type="button"
                    variant="danger"
                    className="h-8 px-2 text-xs"
                    disabled={busy || id === "-"}
                    onClick={() => void runBlockPackage(pkg)}
                  >
                    <EyeOff className="h-3.5 w-3.5" />
                    {i18n.t("package.block")}
                  </Button>
                    </div>
                  </details>
                  <Button
                    type="button"
                    variant="danger"
                    className="h-8 px-2 text-xs"
                    disabled={busy || id === "-"}
                    onClick={() => void runUninstall(id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {i18n.t("package.uninstall")}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>

        <details className="grid gap-3 rounded-lg border border-border bg-background p-3">
          <summary className="cursor-pointer text-sm font-medium">
            {i18n.t("package.governance")}
            <span className="ml-2 text-xs font-normal text-muted-foreground">{safeModeEnabled ? i18n.t("package.safeMode") : i18n.t("package.standard")}</span>
          </summary>
          <DataLine label={i18n.t("package.store")} value={packageStore || "-"} />
          <div className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{i18n.t("package.governance")}</span>
            <Badge tone={safeModeEnabled ? "warn" : "muted"} className="shrink-0">
              {safeModeEnabled ? i18n.t("package.safeMode") : i18n.t("package.standard")}
            </Badge>
            <Badge tone="muted" className="shrink-0">
              {audit.length} audit
            </Badge>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <SkillFieldLabel label={i18n.t("package.reason")}>
              <input
                value={governanceReason}
                onChange={(event) => setGovernanceReason(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </SkillFieldLabel>
            <Button
              type="button"
              variant={safeModeEnabled ? "outline" : "primary"}
              className="self-end"
              disabled={loading || packageActionId === "safe-mode"}
              onClick={() => void runSetSafeMode(!safeModeEnabled)}
            >
              {packageActionId === "safe-mode" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Shield className="h-4 w-4" />}
              {safeModeEnabled ? i18n.t("package.disableSafeMode") : i18n.t("package.enableSafeMode")}
            </Button>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto]">
            <SkillFieldLabel label={i18n.t("package.signerFingerprint")}>
              <input
                value={signerFingerprint}
                onChange={(event) => setSignerFingerprint(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 font-mono text-xs outline-none focus:border-primary"
              />
            </SkillFieldLabel>
            <Button type="button" variant="outline" className="self-end" disabled={loading || !signerFingerprint.trim()} onClick={() => void runTrustSigner()}>
              <Check className="h-4 w-4" />
              {i18n.t("package.trust")}
            </Button>
            <Button type="button" variant="danger" className="self-end" disabled={loading || !signerFingerprint.trim()} onClick={() => void runRevokeSigner()}>
              <X className="h-4 w-4" />
              {i18n.t("package.revoke")}
            </Button>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <SkillFieldLabel label={i18n.t("package.packageId")}>
              <input
                value={blockPackageId}
                onChange={(event) => setBlockPackageId(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </SkillFieldLabel>
            <Button type="button" variant="danger" className="self-end" disabled={loading || !blockPackageId.trim()} onClick={() => void runBlockPackage()}>
              <EyeOff className="h-4 w-4" />
              {i18n.t("package.block")}
            </Button>
          </div>
          <SkillPackageAuditList audit={audit} />
          <details className="rounded-md border border-border/70 bg-background px-3 py-2">
            <summary className="cursor-pointer text-xs font-medium">{i18n.t("package.officialKey")}</summary>
            <div className="mt-3 grid gap-3">
              <DataLine label={i18n.t("package.officialKeyFingerprint")} value={officialKey?.fingerprint || "-"} mono />
              <div className="grid gap-3 md:grid-cols-2">
                <div className="grid gap-2">
                  <SkillFieldLabel label={i18n.t("package.officialKeyExportPath")}>
                    <input value={officialKeyOutputPath} onChange={(event) => setOfficialKeyOutputPath(event.target.value)} className="h-9 w-full rounded-md border border-border bg-card px-3 text-xs outline-none focus:border-primary" />
                  </SkillFieldLabel>
                  <SkillFieldLabel label={i18n.t("package.officialKeyPassphrase")}>
                    <input type="password" minLength={8} value={officialKeyExportPassphrase} onChange={(event) => setOfficialKeyExportPassphrase(event.target.value)} className="h-9 w-full rounded-md border border-border bg-card px-3 text-xs outline-none focus:border-primary" autoComplete="new-password" />
                  </SkillFieldLabel>
                  <Button type="button" variant="outline" className="h-8 w-fit text-xs" disabled={officialKeyBusy || officialKeyExportPassphrase.length < 8 || !officialKeyOutputPath.trim()} onClick={() => void runOfficialKeyExport()}>{i18n.t("package.officialKeyExport")}</Button>
                </div>
                <div className="grid gap-2">
                  <SkillFieldLabel label={i18n.t("package.officialKeyBackupPath")}>
                    <input value={officialKeyBackupPath} onChange={(event) => setOfficialKeyBackupPath(event.target.value)} className="h-9 w-full rounded-md border border-border bg-card px-3 text-xs outline-none focus:border-primary" />
                  </SkillFieldLabel>
                  <SkillFieldLabel label={i18n.t("package.officialKeyPassphrase")}>
                    <input type="password" minLength={8} value={officialKeyImportPassphrase} onChange={(event) => setOfficialKeyImportPassphrase(event.target.value)} className="h-9 w-full rounded-md border border-border bg-card px-3 text-xs outline-none focus:border-primary" autoComplete="current-password" />
                  </SkillFieldLabel>
                  <Button type="button" variant="outline" className="h-8 w-fit text-xs" disabled={officialKeyBusy || officialKeyImportPassphrase.length < 8 || !officialKeyBackupPath.trim()} onClick={() => void runOfficialKeyImport()}>{i18n.t("package.officialKeyImport")}</Button>
                </div>
              </div>
            </div>
          </details>
        </details>

        <div ref={packageDropzoneRef} className={`grid gap-3 rounded-lg border p-3 transition-colors ${draggingPackage ? "border-primary bg-primary/5" : "border-border bg-background"}`} data-vrcforge-skill-dropzone>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <FileUp className="h-4 w-4 text-primary" />
            <span>{i18n.t("package.dropHint")}</span>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_auto]">
          <SkillFieldLabel label={i18n.t("package.packagePath")}>
            <input
              value={packagePath}
              onChange={(event) => setPackagePath(event.target.value)}
              className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
          </SkillFieldLabel>
          <Button type="button" variant="outline" className="self-end" disabled={loading || !packagePath.trim()} onClick={() => void runPreflight()}>
            <Eye className="h-4 w-4" />
            {i18n.t("package.preflight")}
          </Button>
          <Button type="button" className="self-end" disabled={loading || !packagePath.trim()} onClick={() => void runImport()}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {i18n.t("package.import")}
          </Button>
          </div>
        </div>

        {preview ? (
          <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{skillPackageTitle(preview)}</span>
              {skillPackageLabels(preview).map((label) => (
                <Badge key={label} title={skillPackageExecutionDescription(preview)} tone={skillPackageLabelTone(label)} className="h-6 shrink-0">
                  {label}
                </Badge>
              ))}
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <DataLine label="Version" value={String(preview.version || "-")} />
              <DataLine label={i18n.t("package.tableRisk")} value={skillPackageRiskLabel(preview)} />
              <DataLine label="Signer" value={skillPackageSigner(preview)} mono />
            </div>
            <SkillOutputBlock label="Permissions" value={skillPackagePermissions(preview).join("\n")} />
            {preview.governance ? <SkillOutputBlock label={i18n.t("package.governance")} value={formatPayload(preview.governance)} /> : null}
            {preview.dryRun ? <SkillOutputBlock label="Dry Run" value={formatPayload(preview.dryRun)} /> : null}
            {preview.manifest ? <SkillOutputBlock label="Manifest" value={formatPayload(preview.manifest)} /> : null}
          </div>
        ) : null}

        <details className="rounded-lg border border-border bg-background p-3">
          <summary className="cursor-pointer text-sm font-medium">{i18n.t("package.pathToSkillAdvanced")}</summary>
          <div className="mt-3">
            <PathToSkillCapturePanel
              key={pathToSkillDraftSeed?.revision ?? "manual"}
              initialSummary={pathToSkillDraftSeed?.summary}
              onPreview={onPreviewPathToSkill}
              onWrite={onWritePathToSkill}
            />
          </div>
        </details>

        <details className="rounded-lg border border-border bg-background p-3" open={showAdvanced} onToggle={(event) => setShowAdvanced(event.currentTarget.open)}>
          <summary className="cursor-pointer text-sm font-medium">{i18n.t("package.exportSection")}</summary>
          <div className="mt-3 grid gap-3">
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
            <SkillFieldLabel label={i18n.t("package.skillName")}>
              <input
                value={exportSkillName}
                onChange={(event) => setExportSkillName(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </SkillFieldLabel>
            <SkillFieldLabel label={i18n.t("package.outputPath")}>
              <input
                value={exportPath}
                onChange={(event) => setExportPath(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </SkillFieldLabel>
          </div>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto]">
            <SkillFieldLabel label={i18n.t("package.privateKeyPath")}>
              <input
                value={exportPrivateKeyPath}
                onChange={(event) => setExportPrivateKeyPath(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-card px-3 text-sm outline-none focus:border-primary"
              />
            </SkillFieldLabel>
            <label className="flex h-10 min-w-0 items-center gap-2 self-end rounded-md border border-border px-3 text-sm text-muted-foreground">
              <input type="checkbox" checked={releaseExport} onChange={(event) => setReleaseExport(event.target.checked)} />
              <span className="truncate">{i18n.t("skills.signedRelease")}</span>
            </label>
          </div>
          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              disabled={loading || !exportSkillName.trim() || !exportPath.trim() || (releaseExport && !exportPrivateKeyPath.trim())}
              onClick={() => void runExport()}
            >
              <Copy className="h-4 w-4" />
              {i18n.t("package.export")}
            </Button>
          </div>
          </div>
        </details>


      </div>
    </details>
  );
}

function SkillSwitch({ checked, busy, label, onChange }: { checked: boolean; busy: boolean; label: string; onChange: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      data-vrcforge-skill-package-enabled={checked ? "true" : "false"}
      disabled={busy}
      onClick={onChange}
      className={`relative h-7 w-12 shrink-0 overflow-hidden rounded-full border transition-colors ${checked ? "border-primary bg-primary" : "border-border bg-muted"} ${busy ? "cursor-not-allowed opacity-50" : ""}`}
    >
      <span className={`absolute left-0 top-1/2 h-5 w-5 -translate-y-1/2 rounded-full bg-white shadow-sm transition-transform ${checked ? "translate-x-[26px]" : "translate-x-[2px]"}`} />
    </button>
  );
}

function normalizeSkillPackagePreview(payload: SkillPackagePreflight | null): SkillPackageEntry | null {
  if (!payload) {
    return null;
  }
  return payload.preview || payload;
}

function skillPackageId(pkg: SkillPackageEntry): string {
  return String(pkg.id || pkg.name || pkg.manifest?.id || "-");
}

function skillPackageTitle(pkg: SkillPackageEntry): string {
  return String(pkg.title || pkg.name || pkg.manifest?.title || pkg.manifest?.name || skillPackageId(pkg));
}

function skillPackageRisk(pkg: SkillPackageEntry): string {
  return String(pkg.risk_level || pkg.riskLevel || "low");
}

function skillPackageRiskLabel(pkg: SkillPackageEntry): string {
  const risk = skillPackageRisk(pkg).toLowerCase();
  const key = risk === "high" || risk === "medium" || risk === "low" ? risk : "low";
  return i18n.t(`package.risk.${key}`);
}

function skillPackageMessageTone(message: string): "ok" | "warn" | "muted" {
  if (message === i18n.t("package.messages.packageEnabled")) return "ok";
  if (message === i18n.t("package.messages.packageDisabled")) return "muted";
  return "warn";
}

function skillPackageSigner(pkg: SkillPackageEntry): string {
  return String(pkg.signer_fingerprint || pkg.signerFingerprint || "-");
}

function skillPackagePackageSha(pkg: SkillPackageEntry): string {
  return String(pkg.package_sha256 || pkg.packageSha256 || "");
}

function skillPackageEnabled(pkg: SkillPackageEntry): boolean {
  return pkg.enabled !== false && pkg.available !== false;
}

function skillPackagePermissions(pkg: SkillPackageEntry): string[] {
  const permissions = pkg.permissions || [];
  const tiers = pkg.permission_tiers || pkg.permissionTiers || {};
  const tierValues = Object.entries(tiers).flatMap(([tier, items]) => (items || []).map((item) => `${tier}: ${item}`));
  return [...permissions, ...tierValues];
}

function skillPackageLabels(pkg: SkillPackageEntry): string[] {
  const labels: string[] = [];
  labels.push(i18n.t(`package.executionMode.${skillPackageExecutionMode(pkg)}`));
  const status = String(pkg.signature_status || pkg.signatureStatus || "").toLowerCase();
  const errorText = [...(pkg.errors || []), ...(pkg.warnings || [])].join(" ").toLowerCase();
  const governance = skillPackageGovernance(pkg);
  const signerStatus = String(governance.signerTrustStatus || governance.signer_trust_status || "").toLowerCase();
  const importAllowed = governance.importAllowed ?? governance.import_allowed;
  const enableAllowed = governance.enableAllowed ?? governance.enable_allowed;
  const safeMode = skillPackageSafeMode(governance);
  if (pkg.source === "builtin") {
    labels.push("Built-in");
  }
  if (pkg.official === true) {
    labels.push("Official");
  }
  if (status === "signed") {
    labels.push("Signed");
  } else if (status === "dev") {
    labels.push("Dev");
  } else {
    labels.push("Unsigned");
  }
  if (errorText.includes("signature")) {
    labels.push("Signature mismatch");
  }
  if (signerStatus === "trusted") {
    labels.push("Trusted signer");
  } else if (signerStatus === "revoked") {
    labels.push("Revoked signer");
  } else if (signerStatus === "untrusted") {
    labels.push("Untrusted signer");
  }
  if (safeMode.defaultEnabled === false || safeMode.disablesRiskLevel === true || safeMode.disables_risk_level === true) {
    labels.push("Safe Mode disabled");
  }
  if (importAllowed === false) {
    labels.push("Import blocked");
  }
  if (enableAllowed === false) {
    labels.push("Enable blocked");
  }
  if (pkg.dryRun) {
    labels.push("Dry run");
  }
  if (pkg.enabled === false || pkg.available === false || errorText.includes("blocked")) {
    labels.push("Blocked");
  }
  return [...new Set(labels)];
}

function skillPackageExecutionMode(pkg: SkillPackageEntry): "deterministic" | "agentic" {
  const manifest = pkg.manifest || {};
  const workflow = (manifest.workflow && typeof manifest.workflow === "object" ? manifest.workflow : {}) as Record<string, unknown>;
  const raw = String(pkg.execution || manifest.execution || pkg.executionMode || pkg.execution_mode || manifest.executionMode || manifest.execution_mode || workflow.executionMode || workflow.execution_mode || "").toLowerCase();
  return ["deterministic", "fixed", "fixed_steps", "fixed-steps"].includes(raw) ? "deterministic" : "agentic";
}

function skillPackageExecutionDescription(pkg: SkillPackageEntry): string {
  const manifestEntrypoints = pkg.manifest && typeof pkg.manifest.entrypoints === "object"
    ? (pkg.manifest.entrypoints as Record<string, unknown>)
    : {};
  return i18n.t(`package.executionMode.${skillPackageExecutionMode(pkg)}Description`, {
    hasPlan: Boolean(pkg.entrypoints?.executionPlan || manifestEntrypoints.executionPlan),
  });
}

function skillPackageLabelTone(label: string): "ok" | "warn" | "danger" | "muted" {
  if (label === "Signed" || label === "Built-in" || label === "Official" || label === "Trusted signer") {
    return "ok";
  }
  if (label === "Signature mismatch" || label === "Blocked" || label === "Revoked signer" || label.includes("blocked")) {
    return "danger";
  }
  if (label === "Unsigned" || label === "Dev" || label === "Untrusted signer" || label === "Safe Mode disabled") {
    return "warn";
  }
  return "muted";
}

function skillPackageGovernance(pkg: SkillPackageEntry): Record<string, unknown> {
  return asRecord(pkg.governance) || {};
}

function skillPackageSafeMode(governance: Record<string, unknown>): Record<string, unknown> {
  return asRecord(governance.safeMode) || asRecord(governance.safe_mode) || {};
}

function skillPackageSafeModeEnabled(governance: Record<string, unknown>): boolean {
  const safeMode = skillPackageSafeMode(governance);
  return safeMode.enabled === true;
}

function SkillFieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-2 text-sm">
      <span className="truncate font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function SkillOutputBlock({ label, value, danger = false }: { label: string; value: string; danger?: boolean }) {
  return (
    <div className={danger ? "rounded-lg border border-destructive/40 bg-destructive/5 p-3" : "rounded-lg border border-border bg-background p-3"}>
      <div className="mb-2 text-xs font-medium text-muted-foreground">{label}</div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed">{value}</pre>
    </div>
  );
}

function formatPayload(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "string") {
    return value;
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}
