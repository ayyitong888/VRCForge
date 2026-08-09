import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { TFunction } from "i18next";
import type { ActiveView } from "../lib/app-view";
import {
  blockSkillPackage,
  checkSkills,
  createSkill,
  deleteSkill,
  exportSkillPackage,
  fetchSkillPackages,
  fetchSkills,
  importSkillPackage,
  preflightSkillPackage,
  previewPathToSkill,
  revokeSkillPackageSigner,
  setSkillPackageEnabled,
  setSkillPackageSafeMode,
  trustSkillPackageSigner,
  uninstallSkillPackage,
  updateSkill,
  writePathToSkill,
} from "../lib/api";
import type {
  AgentSkill,
  AgentSkillCheck,
  AgentSkillRegistry,
  PathToSkillCaptureRequest,
  PathToSkillCaptureResult,
  SkillPackageEntry,
} from "../lib/api";
import type { PathToSkillDraftSeed, PathToSkillOperationSummary } from "../lib/path-to-skill-context";
import { LatestForegroundRequestGate } from "../lib/request-priority";
import { emptySkillDraft } from "../lib/skill-draft";

type UseSkillsWorkspaceControllerParams = {
  endpoint: string;
  runtimeConnected: boolean;
  bootstrapSkills: AgentSkill[];
  activeView: ActiveView;
  setActiveView: (view: ActiveView) => void;
  startRuntime: () => Promise<string | null>;
  refresh: (target?: string) => Promise<void>;
  setError: (message: string) => void;
  t: TFunction;
};

const SKILL_REGISTRY_BACKGROUND_REFRESH_MS = 30_000;

