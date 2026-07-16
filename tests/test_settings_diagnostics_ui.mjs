import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (relativePath) => readFile(path.join(root, relativePath), "utf8");

const challengeLogicPath = path.join(root, "src/components/settings/developer-options-challenge.ts");
const challengeLogicSource = await readFile(challengeLogicPath, "utf8");
const transpiled = ts.transpileModule(challengeLogicSource, {
  compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
  fileName: challengeLogicPath,
}).outputText;
const challengeLogic = await import(`data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`);

assert.equal(challengeLogic.DEVELOPER_OPTIONS_MINIMUM_WAIT_MS, 5_000);
assert.equal(challengeLogic.developerChallengeCountdown(5_000, 0), 5);
assert.equal(challengeLogic.developerChallengeCountdown(5_000, 1), 5);
assert.equal(challengeLogic.developerChallengeCountdown(5_000, 4_001), 1);
assert.equal(challengeLogic.developerChallengeCountdown(5_000, 5_000), 0);
assert.equal(challengeLogic.developerChallengeReady(5_000, 4_999.999), false);
assert.equal(challengeLogic.developerChallengeReady(5_000, 5_000), true);
const submitOnce = challengeLogic.createDeveloperChallengeSubmitGuard();
assert.equal(submitOnce(), true);
assert.equal(submitOnce(), false);

const [workspace, panel, control, dialog, controller, api, app] = await Promise.all([
  read("src/components/settings/settings-workspace.tsx"),
  read("src/components/settings/diagnostics-settings-panel.tsx"),
  read("src/components/settings/developer-options-control.tsx"),
  read("src/components/settings/developer-options-warning-dialog.tsx"),
  read("src/hooks/use-settings-workspace-controller.ts"),
  read("src/lib/api/app.ts"),
  read("src/App.tsx"),
]);

assert.ok(workspace.includes("<DiagnosticsSettingsPanel"));
assert.ok(
  workspace.indexOf("<DiagnosticsSettingsPanel") < workspace.indexOf('{visibleSection === "developer"'),
  "normal diagnostics must render in General before the developer-only section",
);
assert.equal((workspace.match(/<DiagnosticsSettingsPanel/g) || []).length, 1);
assert.ok(!workspace.includes("onSetDebugLogging"));
assert.ok(!workspace.includes("diagnosticsStatus?.logsDir"));
assert.ok(panel.includes("data-vrcforge-diagnostics-settings"));
assert.ok(panel.includes('type="range"'));
assert.ok(panel.includes("data-vrcforge-log-level={selectedLevel}"));
assert.ok(panel.includes('const DIAGNOSTIC_LOG_LEVELS: readonly DiagnosticLogLevel[] = ["error", "warn", "info", "debug", "trace"]'));
assert.ok(panel.includes("onChange={(event) => handleLevelChange"));
assert.ok(panel.includes("onLogLevelChange(level)"));
assert.ok(panel.includes("data-vrcforge-open-logs"));
assert.ok(panel.includes("data-vrcforge-export-support"));
assert.ok(panel.includes("data-vrcforge-log-identities"));
assert.ok(!panel.includes("logsDir"));

assert.ok(control.includes("beginDeveloperOptionsChallenge(endpoint)"));
assert.ok(control.includes("cancelDeveloperOptionsChallenge(endpoint"));
assert.ok(control.includes("onChange(true, active.challengeId)"));
assert.ok(control.includes("data-vrcforge-developer-toggle"));
assert.ok(dialog.includes("Math.max(DEVELOPER_OPTIONS_MINIMUM_WAIT_MS, waitMs)"));
assert.ok(dialog.includes("performance.now()"));
assert.ok(dialog.includes("disabled={!ready}"));
assert.ok(dialog.includes("data-vrcforge-developer-cancel"));
assert.ok(dialog.includes("data-vrcforge-developer-confirm"));
assert.ok(dialog.includes("data-vrcforge-developer-countdown"));
assert.ok(dialog.includes('data-vrcforge-developer-warning="true"'));
assert.ok(!dialog.includes("data-vrcforge-developer-warning={challengeId}"));
const cancelButton = dialog.match(/<Button[\s\S]*?data-vrcforge-developer-cancel[\s\S]*?>/)?.[0] || "";
assert.ok(cancelButton);
assert.ok(!cancelButton.includes("disabled="), "Cancel must never be disabled");

assert.ok(controller.includes("diagnosticsRequestSequenceRef"));
assert.ok(controller.includes("diagnosticsWriteQueueRef"));
assert.match(controller, /diagnosticsWriteQueueRef\.current\s*\.catch/);
assert.ok(controller.includes("updateDiagnostics(targetEndpoint, { logLevel: level })"));
assert.ok(controller.includes('invoke("open_logs_folder")'));
assert.ok(api.includes('"begin_developer_options_challenge"'));
assert.ok(api.includes('"cancel_developer_options_challenge"'));
assert.ok(api.includes("encodeURIComponent(challengeId)"));
assert.ok(app.includes("developerChallengeId: next.developerChallengeId"));

const localeNames = ["en-US", "ja-JP", "zh-CN", "zh-TW"];
const locales = await Promise.all(
  localeNames.map(async (name) => [name, JSON.parse(await read(`src/locales/${name}.json`))]),
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
const flattened = locales.map(([name, locale]) => [name, flatten(locale)]);
const referenceKeys = [...flattened[0][1].keys()].sort();
const placeholders = (value) => [...value.matchAll(/{{\s*([^}\s]+)\s*}}/g)].map((match) => match[1]).sort();
for (const [name, entries] of flattened) {
  assert.deepEqual([...entries.keys()].sort(), referenceKeys, `${name} locale keys differ`);
  for (const key of referenceKeys) {
    assert.deepEqual(placeholders(entries.get(key)), placeholders(flattened[0][1].get(key)), `${name}:${key} placeholders differ`);
  }
}

console.log("settings diagnostics/developer challenge contract: ok");
