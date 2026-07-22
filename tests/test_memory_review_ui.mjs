import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const api = read("src/lib/api/memory-review.ts");
const hook = read("src/hooks/use-memory-review.ts");
const settings = read("src/components/settings/memory-review-settings.tsx");
const inbox = read("src/components/settings/memory-review-inbox.tsx");

for (const mode of ["off", "shadow", "suggest_only", "bounded_background", "auto_safe"]) {
  assert.match(api, new RegExp(`"${mode}"`), `missing ${mode} transport mode`);
  assert.match(settings, new RegExp(`${mode}:|mode === "${mode}"|mode=\\{mode\\}`), `missing ${mode} UI state`);
}
assert.match(api, /normalizeMemoryReviewMode/);
assert.match(api, /: "off"/);
assert.match(api, /MemoryReviewCandidateAction = "accept" \| "reject" \| "defer" \| "erase" \| "undo" \| "read"/);
assert.match(api, /schema: string/);
assert.match(api, /policyVersion: string/);
assert.match(api, /revision: number/);
assert.match(api, /runStatus: MemoryReviewRunStatus/);
assert.match(api, /unreadCount: number/);
assert.match(api, /candidates: MemoryReviewCandidate\[\]/);
assert.match(api, /providerDisclosure: MemoryReviewProviderDisclosure/);
assert.match(api, /activeConfigMatches\?: boolean/);
assert.match(api, /requestedProjectRoot\?: string/);
assert.match(api, /configuredProjectMatches\?: boolean/);
assert.match(api, /conflictCount\?: number/);
assert.doesNotMatch(api, /confidenceFactors\?:/);
assert.match(api, /usage\?: MemoryReviewUsage/);
assert.match(api, /provider\?: string/);
assert.match(api, /model\?: string/);
assert.match(api, /budget\?: MemoryReviewRunBudget/);
assert.match(api, /shadowSummary\?: MemoryReviewShadowSummary/);
for (const field of [
  "mode",
  "cadenceMinutes",
  "inputCharCap",
  "tokenCap",
  "costCapUsd",
  "inputCostPerMillionUsd",
  "outputCostPerMillionUsd",
  "retentionDays",
  "provider",
  "model",
  "scope",
  "projectRoot",
  "expectedRevision",
]) {
  assert.match(api, new RegExp(`${field}[?:]+`), `missing ${field} config contract`);
}
assert.match(api, /fetch_agent_memory_review/);
assert.match(api, /update_agent_memory_review/);
assert.match(api, /run_agent_memory_review/);
assert.match(api, /mutate_agent_memory_review_candidate/);
assert.match(api, /\/api\/app\/agent\/memory\/review/);
assert.match(api, /\/candidates\/\$\{encodedId\}\/\$\{action\}/);
assert.match(api, /timeoutMs: 1_200_000/);

assert.match(hook, /contextEpoch = useRef\(0\)/);
assert.match(hook, /requestSerial = useRef\(0\)/);
assert.match(hook, /lastAppliedRequest = useRef\(0\)/);
assert.match(hook, /next\.revision < currentRevision/);
assert.match(hook, /next\.revision === currentRevision && requestId < lastAppliedRequest\.current/);
assert.match(hook, /expectedEpoch !== contextEpoch\.current/);
assert.match(hook, /expectedRevision: finiteRevision\(current\.revision\)/);
assert.match(hook, /candidate\?\.scope === "project"/);
assert.doesNotMatch(hook, /action === "accept" \|\| action === "erase" \|\| action === "undo"/);
assert.match(hook, /projectRoot: current\.projectRoot \|\| selectedProjectPath \|\| undefined/);
assert.match(hook, /previousRefreshSignal\.current === refreshSignal/);
assert.match(hook, /void refresh\(false\)/);
assert.match(hook, /setError\("stale_revision"\)/);
assert.match(hook, /setError\("request_failed"\)/);
assert.doesNotMatch(hook, /cause\.message|JSON\.stringify\(cause\)/);

