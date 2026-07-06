import { Check, Copy, Eye, EyeOff, Loader2, Plus, RefreshCw, Shield, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import i18n from "../../i18n";
import type { SkillPackageEntry, SkillPackagePreflight } from "../../lib/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

export function SkillPackageManagerPanel({
  packages,
  packageStore,
  loading,
  message,
  error,
  governance,
  audit,
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
}: {
  packages: SkillPackageEntry[];
  packageStore: string;
  loading: boolean;
  message: string;
  error: string;
  governance: Record<string, unknown>;
  audit: Array<Record<string, unknown>>;
  onRefresh: () => void;
  onPreflight: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImport: (packagePath: string) => Promise<unknown>;
  onExport: (skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) => Promise<unknown>;
  onSetEnabled: (skillPackageId: string, enabled: boolean) => Promise<unknown>;
  onUninstall: (skillPackageId: string) => Promise<unknown>;
  onSetSafeMode: (enabled: boolean, reason?: string) => Promise<unknown>;
  onTrustSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onRevokeSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onBlockPackage: (request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) => Promise<unknown>;
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
  const preview = normalizeSkillPackagePreview(preflight);
  const safeModeEnabled = skillPackageSafeModeEnabled(governance);
  const auditTail = audit.slice(-3).reverse();
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
    <section className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
      <div className="mb-5 flex min-w-0 items-center gap-2">
        <Shield className="h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 truncate text-sm font-semibold">{i18n.t("package.title")}</div>
        <Badge tone="muted" className="shrink-0">
          {packages.length}
        </Badge>
        <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onRefresh} disabled={loading}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
        </Button>
      </div>

      <div className="grid gap-4">
        <div className="grid gap-3">
          <DataLine label={i18n.t("package.store")} value={packageStore || "-"} />
          {displayMessage ? <Badge tone="ok" className="w-fit">{displayMessage}</Badge> : null}
          {displayError ? <div className="rounded-md border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">{displayError}</div> : null}
        </div>

        <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
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
          {auditTail.length ? (
            <div className="grid gap-1 border-t border-border pt-3 text-xs text-muted-foreground">
              {auditTail.map((item, index) => (
                <div key={`${String(item.event || i18n.t("package.audit"))}-${index}`} className="flex min-w-0 gap-2">
                  <span className="shrink-0 font-mono">{String(item.event || i18n.t("package.audit"))}</span>
                  <span className="min-w-0 truncate">{String(item.skill_id || item.signer_fingerprint || item.package_id || item.reason || "")}</span>
                </div>
              ))}
            </div>
          ) : null}
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
            Import
          </Button>
        </div>

        {preview ? (
          <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{skillPackageTitle(preview)}</span>
              {skillPackageLabels(preview).map((label) => (
                <Badge key={label} tone={skillPackageLabelTone(label)} className="h-6 shrink-0">
                  {label}
                </Badge>
              ))}
            </div>
            <div className="grid gap-2 md:grid-cols-3">
              <DataLine label="Version" value={String(preview.version || "-")} />
              <DataLine label={i18n.t("package.tableRisk")} value={skillPackageRisk(preview)} />
              <DataLine label="Signer" value={skillPackageSigner(preview)} mono />
            </div>
            <SkillOutputBlock label="Permissions" value={skillPackagePermissions(preview).join("\n")} />
            {preview.governance ? <SkillOutputBlock label={i18n.t("package.governance")} value={formatPayload(preview.governance)} /> : null}
            {preview.dryRun ? <SkillOutputBlock label="Dry Run" value={formatPayload(preview.dryRun)} /> : null}
            {preview.manifest ? <SkillOutputBlock label="Manifest" value={formatPayload(preview.manifest)} /> : null}
          </div>
        ) : null}

        <div className="grid gap-3 rounded-lg border border-border bg-background p-3">
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

        <div className="overflow-hidden rounded-lg border border-border">
          <div className="grid grid-cols-[minmax(0,1fr)_76px_150px_minmax(300px,390px)] gap-2 border-b border-border bg-muted/40 px-3 py-2 text-xs font-medium text-muted-foreground">
            <span className="truncate">{i18n.t("subagent.roles.outfitPackageInspection")}</span>
            <span className="truncate">{i18n.t("package.tableRisk")}</span>
            <span className="truncate">{i18n.t("connector.status")}</span>
            <span className="truncate">{i18n.t("package.tableActions")}</span>
          </div>
          {packages.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-muted-foreground">{i18n.t("package.noPackages")}</div>
          ) : null}
          {packages.map((pkg, index) => {
            const id = skillPackageId(pkg);
            const enabled = skillPackageEnabled(pkg);
            const busy = loading || packageActionId === id;
            return (
              <div key={`${id}-${index}`} className="grid grid-cols-[minmax(0,1fr)_76px_150px_minmax(300px,390px)] gap-2 border-b border-border/60 px-3 py-2 text-xs last:border-b-0">
                <div className="min-w-0">
                  <div className="truncate font-medium">{skillPackageTitle(pkg)}</div>
                  <div className="truncate text-muted-foreground">{id}</div>
                </div>
                <span className="truncate">{skillPackageRisk(pkg)}</span>
                <div className="flex min-w-0 flex-wrap gap-1">
                  {skillPackageLabels(pkg).map((label) => (
                    <Badge key={label} tone={skillPackageLabelTone(label)} className="h-5 px-1.5 text-[10px]">
                      {label}
                    </Badge>
                  ))}
                </div>
                <div className="flex min-w-0 flex-wrap justify-end gap-1">
                  <Button
                    type="button"
                    variant="outline"
                    className="h-8 px-2 text-xs"
                    disabled={busy || id === "-"}
                    onClick={() => void runSetEnabled(id, !enabled)}
                  >
                    {packageActionId === id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : enabled ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    {enabled ? i18n.t("package.disable") : i18n.t("package.enable")}
                  </Button>
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
      </div>
    </section>
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
  return String(pkg.title || pkg.manifest?.title || skillPackageId(pkg));
}

function skillPackageRisk(pkg: SkillPackageEntry): string {
  return String(pkg.risk_level || pkg.riskLevel || "low");
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

function skillPackageLabelTone(label: string): "ok" | "warn" | "danger" | "muted" {
  if (label === "Signed" || label === "Built-in" || label === "Trusted signer") {
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
