import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relative) => fs.readFileSync(path.join(root, relative), "utf8");

const api = read("src/lib/api/memory-review.ts");
const hook = read("src/hooks/use-memory-review.ts");
const settings = read("src/components/settings/memory-review-settings.tsx");
const workspace = read("src/components/settings/settings-workspace.tsx");

assert.match(api, /memoryEnabled: boolean/);
assert.match(api, /crossSessionEnabled: boolean/);
assert.match(api, /memoryEnabled\?: boolean/);
assert.match(api, /crossSessionEnabled\?: boolean/);
assert.match(api, /expectedRevision: number/);
assert.match(api, /fetch_agent_memory_review/);
assert.match(api, /update_agent_memory_review/);

assert.match(hook, /memoryEnabled: snapshot\.memoryEnabled !== false/);
assert.match(hook, /crossSessionEnabled: snapshot\.memoryEnabled !== false && snapshot\.crossSessionEnabled !== false/);
assert.match(hook, /expectedRevision: finiteRevision\(current\.revision\)/);
assert.match(hook, /setError\("stale_revision"\)/);
assert.match(hook, /setError\("request_failed"\)/);

assert.match(settings, /data-memory-preferences/);
assert.equal((settings.match(/data-memory-toggle=/g) || []).length, 1, "toggle marker must be defined once by the shared row");
assert.match(settings, /testId="memory"/);
assert.match(settings, /testId="cross-session"/);
assert.match(settings, /role="switch"/);
assert.match(settings, /disabled=\{!memoryEnabled\}/);
assert.match(settings, /mode: "off"/);
assert.match(settings, /automaticCaptureEnabled: effectiveCrossSession/);
assert.match(settings, /settings\.memoryPreferencesTitle/);
assert.match(settings, /settings\.memoryEnabled/);
assert.match(settings, /settings\.crossSessionMemory/);

for (const forbidden of [
  "MEMORY_REVIEW_MODES",
  "MemoryReviewInbox",
  "memoryReviewModeShadow",
  "memoryReviewModeSuggest",
  "memoryReviewModeBackground",
  "memoryReviewModeAutoSafe",
  "memoryConsolidationStage",
  "memoryReviewJournal",
  "memoryReviewProvider",
  "memoryReviewToken",
  "memoryReviewCost",
  "startReview",
]) {
  assert.doesNotMatch(settings, new RegExp(forbidden), `advanced Memory UI must stay hidden: ${forbidden}`);
}
assert.doesNotMatch(workspace, /MemorySettingsPanel/);

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
  "settings.memoryPreferencesTitle",
  "settings.memoryPreferencesDesc",
  "settings.memoryPreferencesLoading",
  "settings.memoryEnabled",
  "settings.memoryEnabledDesc",
  "settings.crossSessionMemory",
  "settings.crossSessionMemoryDesc",
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

console.log("memory preferences UI contract: ok");
