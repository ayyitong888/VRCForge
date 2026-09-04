import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../src/components/settings/checkpoint-quota-warning.ts", import.meta.url), "utf8");
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 } }).outputText;
const { checkpointQuotaWarning: warning, shouldShowCheckpointQuotaWarning: show } = await import(`data:text/javascript;base64,${Buffer.from(js).toString("base64")}`);
const usage = { ok: true, directory: "/fixture", sizeBytes: 1048576, maxSizeMb: 1 };
assert.equal(warning(usage), null, "exactly at limit is not an overage");
assert.equal(warning({ ...usage, maxSizeMb: 0, sizeBytes: 9000000000 }), null, "zero means unlimited");
assert.equal(warning({ ...usage, ok: false, sizeBytes: 1048577 }), null, "failed observation is not evidence");
assert.equal(warning({ ...usage, sizeBytes: undefined }), null);
assert.equal(warning({ ...usage, sizeBytes: NaN }), null);
const first = warning({ ...usage, sizeBytes: 1048577 });
assert.equal(first.excessBytes, 1, "compare actual bytes, not rounded MB");
assert.equal(show(first, null), true);
assert.equal(show({ ...first }, first), false, "identical polls cannot reopen dismissed warning");
assert.equal(show({ ...first, sizeBytes: first.sizeBytes - 1 }, first), false);
assert.equal(show({ ...first, sizeBytes: first.sizeBytes + 1 }, first), true, "new growth can alert again");
assert.equal(show({ ...first, key: "/other|1" }, first), true);
assert.equal(show({ ...first, key: "/fixture|2" }, first), true, "new limit needs a new decision");

const component = await readFile(new URL("../src/components/settings/checkpoint-quota-notice.tsx", import.meta.url), "utf8");
assert.match(component, /fetchCheckpointArchiveUsage\(endpoint, abort.signal\)/);
assert.match(component, /setTimeout\(\(\) => void check\(\), 60_000\)/);
assert.match(component, /abort\.abort\(\)/);
assert.match(component, /clearTimeout\(timer\)/);
assert.match(component, /showModal\(\)/, "must be a visible modal, not a silent status field");
assert.match(component, /acknowledged\.current = null/, "recovering under quota re-arms later overage");
assert.match(component, /dismiss\(\); onManage\(\)/);
assert.doesNotMatch(component, /deleteCheckpoint|loadConnectors|fetchExternalAgentConnectors/);
const app = await readFile(new URL("../src/App.tsx", import.meta.url), "utf8");
assert.match(app, /<CheckpointQuotaNotice/);
assert.match(app, /onManage=\{\(\) => openSettingsSection\("storage"\)\}/);
for (const locale of ["en-US", "zh-CN", "zh-TW", "ja-JP"]) {
  const strings = JSON.parse(await readFile(new URL(`../src/locales/${locale}.json`, import.meta.url), "utf8")).settings;
  for (const key of ["checkpointQuotaTitle", "checkpointQuotaDescription", "checkpointQuotaRetentionHint", "checkpointQuotaLater", "checkpointQuotaManage"]) assert.ok(strings[key], `${locale}: ${key}`);
}
console.log("checkpoint quota thresholds, acknowledgement, and modal wiring passed");
