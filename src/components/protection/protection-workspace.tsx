import { Gauge, Loader2, RefreshCw, Shield, Sparkles } from "lucide-react";
import i18n from "../../i18n";
import type { AvatarEncryptionBenchmarkRow, AvatarEncryptionPlanResult, AvatarEncryptionProfileCard, AvatarListItem } from "../../lib/api";
import { protectionPlanPayload } from "../../lib/protection-plan";
import { cn } from "../../lib/utils";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { DataLine } from "../ui/data-line";

const PROTECTION_PROFILE_FALLBACKS: AvatarEncryptionProfileCard[] = [
  {
    id: "lite",
    label: "Lite",
    title: i18n.t("encryption.profiles.liteTitle"),
    description: i18n.t("encryption.profiles.liteDesc"),
    protection: "Low-overhead encryption.",
    cost: "lowest",
    deviceFit: "Windows / low-end PC",
    applyStatus: "available",
  },
  {
    id: "standard",
    label: i18n.t("package.standard"),
    title: i18n.t("encryption.profiles.standardTitle"),
    description: i18n.t("encryption.profiles.standardDesc"),
    protection: "Recommended encryption.",
    cost: "balanced",
    deviceFit: "PC default",
    recommended: true,
    applyStatus: "available",
  },
  {
    id: "paranoid",
    label: "Paranoid",
    title: i18n.t("encryption.profiles.paranoidTitle"),
    description: i18n.t("encryption.profiles.paranoidDesc"),
    protection: "Highest preview mode.",
    cost: "highest",
    deviceFit: "high-end PC",
    applyStatus: "blocked_until_blendshape_proof",
  },
];