export function useSkillsWorkspaceController({
  endpoint,
  runtimeConnected,
  bootstrapSkills,
  setActiveView,
  startRuntime,
  refresh,
  setError,
  t,
}: UseSkillsWorkspaceControllerParams) {
  const [skillRegistry, setSkillRegistry] = useState<AgentSkillRegistry | null>(null);
  const [skillCheck, setSkillCheck] = useState<AgentSkillCheck | null>(null);
  const [selectedSkillName, setSelectedSkillName] = useState("");
  const [skillDraft, setSkillDraft] = useState<Partial<AgentSkill>>(emptySkillDraft());
  const [savingSkill, setSavingSkill] = useState(false);
  const [skillPackages, setSkillPackages] = useState<SkillPackageEntry[]>([]);
  const [skillPackageStore, setSkillPackageStore] = useState("");
  const [skillPackageGovernance, setSkillPackageGovernance] = useState<Record<string, unknown>>({});
  const [skillPackageAudit, setSkillPackageAudit] = useState<Array<Record<string, unknown>>>([]);
  const [loadingSkillPackages, setLoadingSkillPackages] = useState(false);
  const [skillPackageMessage, setSkillPackageMessage] = useState("");
  const [skillPackageError, setSkillPackageError] = useState("");
  const [pathToSkillDraftSeed, setPathToSkillDraftSeed] = useState<PathToSkillDraftSeed | null>(null);
  const skillRegistryRequestGateRef = useRef(new LatestForegroundRequestGate());

  const skills = useMemo(() => skillRegistry?.skills ?? bootstrapSkills, [bootstrapSkills, skillRegistry]);
  const skillCount = skillRegistry?.count ?? skills.length;

  useEffect(() => {
    if (!runtimeConnected) {
      return;
    }
    let active = true;
    const refreshRegistry = () => {
      const token = skillRegistryRequestGateRef.current.beginBackground();
      if (token === null) {
        return;
      }
      void fetchSkills(endpoint)
        .then((payload) => {
          if (active && skillRegistryRequestGateRef.current.isCurrent(token)) {
            setSkillRegistry(payload);
          }
        })
        .catch(() => {
          // Startup keeps the chat surface usable; opening Skills retries visibly.
        });
    };
    refreshRegistry();
    const timer = window.setInterval(refreshRegistry, SKILL_REGISTRY_BACKGROUND_REFRESH_MS);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [endpoint, runtimeConnected]);

  async function openSkills(options?: { preserveCapturedPath?: boolean }) {
    const registryToken = skillRegistryRequestGateRef.current.beginForeground();
    if (!options?.preserveCapturedPath) {
      setPathToSkillDraftSeed(null);
    }
    setActiveView("skills");
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const [payload, , check] = await Promise.all([
        fetchSkills(targetEndpoint),
        loadSkillPackages(targetEndpoint),
        checkSkills(targetEndpoint),
      ]);
      if (!skillRegistryRequestGateRef.current.isCurrent(registryToken)) {
        return;
      }
      setSkillRegistry(payload);
      setSkillCheck(check);
      if (!selectedSkillName && payload.skills.length > 0) {
        const firstUserSkill = payload.skills.find((skill) => skill.source === "user") || payload.skills[0];
        selectSkill(firstUserSkill);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
    }
  }

  async function openSkillsWithCapturedPath(summary: PathToSkillOperationSummary) {
    setPathToSkillDraftSeed((current) => ({
      revision: (current?.revision ?? 0) + 1,
      summary,
    }));
    await openSkills({ preserveCapturedPath: true });
  }

  async function loadSkillPackages(target = endpoint) {
    setLoadingSkillPackages(true);
    setSkillPackageError("");
    try {
      let targetEndpoint = target;
      if (!runtimeConnected && target === endpoint) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return null;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = await fetchSkillPackages(targetEndpoint);
      setSkillPackages(payload.installed || []);
      setSkillPackageStore(payload.store || "");
      setSkillPackageGovernance((payload.governance || {}) as Record<string, unknown>);
      setSkillPackageAudit(payload.audit || []);
      return payload;
    } catch (cause) {
      setSkillPackageError(cause instanceof Error ? cause.message : String(cause));
      return null;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function preflightVskPackage(packagePath: string) {
    setSkillPackageMessage("");
    setSkillPackageError("");
    const payload = await preflightSkillPackage(endpoint, { packagePath });
    setSkillPackageMessage("Package preflight completed");
    return payload;
  }

  async function importVskPackage(packagePath: string) {
    const registryToken = skillRegistryRequestGateRef.current.beginAuthoritative();
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await importSkillPackage(endpoint, { packagePath });
      setSkillPackageMessage(payload.changed === false ? "Package already installed" : t("package.messages.packageImported"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      const committedRevision = skillRegistryRequestGateRef.current.commitAuthoritative(registryToken);
      if (committedRevision !== null) {
        setSkillRegistry(skillsPayload);
      }
      const check = await checkSkills(endpoint);
      if (committedRevision !== null && skillRegistryRequestGateRef.current.isCurrent(committedRevision)) {
        setSkillCheck(check);
      }
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
      setLoadingSkillPackages(false);
    }
  }

  async function exportVskPackage(skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await exportSkillPackage(endpoint, { skillName, outputPath, release, privateKeyPath: privateKeyPath || undefined });
      setSkillPackageMessage(release ? t("package.messages.releaseExported") : t("package.messages.devExported"));
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function previewCapturedPath(request: PathToSkillCaptureRequest): Promise<PathToSkillCaptureResult> {
    let targetEndpoint = endpoint;
    if (!runtimeConnected) {
      const readyEndpoint = await startRuntime();
      if (!readyEndpoint) {
        throw new Error(t("package.pathToSkill.runtimeUnavailable"));
      }
      targetEndpoint = readyEndpoint;
    }
    return previewPathToSkill(targetEndpoint, request);
  }

  async function writeCapturedPath(request: PathToSkillCaptureRequest): Promise<PathToSkillCaptureResult> {
    let targetEndpoint = endpoint;
    if (!runtimeConnected) {
      const readyEndpoint = await startRuntime();
      if (!readyEndpoint) {
        throw new Error(t("package.pathToSkill.runtimeUnavailable"));
      }
      targetEndpoint = readyEndpoint;
    }
    return writePathToSkill(targetEndpoint, request);
  }

  async function setVskPackageEnabled(skillPackageId: string, enabled: boolean) {
    const registryToken = skillRegistryRequestGateRef.current.beginAuthoritative();
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await setSkillPackageEnabled(endpoint, skillPackageId, { enabled, syncProjectedSkill: true });
      setSkillPackageMessage(enabled ? t("package.messages.packageEnabled") : t("package.messages.packageDisabled"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      const committedRevision = skillRegistryRequestGateRef.current.commitAuthoritative(registryToken);
      if (committedRevision !== null) {
        setSkillRegistry(skillsPayload);
      }
      const check = await checkSkills(endpoint);
      if (committedRevision !== null && skillRegistryRequestGateRef.current.isCurrent(committedRevision)) {
        setSkillCheck(check);
      }
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
      setLoadingSkillPackages(false);
    }
  }

  async function uninstallVskPackage(skillPackageId: string) {
    const registryToken = skillRegistryRequestGateRef.current.beginAuthoritative();
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await uninstallSkillPackage(endpoint, skillPackageId, { removeProjectedSkill: true });
      setSkillPackageMessage(t("package.messages.packageUninstalled"));
      const [skillsPayload] = await Promise.all([fetchSkills(endpoint), loadSkillPackages(endpoint)]);
      const committedRevision = skillRegistryRequestGateRef.current.commitAuthoritative(registryToken);
      if (committedRevision !== null) {
        setSkillRegistry(skillsPayload);
      }
      const check = await checkSkills(endpoint);
      if (committedRevision !== null && skillRegistryRequestGateRef.current.isCurrent(committedRevision)) {
        setSkillCheck(check);
      }
      await refresh(endpoint);
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
      setLoadingSkillPackages(false);
    }
  }

  async function refreshSkillWorkspaceState() {
    const registryToken = skillRegistryRequestGateRef.current.beginForeground();
    try {
      const [skillsPayload, , check] = await Promise.all([
        fetchSkills(endpoint),
        loadSkillPackages(endpoint),
        checkSkills(endpoint),
      ]);
      if (skillRegistryRequestGateRef.current.isCurrent(registryToken)) {
        setSkillRegistry(skillsPayload);
        setSkillCheck(check);
      }
      await refresh(endpoint);
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
    }
  }

  async function setVskPackageSafeMode(enabled: boolean, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await setSkillPackageSafeMode(endpoint, { enabled, reason: reason || undefined });
      setSkillPackageMessage(enabled ? t("package.messages.safeModeEnabled") : t("package.labels.safeModeDisabled"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function trustVskPackageSigner(signerFingerprint: string, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await trustSkillPackageSigner(endpoint, { signerFingerprint, reason: reason || undefined });
      setSkillPackageMessage(t("package.messages.signerTrusted"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function revokeVskPackageSigner(signerFingerprint: string, reason?: string) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await revokeSkillPackageSigner(endpoint, { signerFingerprint, reason: reason || undefined });
      setSkillPackageMessage(t("package.messages.signerRevoked"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  async function blockVskPackage(request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) {
    setLoadingSkillPackages(true);
    setSkillPackageMessage("");
    setSkillPackageError("");
    try {
      const payload = await blockSkillPackage(endpoint, request);
      setSkillPackageMessage(t("package.messages.packageBlocked"));
      await refreshSkillWorkspaceState();
      return payload;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setSkillPackageError(message);
      throw cause;
    } finally {
      setLoadingSkillPackages(false);
    }
  }

  function selectSkill(skill: AgentSkill) {
    setSelectedSkillName(skill.name);
    setSkillDraft({ ...skill });
  }

  function newSkill() {
    setSelectedSkillName("");
    setSkillDraft(emptySkillDraft());
  }

  async function runSkillCheck() {
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      setSkillCheck(await checkSkills(targetEndpoint));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function saveSkill(event?: FormEvent) {
    event?.preventDefault();
    if (!skillDraft.name || savingSkill) {
      return;
    }
    setSavingSkill(true);
    const registryToken = skillRegistryRequestGateRef.current.beginAuthoritative();
    setError("");
    try {
      let targetEndpoint = endpoint;
      if (!runtimeConnected) {
        const readyEndpoint = await startRuntime();
        if (!readyEndpoint) {
          return;
        }
        targetEndpoint = readyEndpoint;
      }
      const payload = selectedSkillName
        ? await updateSkill(targetEndpoint, selectedSkillName, skillDraft)
        : await createSkill(targetEndpoint, skillDraft);
      const committedRevision = skillRegistryRequestGateRef.current.commitAuthoritative(registryToken);
      if (committedRevision !== null) {
        setSkillRegistry(payload);
      }
      const check = await checkSkills(targetEndpoint);
      if (committedRevision !== null && skillRegistryRequestGateRef.current.isCurrent(committedRevision)) {
        setSkillCheck(check);
        setSelectedSkillName(payload.skill.name);
        setSkillDraft({ ...payload.skill });
      }
      await refresh(targetEndpoint);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
      setSavingSkill(false);
    }
  }

  async function removeSelectedSkill() {
    if (!selectedSkillName || savingSkill) {
      return;
    }
    setSavingSkill(true);
    const registryToken = skillRegistryRequestGateRef.current.beginAuthoritative();
    setError("");
    try {
      const payload = await deleteSkill(endpoint, selectedSkillName);
      const committedRevision = skillRegistryRequestGateRef.current.commitAuthoritative(registryToken);
      if (committedRevision !== null) {
        setSkillRegistry(payload);
      }
      const check = await checkSkills(endpoint);
      if (committedRevision !== null && skillRegistryRequestGateRef.current.isCurrent(committedRevision)) {
        setSkillCheck(check);
        setSelectedSkillName("");
        setSkillDraft(emptySkillDraft());
      }
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      skillRegistryRequestGateRef.current.endForeground(registryToken);
      setSavingSkill(false);
    }
  }

  return {
    skills,
    skillCount,
    skillCheck,
    selectedSkillName,
    skillDraft,
    savingSkill,
    skillPackages,
    skillPackageStore,
    skillPackageGovernance,
    skillPackageAudit,
    loadingSkillPackages,
    skillPackageMessage,
    skillPackageError,
    pathToSkillDraftSeed,
    openSkills,
    openSkillsWithCapturedPath,
    loadSkillPackages,
    preflightVskPackage,
    importVskPackage,
    exportVskPackage,
    previewCapturedPath,
    writeCapturedPath,
    setVskPackageEnabled,
    uninstallVskPackage,
    setVskPackageSafeMode,
    trustVskPackageSigner,
    revokeVskPackageSigner,
    blockVskPackage,
    selectSkill,
    newSkill,
    runSkillCheck,
    saveSkill,
    removeSelectedSkill,
    setSkillDraft,
  };
}
