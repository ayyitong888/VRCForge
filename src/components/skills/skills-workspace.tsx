import { ChevronDown, Loader2, Search, Wrench } from "lucide-react";
import { type FormEvent, type ReactNode, useMemo, useState } from "react";
import i18n from "../../i18n";
import type {
  AgentSkill,
  AgentSkillCheck,
  PathToSkillCaptureRequest,
  PathToSkillCaptureResult,
  SkillPackageEntry,
  SkillPackagePreflight,
} from "../../lib/api";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { SkillPackageManagerPanel } from "./skill-package-manager-panel";

const SKILL_DOMAIN_RULES: Array<{ label: string; pattern: RegExp }> = [
  { label: i18n.t("skills.domains.roslyn"), pattern: /roslyn/i },
  { label: i18n.t("skills.domains.face"), pattern: /blendshape|face|expression/i },
  { label: i18n.t("skills.domains.material"), pattern: /material|shader|texture/i },
  { label: i18n.t("skills.domains.clothing"), pattern: /clothing|outfit|wardrobe|gesture|\bfx\b|fx_/i },
  { label: i18n.t("skills.domains.parameter"), pattern: /parameter|param_/i },
  { label: i18n.t("skills.domains.screenshot"), pattern: /screenshot|capture|scene_view|vision|game_view/i },
  { label: i18n.t("skills.domains.package"), pattern: /package|vpm|addon|modular/i },
  { label: i18n.t("skills.domains.approval"), pattern: /approval|approve|backup|restore|rollback/i },
  { label: i18n.t("skills.domains.shell"), pattern: /shell|command|console|debug/i },
  { label: i18n.t("skills.domains.diagnostics"), pattern: /\blog|health|diagno|status|check/i },
  { label: i18n.t("skills.domains.avatarScan"), pattern: /scan|avatar|inventory|control|animation|toggle/i },
];
const SKILL_DOMAIN_FALLBACK = "skills.domainFallback";
const SKILL_DOMAIN_ORDER = [...SKILL_DOMAIN_RULES.map((rule) => rule.label), SKILL_DOMAIN_FALLBACK];

function skillDomain(skill: AgentSkill): string {
  const haystack = `${skill.name} ${skill.title || ""} ${skill.category || ""} ${skill.description || ""}`;
  for (const rule of SKILL_DOMAIN_RULES) {
    if (rule.pattern.test(haystack)) {
      return rule.label;
    }
  }
  return SKILL_DOMAIN_FALLBACK;
}



function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}



function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}



