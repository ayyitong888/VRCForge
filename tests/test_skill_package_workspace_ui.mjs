import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const source = await readFile(
  new URL("../src/components/skills/skill-package-manager-panel.tsx", import.meta.url),
  "utf8",
);
const api = await readFile(new URL("../src/lib/api/skill-packages.ts", import.meta.url), "utf8");
const config = await readFile(new URL("../src-tauri/tauri.conf.json", import.meta.url), "utf8");
const composer = await readFile(new URL("../src/components/chat/composer.tsx", import.meta.url), "utf8");
const workspace = await readFile(new URL("../src/components/skills/skills-workspace.tsx", import.meta.url), "utf8");
const locales = await Promise.all(["zh-CN", "zh-TW", "en-US", "ja-JP"].map(async (locale) =>
  JSON.parse(await readFile(new URL(`../src/locales/${locale}.json`, import.meta.url), "utf8"))
));

assert.ok(source.includes("getCurrentWebview"), "desktop drag and drop must use the Tauri webview event");
assert.ok(source.includes("onDragDropEvent"), "desktop drag and drop must receive absolute dropped paths from Tauri");
assert.ok(source.includes("payload.paths.find"), "drop handler must filter paths from the native event");
assert.ok(source.includes('.endsWith(".vsk")'), "drop handler must reject non-vsk files");
assert.ok(source.includes("onPreflight(path)"), "drop import must preflight through the existing backend API");
assert.ok(source.includes("onImport(path)"), "drop import must use the existing backend import API");
assert.ok(source.includes("data-vrcforge-skill-dropzone"), "skill package drop zone needs a semantic marker");
assert.ok(source.includes("data-vrcforge-skill-package-enabled"), "package enable control needs a semantic marker");
assert.ok(source.includes("runSetEnabled(id, !enabled)"), "enable switch must use the existing package state API");
assert.ok(source.includes("onExport(exportSkillName.trim()"), "export must use the existing package export API");
assert.ok(api.includes('fetch_official_skill_signing_key'), "official key status must use the typed Tauri command");
assert.ok(api.includes("payload.key || {}"), "official key status must unwrap the backend key envelope");
assert.ok(api.includes("publicKeyPath"), "official key status must preserve the public key path field");
assert.ok(source.includes("<details"), "advanced governance/export areas should be collapsible");
assert.ok(source.includes("role=\"switch\""), "package enable control must use the switch affordance");
assert.match(source, /relative h-7 w-12 shrink-0 overflow-hidden/, "skill switches must clip the moving thumb to their own track");
assert.match(source, /absolute left-0 top-1\/2 h-5 w-5 -translate-y-1\/2/, "skill switch thumbs must share their track's vertical center while remaining anchored inside its left edge");
assert.match(source, /translate-x-\[26px\].*translate-x-\[2px\]/, "skill switch thumb positions must be symmetric inside the 48px track");
assert.ok(source.includes("break-words font-medium") && source.includes("break-all text-muted-foreground"), "long official Skill names and IDs must wrap instead of truncating");
assert.ok(source.includes("advancedActions"), "package governance actions must be behind an advanced disclosure");
assert.ok(source.includes("preparePackageExport(pkg)"), "package rows must offer export preparation");
assert.match(source, /pkg\.title \|\| pkg\.name \|\| pkg\.manifest\?\.title \|\| pkg\.manifest\?\.name/, "installed package titles must use their existing manifest display names");
assert.ok(source.includes('if (pkg.official === true)'), "Official must require an explicitly verified backend identity");
assert.ok(source.includes('labels.push("Official")'), "verified official signer identity must be displayed");
assert.ok(source.includes("skillPackageExecutionMode(pkg)"), "installed skills must expose one execution mode concept");
assert.ok(source.includes("pkg.execution || manifest.execution"), "canonical execution field must take precedence over compatibility aliases");
assert.ok(source.includes("package.executionMode."), "execution mode must use localized labels");
assert.ok(source.includes("skillPackageExecutionDescription(pkg)"), "execution mode must explain deterministic gates or agent-guided selection");
assert.ok(source.includes("executionPlan"), "signed deterministic execution plans must remain visible to the frontend");
assert.ok(source.includes("skillPackageRiskLabel(pkg)"), "risk badges must use localized human-readable labels");
assert.ok(source.includes("package.risk.${key}"), "risk levels must map through localized labels");
assert.ok(locales.every((locale) => locale.package.executionMode.agentic && locale.package.risk.high), "all locales must expose smart execution and risk labels");
assert.ok(source.includes("skillPackageMessageTone(displayMessage)"), "package status messages must use semantic tones");
assert.ok(source.includes('message === i18n.t("package.messages.packageDisabled")') && source.includes('return "muted"'), "disabled status must never use the success tone");
assert.ok(locales.every((locale) => /permission|权限|權限|権限/.test(locale.package.executionMode.deterministicDescription)), "deterministic mode must follow the current permission mode");
assert.ok(source.includes('return ["deterministic", "fixed", "fixed_steps", "fixed-steps"].includes(raw) ? "deterministic" : "agentic"'), "legacy packages must default to agentic mode");
assert.ok(!source.includes('pkg.author === "VRCForge"'), "an author declaration alone must never grant Official identity");
assert.ok(source.includes("data-vrcforge-installed-skill-card"), "installed skills must render as independent cards");
assert.match(source, /flex min-w-0 flex-wrap items-center justify-end gap-2/, "package actions must retain spacing around the switch");
assert.match(source, /<details\s+open\s+className="[^"]*"\s+data-vrcforge-installed-skills>/, "the VSK package manager must itself be collapsible and initially expanded");
assert.match(source, /<summary className="[^"]*">\s*<Shield/, "the VSK package manager needs a visible collapsible header");
assert.ok(workspace.includes("showBuiltinSkills"), "built-in tools must be opt-in");
assert.ok(workspace.includes("skill.source !== \"builtin\""), "default skill list must hide built-in tools");
assert.ok(workspace.includes("data-vrcforge-skill-editor"), "skill editor must be a collapsed secondary section");
assert.ok(workspace.includes("data-vrcforge-show-builtin-skills"), "built-in visibility must have a clear toggle");
assert.ok(workspace.indexOf("<SkillPackageManagerPanel") < workspace.indexOf("data-vrcforge-skill-catalog"), "installed VSK panel must be the first workspace card in the DOM");
assert.ok(workspace.includes('data-vrcforge-skill-catalog'), "skill catalog must be a collapsible card");
assert.ok(workspace.includes('data-vrcforge-skill-editor'), "skill editor must be a collapsible card");
assert.match(workspace, /<details className="[^"]*" data-vrcforge-skill-catalog>/, "the skill catalog must be collapsed initially");
assert.match(workspace, /<details className="[^"]*\border-last\b[^"]*" data-vrcforge-skill-catalog>/, "the skill catalog must remain the last workspace card");
assert.match(workspace, /<details className="[^"]*" data-vrcforge-skill-editor>/, "the skill editor must be collapsed initially");
assert.ok(source.indexOf("packages.map") < source.indexOf('package.governance'), "installed package cards must precede governance");
assert.ok(source.indexOf("packages.map") < source.indexOf('data-vrcforge-skill-dropzone'), "installed package cards must precede import dropzone");
assert.ok(!source.includes("grid-cols-[minmax(0,1fr)_76px_150px_minmax(300px,390px)]"), "installed packages must not use the wide table layout");
assert.ok(source.includes("isInsidePackageDropzone"), "skill native drops must be scoped to the dropzone bounds");
assert.ok(source.includes('i18n.t("package.dropHint")'), "skill drop copy must be localized");
assert.ok(composer.includes("onDragDropEvent"), "Composer must retain native drag/drop support when Tauri native drops are enabled");
assert.ok(composer.includes("composerRef.current.getBoundingClientRect"), "Composer native drops must be scoped to its bounds");
assert.ok(composer.includes("convertFileSrc(path)"), "native dropped files must use Tauri's exact dropped-path asset scope");
assert.ok(composer.includes("onAttachFiles?.(files)"), "native dropped files must use the existing attachment ingest");
for (const locale of locales) {
  assert.ok(locale.package.dropHint && locale.package.dropInvalid, "all locales must define native skill drop copy");
  assert.ok(locale.package.pathToSkillAdvanced && locale.package.exportSection, "all locales must define advanced section labels");
}
assert.ok(!locales[0].package.pathToSkillAdvanced.includes("工作流转"), "operation capture must not present workflows and Skills as separate capability types");
assert.ok(locales[0].package.pathToSkill.description.includes("原子工具"), "Skill authoring must explain that execution steps and atomic tools belong to the same Skill");
assert.ok(JSON.parse(config).app.security.csp.includes("asset:"), "asset protocol must be allowed for exact native dropped paths");
assert.equal(JSON.parse(config).app.windows[0].dragDropEnabled, true, "Tauri native drag/drop must be enabled");

console.log("skill package workspace drag/drop, toggle, export, and disclosure UI contract passed");
