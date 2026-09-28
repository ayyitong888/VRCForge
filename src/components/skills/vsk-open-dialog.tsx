import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { SkillPackageEntry, SkillPackagePreflight } from "../../lib/api";
import { hasTauriInternals } from "../../lib/api/http";
import { displayVskPath, vskPreviewIdentity } from "../../lib/vsk-open-presentation";
import { Button } from "../ui/button";

/** File-open requests are untrusted data, not permission to install a package. */
export function VskOpenDialog({ ready, onPreflight, onImport }: {
  ready: boolean;
  onPreflight: (path: string) => Promise<SkillPackagePreflight>;
  onImport: (path: string) => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [paths, setPaths] = useState<string[]>([]);
  const [preview, setPreview] = useState<SkillPackageEntry | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [imported, setImported] = useState(false);
  const busyRef = useRef(false);
  const verifiedPath = useRef("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const callbacks = useRef({ onPreflight, onImport });
  callbacks.current = { onPreflight, onImport };
  const path = paths[0] || "";

  useEffect(() => {
    if (!ready || !hasTauriInternals()) return;
    let active = true;
    let unlisten: (() => void) | undefined;
    let draining = false;
    let again = false;
    const drain = async () => {
      if (draining) { again = true; return; }
      draining = true;
      try {
        do {
          again = false;
          const pending = await invoke<string[]>("take_pending_vsk_paths");
          if (active) setPaths((current) => [...new Set([...current, ...pending])].slice(0, 32));
        } while (active && again);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : String(cause));
      } finally { draining = false; }
    };
    // Subscribe before draining: a second launch during frontend startup cannot
    // disappear between the initial read and event registration.
    void listen("vrcforge:vsk-open", () => { void drain(); }).then((cleanup) => {
      if (!active) { cleanup(); return; }
      unlisten = cleanup;
      void drain();
    }).catch((cause) => { if (active) setError(String(cause)); });
    return () => { active = false; unlisten?.(); };
  }, [ready]);

  useEffect(() => {
    if (!path) return;
    let active = true;
    setPreview(null);
    verifiedPath.current = "";
    setError("");
    setImported(false);
    dialogRef.current?.showModal();
    dialogRef.current?.querySelector<HTMLButtonElement>("[data-vsk-cancel]")?.focus();
    // Read-only verification is the ONLY action performed by opening a file.
    void callbacks.current.onPreflight(path).then((result) => {
      if (!active) return;
      const entry = result.preview || result;
      if (result.ok !== true || (entry.errors?.length ?? 0) > 0) {
        setError(entry.errors?.join("\n") || t("vskOpen.invalid"));
      } else { verifiedPath.current = path; setPreview(entry); }
    }).catch((cause) => {
      if (active) setError(cause instanceof Error ? cause.message : String(cause));
    });
    return () => { active = false; };
  }, [path]);

  function dismiss() {
    if (busyRef.current) return;
    verifiedPath.current = "";
    dialogRef.current?.close();
    setPaths((current) => current.slice(1));
  }
  async function confirmImport() {
    if (!path || verifiedPath.current !== path || !preview || error || imported || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      await callbacks.current.onImport(path);
      setImported(true);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { busyRef.current = false; setBusy(false); }
  }
  if (!path) return null;
  const manifest = preview?.manifest && typeof preview.manifest === "object" ? preview.manifest : {};
  const governance = preview?.governance && typeof preview.governance === "object" ? preview.governance : {};
  const signatureStatus = String(preview?.signature_status || preview?.signatureStatus || "").trim();
  const signerTrustStatus = String(governance.signerTrustStatus || governance.signer_trust_status || "").trim();
  const signerFingerprint = String(preview?.signer_fingerprint || preview?.signerFingerprint || "").trim();
  const official = governance.official === true || preview?.official === true;
  const officialPublisher = String(
    (governance.official === true ? governance.officialPublisher : undefined)
      || (preview?.official === true ? preview.officialPublisher : "")
      || "",
  ).trim();
  const showOfficialPublisher = official && Boolean(officialPublisher);
  const permissions = Array.isArray(preview?.permissions) ? preview.permissions : [];
  const authorName = [manifest.author_display_name, manifest.author].find(
    (value): value is string => typeof value === "string" && value.trim().length > 0,
  );
  const knownPermissions = new Set([
    "read_project", "read_assets", "read_package", "analyze_logs", "build_index",
    "unity_scan_scene", "unity_modify_materials", "unity_modify_prefab", "unity_modify_components", "unity_run_validation",
    "write_project_files", "delete_files", "execute_shell", "run_editor_script", "network_access", "read_env", "write_outside_project",
  ]);
  const trustStatuses = new Set(["trusted", "untrusted", "revoked", "unsigned_dev"]);
  const signatureStatuses = new Set(["signed", "dev"]);
  return (
    <dialog ref={dialogRef} aria-labelledby="vsk-open-title"
      className="m-auto max-h-[85vh] w-[min(92vw,36rem)] overflow-y-auto rounded-xl border border-border bg-card p-6 text-foreground shadow-panel backdrop:bg-black/55"
      onCancel={(event) => { event.preventDefault(); dismiss(); }}>
      <h2 id="vsk-open-title" className="text-lg font-semibold">{t("vskOpen.title")}</h2>
      <p className="mt-2 text-sm text-muted-foreground">{t("vskOpen.description")}</p>
      <details className="mt-3 text-xs">
        <summary className="cursor-pointer text-muted-foreground">{t("vskOpen.details")}</summary>
        <div className="mt-2 space-y-2">
          <div><span className="text-muted-foreground">{t("vskOpen.path")}</span><code className="mt-1 block break-all">{displayVskPath(path)}</code></div>
          {preview && <div><span className="text-muted-foreground">{t("vskOpen.signerFingerprint")}</span><code className="mt-1 block break-all font-mono">{signerFingerprint || t("vskOpen.signerTrustUnknown")}</code></div>}
        </div>
      </details>
      {preview && <dl className="mt-4 space-y-3 text-sm">
        <div><dt className="text-muted-foreground">{t("vskOpen.name")}</dt><dd className="font-semibold">{vskPreviewIdentity(preview).name}</dd></div>
        <div><dt className="text-muted-foreground">{t("vskOpen.version")}</dt><dd className="font-semibold">{vskPreviewIdentity(preview).version}</dd></div>
        <div><dt className="text-muted-foreground">{t("vskOpen.author")}</dt><dd className="font-semibold">{authorName || t("vskOpen.notProvided")}</dd></div>
        <div>
          <dt className="text-muted-foreground">{t("vskOpen.signature")}</dt>
          <dd className="font-semibold">{signatureStatuses.has(signatureStatus.toLowerCase())
            ? t(`vskOpen.signatureStatus.${signatureStatus.toLowerCase()}`)
            : signatureStatus || "—"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("vskOpen.signerTrust")}</dt>
          <dd>{trustStatuses.has(signerTrustStatus.toLowerCase())
            ? t(`vskOpen.signerTrustStatus.${signerTrustStatus.toLowerCase()}`)
            : signerTrustStatus || t("vskOpen.signerTrustUnknown")}</dd>
        </div>
        {showOfficialPublisher && <div><dt className="text-muted-foreground">{t("vskOpen.officialPublisher")}</dt><dd className="font-semibold">{officialPublisher}</dd></div>}
        <div>
          <dt className="text-muted-foreground">{t("vskOpen.permissions")}</dt>
          {permissions.length ? <ul className="mt-1 list-disc space-y-1 pl-5">{permissions.map((permission, index) => {
            const rawPermission = String(permission);
            return <li key={`${rawPermission}-${index}`}>{knownPermissions.has(rawPermission)
              ? t(`vskOpen.permission.${rawPermission}`)
              : t("vskOpen.unknownPermission", { permission: rawPermission })}</li>;
          })}</ul> : <dd>—</dd>}
        </div>
        {preview.warnings?.map((warning, index) => <p key={index}>{warning}</p>)}
      </dl>}
      {!preview && !error && <p role="status" className="mt-3">{t("vskOpen.checking")}</p>}
      {error && <p role="alert" className="mt-3 whitespace-pre-wrap text-destructive">{error}</p>}
      {imported && <p role="status" className="mt-3">{t("vskOpen.imported")}</p>}
      <div className="mt-5 flex justify-end gap-2">
        <Button data-vsk-cancel type="button" variant="secondary" disabled={busy} onClick={dismiss}>
          {t(imported ? "common.close" : "common.cancel")}
        </Button>
        {!imported && <Button type="button" disabled={!preview || !!error || busy} onClick={() => { void confirmImport(); }}>
          {t(busy ? "vskOpen.importing" : "vskOpen.confirm")}
        </Button>}
      </div>
    </dialog>
  );
}