export function ProtectionWorkspace({
  plan,
  selectedProjectPath,
  avatarPath,
  avatars,
  profile,
  ownsAssets,
  loading,
  loadingAvatars,
  message,
  avatarMessage,
  requestingFamily,
  onAvatarPathChange,
  onProfileChange,
  onOwnsAssetsChange,
  onRefresh,
  onRefreshAvatars,
  onRequestApply,
}: {
  plan: AvatarEncryptionPlanResult | null;
  selectedProjectPath: string;
  avatarPath: string;
  avatars: AvatarListItem[];
  profile: string;
  ownsAssets: boolean;
  loading: boolean;
  loadingAvatars: boolean;
  message: string;
  avatarMessage: string;
  requestingFamily: string;
  onAvatarPathChange: (value: string) => void;
  onProfileChange: (value: string) => void;
  onOwnsAssetsChange: (value: boolean) => void;
  onRefresh: () => void;
  onRefreshAvatars: () => void;
  onRequestApply: (family: "liltoon" | "poiyomi") => void;
}) {
  const planPayload = protectionPlanPayload(plan);
  const activeProfile = protectionProfileCards(plan).find((item) => item.id === profile) || PROTECTION_PROFILE_FALLBACKS[1];
  const benchmarkRows = protectionBenchmarkRows(plan);
  const benchmarkGroups = groupProtectionBenchmarks(benchmarkRows);
  const hardGate = protectionRecord(planPayload.hardGate);
  const blockingIds = protectionArray(hardGate.blockingIds).map((item) => String(item));
  const connector = protectionRecord(planPayload.externalAddon);
  const connectorConfigured = Boolean(connector.configured);
  const requestReady = planPayload.status === "request_ready" && planPayload.writeStatus !== "blocked" && connectorConfigured;
  const profileApplyBlocked = String(activeProfile.applyStatus || "").startsWith("blocked");
  const selectedCandidates = protectionArray(planPayload.selectedCandidates);
  const hasLilToon = selectedCandidates.length === 0 || protectionFamilyAvailable(selectedCandidates, "liltoon");
  const hasPoiyomi = selectedCandidates.length === 0 || protectionFamilyAvailable(selectedCandidates, "poiyomi");
  const canRequest = requestReady && ownsAssets && Boolean(avatarPath.trim()) && !profileApplyBlocked && !loading;
  const impact = protectionImpactSummary(benchmarkRows, profile);

  return (
    <div className="min-h-0 flex-1 overflow-auto px-3 py-4 sm:px-6 sm:py-8">
      <div className="mx-auto grid max-w-6xl gap-6">
        <section className="flex min-w-0 flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 items-center gap-2">
              <Shield className="h-4 w-4 shrink-0 text-primary" />
              <h1 className="truncate text-lg font-semibold">{i18n.t("encryption.title")}</h1>
              {activeProfile.recommended ? (
                <Badge tone="ok" className="shrink-0">
                  {i18n.t("encryption.recommended")}
                </Badge>
              ) : null}
            </div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{selectedProjectPath || i18n.t("encryption.noUnityProject")}</div>
          </div>
          <Badge tone={requestReady ? "ok" : "warn"} className="shrink-0">
            {requestReady ? i18n.t("encryption.readyToRequest") : connectorConfigured ? i18n.t("encryption.needsReview") : i18n.t("encryption.privateAddonRequired")}
          </Badge>
          <Badge tone={profileApplyBlocked ? "warn" : "muted"} className="shrink-0">
            {activeProfile.label || profile}
          </Badge>
          <Button type="button" variant="outline" onClick={onRefresh} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </section>

        <section className="grid gap-3 md:grid-cols-3">
          {protectionProfileCards(plan).map((card) => {
            const selected = card.id === profile;
            return (
              <button
                key={card.id}
                type="button"
                onClick={() => onProfileChange(String(card.id))}
                className={cn(
                  "min-w-0 rounded-lg border bg-card p-4 text-left transition-colors",
                  selected ? "border-primary bg-primary/5" : "border-border hover:border-primary/40 hover:bg-muted/40",
                )}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <Shield className="h-4 w-4 shrink-0 text-primary" />
                  <div className="min-w-0 flex-1 truncate text-sm font-semibold">{card.title || card.label || card.id}</div>
                  {card.recommended ? (
                    <Badge tone="ok" className="shrink-0">
                      {i18n.t("encryption.default")}
                    </Badge>
                  ) : null}
                </div>
                <div className="mt-2 text-xs text-muted-foreground">{card.description || "-"}</div>
                <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                  <DataLine label={i18n.t("encryption.protection")} value={card.protection || "-"} />
                  <DataLine label={i18n.t("encryption.device")} value={card.deviceFit || "-"} />
                  <DataLine label={i18n.t("encryption.impact")} value={protectionCostLabel(card.cost)} />
                </div>
                {String(card.applyStatus || "").startsWith("blocked") ? (
                  <Badge tone="warn" className="mt-3">
                    {i18n.t("encryption.proofGate")}
                  </Badge>
                ) : null}
              </button>
            );
          })}
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,0.75fr)]">
          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <Sparkles className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("encryption.planTarget")}</div>
            </div>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
              <select
                value={avatars.some((item) => item.avatarPath === avatarPath) ? avatarPath : ""}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                disabled={loadingAvatars || avatars.length === 0}
                className="h-9 min-w-0 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary disabled:text-muted-foreground"
              >
                <option value="">{loadingAvatars ? i18n.t("encryption.scanningAvatars") : avatars.length ? i18n.t("encryption.selectAvatar") : i18n.t("encryption.noSceneAvatars")}</option>
                {avatars.map((avatar, index) => {
                  const value = avatar.avatarPath || "";
                  return (
                    <option key={`${value}-${index}`} value={value}>
                      {avatarOptionLabel(avatar)}
                    </option>
                  );
                })}
              </select>
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loadingAvatars} onClick={onRefreshAvatars}>
                {loadingAvatars ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Avatars
              </Button>
            </div>
            <div className="mt-2 flex min-w-0 items-center gap-2">
              <input
                value={avatarPath}
                onChange={(event) => onAvatarPathChange(event.target.value)}
                placeholder={i18n.t("encryption.avatarScenePath")}
                className="h-9 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-sm outline-none focus:border-primary"
              />
              <Button type="button" variant="ghost" className="h-9 shrink-0 px-3 text-xs" disabled={loading} onClick={onRefresh}>
                {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                Plan
              </Button>
            </div>
            <label className="mt-3 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={ownsAssets}
                onChange={(event) => onOwnsAssetsChange(event.target.checked)}
                className="h-4 w-4 shrink-0 rounded border-border"
              />
              <span className="min-w-0 truncate">{i18n.t("encryption.ownsAssetsLabel")}</span>
            </label>
            {avatarMessage ? <div className="mt-2 truncate text-xs text-muted-foreground">{avatarMessage}</div> : null}
            {message ? <div className="mt-2 truncate text-xs text-muted-foreground">{message}</div> : null}
          </div>

          <div className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
            <div className="mb-3 flex min-w-0 items-center gap-2">
              <Shield className="h-4 w-4 shrink-0 text-primary" />
              <div className="truncate text-sm font-semibold">{i18n.t("encryption.preview")}</div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              <ProtectionMetric label={i18n.t("encryption.targets")} value={formatProtectionMetric(planPayload.selectedCandidateCount)} />
              <ProtectionMetric label={i18n.t("encryption.mode")} value={activeProfile.label || profile} />
              <ProtectionMetric label={i18n.t("encryption.plan")} value={String(planPayload.status || i18n.t("encryption.notLoaded"))} />
              <ProtectionMetric label={i18n.t("encryption.expected")} value={impact} />
            </div>
            {blockingIds.length ? (
              <div className="mt-3 grid gap-1 text-xs text-muted-foreground">
                {blockingIds.slice(0, 4).map((item) => (
                  <DataLine key={item} label={i18n.t("encryption.gate")} value={protectionGateLabel(item)} />
                ))}
              </div>
            ) : null}
            <div className="mt-3 flex min-w-0 flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                className="h-8 px-3 text-xs"
                disabled={!canRequest || !hasLilToon || requestingFamily === "liltoon"}
                onClick={() => onRequestApply("liltoon")}
              >
                {requestingFamily === "liltoon" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                {i18n.t("encryption.requestLilToon")}
              </Button>
              <Button
                type="button"
                variant="outline"
                className="h-8 px-3 text-xs"
                disabled={!canRequest || !hasPoiyomi || requestingFamily === "poiyomi"}
                onClick={() => onRequestApply("poiyomi")}
              >
                {requestingFamily === "poiyomi" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Shield className="h-3.5 w-3.5" />}
                {i18n.t("encryption.requestPoiyomi")}
              </Button>
              <Badge tone="muted" className="h-8 shrink-0">
                {i18n.t("encryption.approvalRequired")}
              </Badge>
            </div>
          </div>
        </section>

        <section className="min-w-0 rounded-xl border border-border bg-card p-4 shadow-panel">
          <div className="mb-3 flex min-w-0 items-center gap-2">
            <Gauge className="h-4 w-4 shrink-0 text-primary" />
            <div className="truncate text-sm font-semibold">{i18n.t("encryption.estimatedFrameImpact")}</div>
            <Badge tone="muted" className="ml-auto shrink-0">
              {i18n.t("encryption.planningEstimate")}
            </Badge>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] border-separate border-spacing-0 text-left text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="border-b border-border px-3 py-2 font-medium">{i18n.t("encryption.avatarSize")}</th>
                  {PROTECTION_PROFILE_FALLBACKS.map((item) => (
                    <th key={item.id} className="border-b border-border px-3 py-2 font-medium">
                      {item.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {benchmarkGroups.map((group) => (
                  <tr key={group.scale}>
                    <td className="border-b border-border/70 px-3 py-2 text-muted-foreground">{group.scale}</td>
                    {PROTECTION_PROFILE_FALLBACKS.map((item) => {
                      const row = group.byProfile[String(item.id)];
                      return (
                        <td key={item.id} className="border-b border-border/70 px-3 py-2">
                          {row ? `${formatProtectionProofValue(row.estimatedFps)} fps / ${formatProtectionProofValue(row.estimatedImpactPercent)}%` : "-"}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}


function protectionRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}



function protectionArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}



function protectionProfileCards(result: AvatarEncryptionPlanResult | null): AvatarEncryptionProfileCard[] {
  const plan = protectionPlanPayload(result);
  const cards = protectionArray(plan.profileCards).filter((item): item is AvatarEncryptionProfileCard => Boolean(item) && typeof item === "object");
  if (!cards.length) {
    return PROTECTION_PROFILE_FALLBACKS;
  }
  const seen = new Set<string>();
  return [...cards, ...PROTECTION_PROFILE_FALLBACKS].filter((item) => {
    const id = String(item.id || "");
    if (!id || seen.has(id)) {
      return false;
    }
    seen.add(id);
    return true;
  });
}



function protectionBenchmarkRows(result: AvatarEncryptionPlanResult | null): AvatarEncryptionBenchmarkRow[] {
  const plan = protectionPlanPayload(result);
  return protectionArray(plan.benchmarkTable).filter((item): item is AvatarEncryptionBenchmarkRow => Boolean(item) && typeof item === "object");
}



function groupProtectionBenchmarks(rows: AvatarEncryptionBenchmarkRow[]): Array<{ scale: string; byProfile: Record<string, AvatarEncryptionBenchmarkRow> }> {
  const groups: Array<{ scale: string; triangles: number; byProfile: Record<string, AvatarEncryptionBenchmarkRow> }> = [];
  for (const row of rows) {
    const triangles = Number(row.triangles || 0);
    const scale = row.avatarScale || (triangles ? `${Math.round(triangles / 10000)}万面` : i18n.t("optimization.unknown"));
    let group = groups.find((item) => item.scale === scale);
    if (!group) {
      group = { scale, triangles, byProfile: {} };
      groups.push(group);
    }
    if (row.profile) {
      group.byProfile[String(row.profile)] = row;
    }
  }
  return groups.sort((a, b) => a.triangles - b.triangles).map(({ scale, byProfile }) => ({ scale, byProfile }));
}

function protectionFamilyAvailable(candidates: unknown[], family: "liltoon" | "poiyomi"): boolean {
  return candidates.some((candidate) => {
    const item = protectionRecord(candidate);
    const familyId = String(item.shaderFamilyId || item.shaderFamily || "").toLowerCase();
    if (family === "liltoon") {
      return familyId.includes("liltoon") || familyId.includes("liltoon");
    }
    return familyId.includes("poiyomi") || familyId.includes("poi");
  });
}



function protectionCostLabel(value?: string): string {
  const cost = String(value || "").toLowerCase();
  if (cost === "lowest") {
    return "Lowest";
  }
  if (cost === "balanced") {
    return "Balanced";
  }
  if (cost === "highest") {
    return "Highest";
  }
  return value || "-";
}



function protectionGateLabel(value: string): string {
  if (value === "platform.windows_only") {
    return "Windows avatar only";
  }
  if (value === "profile.paranoid_blendshape_proof_required") {
    return "Highest mode needs proof";
  }
  if (value === "profile.custom_layers_not_supported") {
    return "Choose one of the three modes";
  }
  if (value === "layer.experimental_or_research_only") {
    return "Layer still in testing";
  }
  if (value === "targets.requested_targets_not_found") {
    return "Selected target not found";
  }
  if (value === "shader_family.no_liltoon_or_poiyomi_candidate") {
    return "No supported shader target";
  }
  if (value === "shader_family.requested_restore_adapter_missing") {
    return "Shader adapter missing";
  }
  if (value === "plan.untrusted_external_plan") {
    return "Plan needs fresh scan";
  }
  return "Needs review";
}



function protectionImpactSummary(rows: AvatarEncryptionBenchmarkRow[], profile: string): string {
  const candidates = rows.filter((row) => row.profile === profile);
  if (!candidates.length) {
    return "not estimated";
  }
  const maxImpact = Math.max(...candidates.map((row) => Number(row.estimatedImpactPercent || 0)));
  return `${formatProtectionProofValue(maxImpact)}% max`;
}


function ProtectionMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-background px-3 py-2">
      <div className="truncate text-[11px] uppercase text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  );
}

function formatProtectionMetric(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value >= 1000 ? Math.round(value).toLocaleString() : String(value);
  }
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return "unknown";
}

function formatProtectionProofValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  return "-";
}

function avatarOptionLabel(avatar: AvatarListItem): string {
  const name = avatar.avatarName || shortPath(avatar.avatarPath || "") || "Avatar";
  const parts = [name];
  if (avatar.sceneName) {
    parts.push(avatar.sceneName);
  }
  const stats: string[] = [];
  if (typeof avatar.rendererCount === "number") {
    stats.push(`${avatar.rendererCount} renderers`);
  }
  if (typeof avatar.blendshapeCount === "number") {
    stats.push(`${avatar.blendshapeCount} blendshapes`);
  }
  if (stats.length) {
    parts.push(stats.join(", "));
  }
  return parts.join(" - ");
}

function shortPath(path: string) {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).slice(-1)[0] || path;
}