assert.match(settings, /data-memory-review-settings/);
assert.match(settings, /mode === "auto_safe"/);
assert.match(settings, /memoryReviewModePlanned/);
assert.match(settings, /min=\{30\}/);
assert.match(settings, /safeCount\(event\.target\.valueAsNumber, 30\)/);
assert.match(settings, /providerDisclosure\?\.paidRun/);
assert.match(settings, /providerDisclosure\.tokenCap/);
assert.match(settings, /providerDisclosure\.costCapUsd/);
assert.match(settings, /draft\.inputCostPerMillionUsd/);
assert.match(settings, /draft\.outputCostPerMillionUsd/);
assert.match(settings, /memoryReviewPricingHelp/);
assert.match(settings, /lastRunUsage\?\.totalTokens/);
assert.match(settings, /lastRunUsage\.costUsd\.toFixed\(4\)/);
assert.match(settings, /snapshot\.lastRun\.provider/);
assert.match(settings, /snapshot\.lastRun\.model/);
assert.match(settings, /snapshot\.lastRun\.budget\?\.inputCharCap/);
assert.match(settings, /data-memory-review-last-run-evidence/);
assert.match(settings, /data-memory-review-shadow-summary/);
assert.match(settings, /controller\.saveConfig\(draft\)/);
assert.match(settings, /controller\.startReview\(draft\.scope\)/);
assert.match(settings, /const configDirty = Boolean/);
assert.match(settings, /\|\| configDirty/);
assert.match(settings, /function configFingerprint/);
assert.match(settings, /const snapshotConfig = snapshot \? configFingerprint\(snapshot\) : ""/);
assert.match(settings, /if \(contextChanged \|\| !draftDirty\)/);
assert.match(settings, /if \(configChanged\)/);
assert.match(settings, /setDraftDirty\(true\)/);
assert.match(settings, /remoteConfigChanged/);
assert.match(settings, /memoryReviewRemoteConfigChanged/);
assert.match(settings, /memoryReviewReloadConfig/);
assert.doesNotMatch(settings, /\[snapshot\?\.revision, snapshot\?\.projectRoot\]/);
assert.match(settings, /providerConfigChanged/);
assert.match(settings, /memoryReviewProviderChanged/);
assert.match(settings, /projectBindingChanged/);
assert.match(settings, /memoryReviewProjectChanged/);
assert.match(settings, /<MemoryReviewInbox/);
assert.match(settings, /memoryReviewRunTimedOut/);
assert.match(settings, /memoryReviewRunDeferred/);
assert.doesNotMatch(settings, /failureLabel|costUnavailableReason|JSON\.stringify/);

for (const action of ["accept", "reject", "defer", "erase", "undo"]) {
  assert.match(inbox, new RegExp(`"${action}"`), `missing ${action} candidate action`);
}
assert.match(inbox, /onDecision\(firstUnreadId, "read"\)/);
assert.match(inbox, /editedText\.trim\(\)/);
assert.match(inbox, /candidate\.proposedText/);
assert.match(inbox, /candidate\.evidenceCount/);
assert.match(inbox, /candidate\.conflictCount/);
assert.doesNotMatch(inbox, /candidate\.(projectRoot|confidenceFactors|conflicts|supersedes|usage)/);
assert.doesNotMatch(inbox, /failureLabel|costUnavailableReason|JSON\.stringify/);

const localeNames = ["en-US", "ja-JP", "zh-CN", "zh-TW"];
const locales = localeNames.map((name) => [name, JSON.parse(read(`src/locales/${name}.json`))]);
const flatten = (value, prefix = "", rows = new Map()) => {
  for (const [key, item] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) flatten(item, next, rows);
    else rows.set(next, String(item));
  }
  return rows;
};
const placeholders = (value) => [...String(value || "").matchAll(/{{\s*([^}\s]+)\s*}}/g)]
  .map((match) => match[1])
  .sort();
const reference = flatten(locales[0][1]);
const requiredKeys = [
  "settings.memoryReviewTitle",
  "settings.memoryReviewModeOff",
  "settings.memoryReviewModeShadow",
  "settings.memoryReviewModeSuggest",
  "settings.memoryReviewModeBackground",
  "settings.memoryReviewModeAutoSafe",
  "settings.memoryReviewPaidRun",
  "settings.memoryReviewNoPaidRun",
  "settings.memoryReviewTokenUsage",
  "settings.memoryReviewActualCost",
  "settings.memoryReviewLastRunEvidence",
  "settings.memoryReviewInputPrice",
  "settings.memoryReviewOutputPrice",
  "settings.memoryReviewPricingHelp",
  "settings.memoryReviewRunTimedOut",
  "settings.memoryReviewRunDeferred",
  "settings.memoryReviewShadowSummary",
  "settings.memoryReviewShadowEligible",
  "settings.memoryReviewShadowSkipped",
  "settings.memoryReviewShadowScannedAt",
  "settings.memoryReviewAccept",
  "settings.memoryReviewAcceptEdited",
  "settings.memoryReviewReject",
  "settings.memoryReviewDefer",
  "settings.memoryReviewUndo",
  "settings.memoryReviewPermanentErase",
  "settings.memoryReviewStaleRevision",
  "settings.memoryReviewRemoteConfigChanged",
  "settings.memoryReviewReloadConfig",
];
for (const [name, locale] of locales) {
  const entries = flatten(locale);
  assert.deepEqual([...entries.keys()].sort(), [...reference.keys()].sort(), `${name} locale keys differ`);
  for (const [key, value] of reference) {
    assert.deepEqual(placeholders(entries.get(key)), placeholders(value), `${name}:${key} placeholders differ`);
  }
  for (const key of requiredKeys) {
    assert.ok(entries.get(key), `${name} missing ${key}`);
  }
}

console.log("memory review UI contract: ok");