export function SkillsWorkspace({
  skills,
  skillCount,
  skillCheck,
  selectedSkillName,
  draft,
  saving,
  onSelect,
  onNew,
  onCheck,
  onDraftChange,
  onSave,
  onDelete,
  packages,
  packageStore,
  packagesLoading,
  packageMessage,
  packageError,
  packageGovernance,
  packageAudit,
  onRefreshPackages,
  onPreflightPackage,
  onImportPackage,
  onExportPackage,
  onSetPackageEnabled,
  onUninstallPackage,
  onSetSafeMode,
  onTrustSigner,
  onRevokeSigner,
  onBlockPackage,
  onPreviewPathToSkill,
  onWritePathToSkill,
}: {
  skills: AgentSkill[];
  skillCount: number;
  skillCheck: AgentSkillCheck | null;
  selectedSkillName: string;
  draft: Partial<AgentSkill>;
  saving: boolean;
  onSelect: (skill: AgentSkill) => void;
  onNew: () => void;
  onCheck: () => void;
  onDraftChange: (skill: Partial<AgentSkill>) => void;
  onSave: (event?: FormEvent) => void;
  onDelete: () => void;
  packages: SkillPackageEntry[];
  packageStore: string;
  packagesLoading: boolean;
  packageMessage: string;
  packageError: string;
  packageGovernance: Record<string, unknown>;
  packageAudit: Array<Record<string, unknown>>;
  onRefreshPackages: () => void;
  onPreflightPackage: (packagePath: string) => Promise<SkillPackagePreflight>;
  onImportPackage: (packagePath: string) => Promise<unknown>;
  onExportPackage: (skillName: string, outputPath: string, release: boolean, privateKeyPath?: string) => Promise<unknown>;
  onSetPackageEnabled: (skillPackageId: string, enabled: boolean) => Promise<unknown>;
  onUninstallPackage: (skillPackageId: string) => Promise<unknown>;
  onSetSafeMode: (enabled: boolean, reason?: string) => Promise<unknown>;
  onTrustSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onRevokeSigner: (signerFingerprint: string, reason?: string) => Promise<unknown>;
  onBlockPackage: (request: { packageId?: string; packageSha256?: string; lockSha256?: string; reason?: string }) => Promise<unknown>;
  onPreviewPathToSkill: (request: PathToSkillCaptureRequest) => Promise<PathToSkillCaptureResult>;
  onWritePathToSkill: (request: PathToSkillCaptureRequest) => Promise<PathToSkillCaptureResult>;
}) {
  const editable = !draft.source || draft.source === "user";
  const userSkillSelected = Boolean(selectedSkillName && draft.source === "user");
  const selectedCheck = skillCheck?.checks.find((item) => item.name === draft.name);
  const checkTone = selectedCheck?.status === "error" ? "danger" : selectedCheck?.status === "warning" ? "warn" : "muted";
  const [skillQuery, setSkillQuery] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const query = skillQuery.trim().toLowerCase();
  const visibleSkills = query
    ? skills.filter((skill) =>
        `${skill.name} ${skill.title || ""} ${skill.description || ""} ${skill.category || ""}`.toLowerCase().includes(query),
      )
    : skills;
  const groupedSkills = useMemo(() => {
    const map = new Map<string, AgentSkill[]>();
    for (const skill of visibleSkills) {
      const domain = skillDomain(skill);
      const list = map.get(domain) || [];
      list.push(skill);
      map.set(domain, list);
    }
    return SKILL_DOMAIN_ORDER.filter((domain) => map.has(domain)).map((domain) => ({
      domain,
      items: map.get(domain) || [],
    }));
  }, [visibleSkills]);

  return (
    <div className="min-h-0 flex-1 overflow-auto px-6 py-8">
      <div className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[360px_minmax(0,1fr)]">
        <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-4 flex items-center gap-2">
            <Wrench className="h-4 w-4 shrink-0 text-primary" />
            <div className="truncate text-sm font-semibold">{i18n.t("skills.title")}</div>
            <Badge tone="muted" className="ml-auto shrink-0">
              {skillCount}
            </Badge>
            <Button type="button" variant="ghost" className="h-7 px-2 text-xs" onClick={onCheck} disabled={saving}>
              {i18n.t("skills.check")}
            </Button>
          </div>
          <div className="relative mb-3">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={skillQuery}
              onChange={(event) => setSkillQuery(event.target.value)}
              placeholder={i18n.t("skills.searchPlaceholder")}
              className="h-9 w-full rounded-md border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
            />
          </div>
          <div className="max-h-[calc(100vh-230px)] space-y-2 overflow-auto pr-1">
            {groupedSkills.length === 0 ? (
              <div className="px-3 py-4 text-xs text-muted-foreground">{i18n.t("skills.noMatch")}</div>
            ) : null}
            {groupedSkills.map((group) => {
              const collapsed = Boolean(collapsedGroups[group.domain]) && !query;
              return (
                <div key={group.domain} className="min-w-0">
                  <button
                    type="button"
                    onClick={() =>
                      setCollapsedGroups((current) => ({ ...current, [group.domain]: !current[group.domain] }))
                    }
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <ChevronDown className={cn("h-3.5 w-3.5 shrink-0 transition-transform", collapsed ? "-rotate-90" : "")} />
                    <span className="min-w-0 flex-1 truncate">{group.domain}</span>
                    <Badge tone="muted" className="h-5 shrink-0 px-1.5 text-[10px]">
                      {group.items.length}
                    </Badge>
                  </button>
                  {collapsed
                    ? null
                    : group.items.map((skill) => (
                        <button
                          key={`${skill.source}-${skill.name}`}
                          onClick={() => onSelect(skill)}
                          className={cn(
                            "grid w-full min-w-0 gap-1 rounded-md px-3 py-2 text-left text-sm transition-colors",
                            selectedSkillName === skill.name
                              ? "bg-muted text-foreground"
                              : "text-muted-foreground hover:bg-muted hover:text-foreground",
                          )}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <span className="min-w-0 flex-1 truncate font-medium">{skill.title || skill.name}</span>
                            <Badge tone={skill.available ? "ok" : "warn"} className="h-6 shrink-0">
                              {skill.skillType || skill.source}
                            </Badge>
                          </div>
                          <div className="truncate text-xs text-muted-foreground">{skill.permissionMode}</div>
                        </button>
                      ))}
                </div>
              );
            })}
          </div>
        </section>

        <div className="grid min-w-0 gap-6">
        <form onSubmit={onSave} className="min-w-0 rounded-xl border border-border bg-card p-5 shadow-panel">
          <div className="mb-5 flex items-center gap-2">
            <div className="truncate text-sm font-semibold">{editable ? i18n.t("skills.userSkill") : i18n.t("skills.readOnlySkill")}</div>
            <Badge tone={checkTone} className="ml-auto shrink-0">
              {selectedCheck?.status || draft.permissionMode || "instruction_only"}
            </Badge>
          </div>
          <div className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2">
              <SkillFieldLabel label={i18n.t("skillForm.name")}>
                <input
                  value={draft.name || ""}
                  onChange={(event) => onDraftChange({ ...draft, name: event.target.value })}
                  disabled={!editable || userSkillSelected}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.titleField")}>
                <input
                  value={draft.title || ""}
                  onChange={(event) => onDraftChange({ ...draft, title: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-4">
              <SkillFieldLabel label={i18n.t("skillForm.categoryField")}>
                <input
                  value={draft.category || ""}
                  onChange={(event) => onDraftChange({ ...draft, category: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.type")}>
                <input
                  value={draft.skillType || "package"}
                  onChange={(event) => onDraftChange({ ...draft, skillType: event.target.value })}
                  disabled
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.permission")}>
                <select
                  value={draft.permissionMode || "instruction_only"}
                  onChange={(event) => onDraftChange({ ...draft, permissionMode: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                >
                  <option value="instruction_only">instruction_only</option>
                  <option value="read_only">read_only</option>
                  <option value="preview">preview</option>
                  <option value="approval_required">approval_required</option>
                  <option value="advanced_power_mode">advanced_power_mode</option>
                </select>
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("package.tableRisk")}>
                <select
                  value={draft.riskLevel || "low"}
                  onChange={(event) => onDraftChange({ ...draft, riskLevel: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="critical">critical</option>
                </select>
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={draft.enabled !== false}
                  onChange={(event) => onDraftChange({ ...draft, enabled: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">{i18n.t("skills.enabled")}</span>
              </label>
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={draft.userInvocable !== false}
                  onChange={(event) => onDraftChange({ ...draft, userInvocable: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">{i18n.t("skills.slashCallable")}</span>
              </label>
              <label className="flex h-10 min-w-0 items-center gap-2 rounded-md border border-border bg-background px-3 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={Boolean(draft.disableModelInvocation)}
                  onChange={(event) => onDraftChange({ ...draft, disableModelInvocation: event.target.checked })}
                  disabled={!editable}
                />
                <span className="truncate">{i18n.t("skills.manualOnly")}</span>
              </label>
            </div>
            <SkillFieldLabel label={i18n.t("skillForm.whenToUse")}>
              <textarea
                value={draft.whenToUse || ""}
                onChange={(event) => onDraftChange({ ...draft, whenToUse: event.target.value })}
                disabled={!editable}
                className="min-h-20 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </SkillFieldLabel>
            <SkillFieldLabel label={i18n.t("skillForm.description")}>
              <textarea
                value={draft.description || ""}
                onChange={(event) => onDraftChange({ ...draft, description: event.target.value })}
                disabled={!editable}
                className="min-h-16 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </SkillFieldLabel>
            <div className="grid gap-4 md:grid-cols-3">
              <SkillFieldLabel label={i18n.t("skillForm.allowedTools")}>
                <input
                  value={(draft.allowedTools || draft.tools || []).join(", ")}
                  onChange={(event) => {
                    const tools = splitList(event.target.value);
                    onDraftChange({ ...draft, tools, allowedTools: tools });
                  }}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.disallowedTools")}>
                <input
                  value={(draft.disallowedTools || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, disallowedTools: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.entrypoint")}>
                <input
                  value={draft.entrypointTool || ""}
                  onChange={(event) => onDraftChange({ ...draft, entrypointTool: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <SkillFieldLabel label={i18n.t("skillForm.argumentHint")}>
                <input
                  value={draft.argumentHint || ""}
                  onChange={(event) => onDraftChange({ ...draft, argumentHint: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.testCommand")}>
                <input
                  value={draft.testCommand || ""}
                  onChange={(event) => onDraftChange({ ...draft, testCommand: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <SkillFieldLabel label={i18n.t("skillForm.inputs")}>
                <textarea
                  value={(draft.inputs || []).join("\n")}
                  onChange={(event) => onDraftChange({ ...draft, inputs: splitLines(event.target.value) })}
                  disabled={!editable}
                  className="min-h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.outputs")}>
                <textarea
                  value={(draft.outputs || []).join("\n")}
                  onChange={(event) => onDraftChange({ ...draft, outputs: splitLines(event.target.value) })}
                  disabled={!editable}
                  className="min-h-24 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <SkillFieldLabel label={i18n.t("skillForm.sideEffects")}>
                <input
                  value={draft.sideEffects || ""}
                  onChange={(event) => onDraftChange({ ...draft, sideEffects: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.backupRestore")}>
                <input
                  value={draft.backupRestore || ""}
                  onChange={(event) => onDraftChange({ ...draft, backupRestore: event.target.value })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
              <SkillFieldLabel label={i18n.t("skillForm.requiresEnv")}>
                <input
                  value={(draft.requiresEnv || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, requiresEnv: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.requiresBinaries")}>
                <input
                  value={(draft.requiresBinaries || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, requiresBinaries: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.supportedOs")}>
                <input
                  value={(draft.supportedOs || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, supportedOs: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <SkillFieldLabel label={i18n.t("skillForm.supportFiles")}>
                <input
                  value={(draft.supportFiles || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, supportFiles: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
              <SkillFieldLabel label={i18n.t("skillForm.tags")}>
                <input
                  value={(draft.tags || []).join(", ")}
                  onChange={(event) => onDraftChange({ ...draft, tags: splitList(event.target.value) })}
                  disabled={!editable}
                  className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:bg-muted"
                />
              </SkillFieldLabel>
            </div>
            <SkillFieldLabel label={i18n.t("skillForm.instructions")}>
              <textarea
                value={draft.instructions || ""}
                onChange={(event) => onDraftChange({ ...draft, instructions: event.target.value })}
                disabled={!editable}
                className="min-h-40 w-full resize-none rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary disabled:bg-muted"
              />
            </SkillFieldLabel>
            {selectedCheck?.reasons?.length ? (
              <div className="grid gap-1 rounded-md border border-border bg-muted/50 p-3 text-xs text-muted-foreground">
                {selectedCheck.reasons.map((reason) => (
                  <div key={reason} className="break-words">
                    {reason}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onNew} disabled={saving}>
              {i18n.t("skills.new")}
            </Button>
            {userSkillSelected ? (
              <Button type="button" variant="danger" onClick={onDelete} disabled={saving}>
                {i18n.t("skills.delete")}
              </Button>
            ) : null}
            <Button type="submit" disabled={!editable || saving || !draft.name}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Save
            </Button>
          </div>
        </form>
        <SkillPackageManagerPanel
          packages={packages}
          packageStore={packageStore}
          loading={packagesLoading}
          message={packageMessage}
          error={packageError}
          governance={packageGovernance}
          audit={packageAudit}
          onRefresh={onRefreshPackages}
          onPreflight={onPreflightPackage}
          onImport={onImportPackage}
          onExport={onExportPackage}
          onSetEnabled={onSetPackageEnabled}
          onUninstall={onUninstallPackage}
          onSetSafeMode={onSetSafeMode}
          onTrustSigner={onTrustSigner}
          onRevokeSigner={onRevokeSigner}
          onBlockPackage={onBlockPackage}
          onPreviewPathToSkill={onPreviewPathToSkill}
          onWritePathToSkill={onWritePathToSkill}
        />
        </div>
      </div>
    </div>
  );
}

function SkillFieldLabel({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid min-w-0 gap-2 text-sm">
      <span className="truncate font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}
