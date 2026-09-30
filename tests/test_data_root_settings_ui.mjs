import assert from "node:assert/strict";
import fs from "node:fs";
import ts from "typescript";

const appSource = fs.readFileSync("src/lib/api/app.ts", "utf8");
const panelSource = fs.readFileSync("src/components/settings/data-root-settings-panel.tsx", "utf8");
const workspaceSource = fs.readFileSync("src/components/settings/settings-workspace.tsx", "utf8");

// API layer contract for both transport channels.
assert.ok(appSource.includes("export async function fetchDataRoot("), "GET data-root must be wired");
assert.ok(appSource.includes("export async function updateDataRoot("), "POST data-root must be wired");
assert.ok(appSource.includes('"/api/app/settings/data-root"') || appSource.includes("/api/app/settings/data-root"), "both calls must target /api/app/settings/data-root");
assert.ok(appSource.includes('"fetch_data_root"'), "GET must invoke the typed desktop command");
assert.ok(appSource.includes('"update_data_root"'), "POST must invoke the typed desktop command");
assert.ok(appSource.includes('method: "POST"'), "the update must use HTTP POST");
assert.ok(appSource.includes("body: JSON.stringify({ path })"), "the update body must carry { path }");
assert.ok(appSource.includes("export type DataRootInfo"), "DataRootInfo must be exported for the panel");
assert.ok(appSource.includes("export type DataRootUpdateResult"), "DataRootUpdateResult must be exported for the panel");

// The workspace renders exactly one data-root panel inside Storage.
assert.ok(
  workspaceSource.includes("<DataRootSettingsPanel endpoint={endpoint}"),
  "the data-root panel must be mounted in the workspace",
);
assert.equal((workspaceSource.match(/<DataRootSettingsPanel/g) || []).length, 1, "the panel must be mounted exactly once");

// Panel behavior: GET display, POST success/failure branches.
assert.ok(panelSource.includes("fetchDataRoot(endpoint)"), "the panel must fetch the current data root");
assert.ok(panelSource.includes("updateDataRoot(endpoint, nextPath)"), "the panel must post the edited path");
assert.ok(panelSource.includes("data-vrcforge-data-root-path"), "the current path must be displayed");
assert.ok(panelSource.includes("{dataRoot.path}"), "the displayed value must be the GET path");
assert.ok(panelSource.includes("dataRoot.writable"), "the writable state must drive the status badge");
assert.ok(panelSource.includes("dataRoot?.inProgramFiles"), "the Program Files state must drive the warning");
assert.ok(panelSource.includes('t("settings.dataRootProgramFilesWarning")'), "the Program Files warning must use a locale key");
assert.ok(panelSource.includes("data-vrcforge-data-root-input"), "an editable path input must exist");
assert.ok(panelSource.includes("data-vrcforge-data-root-save"), "a save button must exist");
assert.ok(panelSource.includes("data-vrcforge-data-root-errors"), "POST errors must be rendered");
assert.ok(panelSource.includes("setSaveErrors(outcome.errors)"), "ok:false must surface the backend error list");
assert.ok(panelSource.includes("setSaveSuccess(true)"), "ok:true must surface success");
assert.ok(panelSource.includes("setMigratedPaths(outcome.migrated)"), "the migration result must be surfaced");
assert.ok(panelSource.includes("data-vrcforge-data-root-migrated"), "migrated items must be announced");
assert.ok(panelSource.includes('t("settings.dataRootRestartRequired")'), "a restart notice must be shown after a successful save");

// Execute the real response classifier with local inputs.
const helperStart = panelSource.indexOf("export function dataRootUpdateOutcome(");
assert.notEqual(helperStart, -1, "the outcome classifier must be exported");
const helperEnd = panelSource.indexOf("\n}", helperStart) + 2;
const helperJs = ts.transpileModule(panelSource.slice(helperStart, helperEnd).replace(/^export /, ""), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText;
const dataRootUpdateOutcome = new Function(`${helperJs}\nreturn dataRootUpdateOutcome;`)();
assert.deepEqual(
  dataRootUpdateOutcome({ ok: true, errors: [], migrated: ["a", "b"] }, "fallback"),
  { ok: true, errors: [], migrated: ["a", "b"] },
  "success must keep the migrated list",
);
assert.deepEqual(
  dataRootUpdateOutcome({ ok: false, errors: ["bad path"], migrated: [] }, "fallback"),
  { ok: false, errors: ["bad path"], migrated: [] },
  "failure must surface the backend error list verbatim",
);
assert.deepEqual(
  dataRootUpdateOutcome({ ok: false, errors: [], migrated: ["x"] }, "fallback"),
  { ok: false, errors: ["fallback"], migrated: [] },
  "an empty error list must fall back to the locale message",
);

// Locale parity for every newly added key across all four languages.
const localeNames = ["en-US", "ja-JP", "zh-CN", "zh-TW"];
const locales = await Promise.all(
  localeNames.map(async (name) => [name, JSON.parse(fs.readFileSync(`src/locales/${name}.json`, "utf8"))]),
);
const flatten = (value, prefix = "", output = new Map()) => {
  for (const [key, entry] of Object.entries(value)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (entry && typeof entry === "object" && !Array.isArray(entry)) {
      flatten(entry, next, output);
    } else {
      output.set(next, String(entry));
    }
  }
  return output;
};
const newKeys = [
  "settings.dataRootTitle",
  "settings.dataRootDesc",
  "settings.dataRootWritable",
  "settings.dataRootNotWritable",
  "settings.dataRootProgramFilesWarning",
  "settings.dataRootPlaceholder",
  "settings.dataRootReload",
  "settings.dataRootLoadFailed",
  "settings.dataRootSaveSuccess",
  "settings.dataRootSaveFailed",
  "settings.dataRootMigratedCount",
  "settings.dataRootRestartRequired",
  "chat.deletePermanentlyFailed",
];
const flattened = locales.map(([name, locale]) => [name, flatten(locale)]);
const placeholders = (value) => [...value.matchAll(/{{\s*([^}\s]+)\s*}}/g)].map((match) => match[1]).sort();
const reference = flattened[0][1];
for (const [name, entries] of flattened) {
  for (const key of newKeys) {
    const value = entries.get(key);
    assert.ok(value !== undefined && String(value).trim().length > 0, `${name}:${key} must exist and be non-empty`);
    assert.deepEqual(placeholders(value), placeholders(reference.get(key)), `${name}:${key} placeholder parity`);
  }
  assert.equal(
    entries.get("settings.dataRootMigratedCount").includes("{{count}}"),
    true,
    `${name}:settings.dataRootMigratedCount must keep {{count}}`,
  );
}

console.log("data root settings UI + chat permanent delete wiring: ok");
