import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const importTypeScript = async (relativePath, transform = (source) => source) => {
  const sourcePath = path.join(root, relativePath);
  const source = transform(await readFile(sourcePath, "utf8"));
  const transpiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2020 },
    fileName: sourcePath,
  }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(transpiled).toString("base64")}`);
};

const checklistLogic = await importTypeScript("src/components/onboarding/onboarding-checklist-state.ts");
const gateLogicSource = await readFile(
  path.join(root, "src/components/onboarding/onboarding-language-gate-state.ts"),
  "utf8",
);
const gateLogic = await importTypeScript(
  "src/components/onboarding/onboarding-language-gate-state.ts",
  (source) => source
    .replace(
      'import { LOCALE_STORAGE_KEY, SUPPORTED_LOCALES } from "../../locales";',
      'const LOCALE_STORAGE_KEY = "vrcforge-locale"; const SUPPORTED_LOCALES = [{ code: "en-US" }, { code: "ja-JP" }, { code: "zh-CN" }, { code: "zh-TW" }];',
    )
    .replace(
      'import { ONBOARDING_FLAG_KEY } from "../../lib/app-preferences";',
      'const ONBOARDING_FLAG_KEY = "vrcforge_onboarded";',
    ),
);
const [overlay, languageGate, app] = await Promise.all([
  readFile(path.join(root, "src/components/onboarding/onboarding-overlay.tsx"), "utf8"),
  readFile(path.join(root, "src/components/onboarding/onboarding-language-gate.tsx"), "utf8"),
  readFile(path.join(root, "src/App.tsx"), "utf8"),
]);

assert.deepEqual(checklistLogic.onboardingChecklistItemState(true, true), { completion: "done", position: "current" });
assert.deepEqual(checklistLogic.onboardingChecklistItemState(true, false), { completion: "done", position: "other" });
assert.deepEqual(checklistLogic.onboardingChecklistItemState(false, true), { completion: "pending", position: "current" });
assert.deepEqual(checklistLogic.onboardingChecklistItemState(false, false), { completion: "pending", position: "other" });

const resolve = (stored, smokeMode = false) => gateLogic.resolveOnboardingLaunchState(stored, smokeMode);
const savedLocaleStorage = new Map([
  ["vrcforge-locale", "ja-JP"],
]);
assert.deepEqual(gateLogic.readOnboardingStoredState(() => ({
  getItem: (key) => savedLocaleStorage.get(key) || null,
  setItem: (key, value) => savedLocaleStorage.set(key, value),
})), {
  onboardingComplete: false,
  hasSavedLocale: true,
  languageGateComplete: false,
});
assert.deepEqual(gateLogic.readOnboardingStoredState(() => { throw new Error("blocked"); }), {
  onboardingComplete: false,
  hasSavedLocale: false,
  languageGateComplete: false,
});
assert.deepEqual(resolve({ onboardingComplete: false, hasSavedLocale: false, languageGateComplete: false }), {
  showOnboarding: true,
  showLanguageGate: true,
  migrateLanguageGateCompletion: false,
});
assert.deepEqual(resolve({ onboardingComplete: false, hasSavedLocale: true, languageGateComplete: false }), {
  showOnboarding: true,
  showLanguageGate: false,
  migrateLanguageGateCompletion: true,
});
assert.deepEqual(resolve({ onboardingComplete: true, hasSavedLocale: false, languageGateComplete: false }), {
  showOnboarding: false,
  showLanguageGate: false,
  migrateLanguageGateCompletion: true,
});
assert.deepEqual(resolve({ onboardingComplete: false, hasSavedLocale: false, languageGateComplete: true }), {
  showOnboarding: true,
  showLanguageGate: false,
  migrateLanguageGateCompletion: false,
});
assert.deepEqual(resolve({ onboardingComplete: false, hasSavedLocale: false, languageGateComplete: false }, true), {
  showOnboarding: false,
  showLanguageGate: false,
  migrateLanguageGateCompletion: false,
});
assert.equal(gateLogic.persistOnboardingLanguageGateCompletion(() => ({ setItem: () => { throw new Error("blocked"); } })), false);
assert.equal(gateLogic.persistOnboardingLanguageGateCompletion(() => ({
  getItem: (key) => savedLocaleStorage.get(key) || null,
  setItem: (key, value) => savedLocaleStorage.set(key, value),
})), true);
assert.equal(savedLocaleStorage.get("vrcforge_onboarding_language_gate_completed"), "true");

assert.ok(gateLogicSource.includes('ONBOARDING_LANGUAGE_GATE_FLAG_KEY = "vrcforge_onboarding_language_gate_completed"'));
assert.ok(overlay.includes("data-vrcforge-onboarding-checklist"));
assert.ok(overlay.includes("data-state={state.completion}"));
assert.ok(overlay.includes("data-position={state.position}"));
assert.ok(overlay.includes("data-vrcforge-onboarding-checklist-item"));
assert.ok(overlay.includes("data-vrcforge-onboarding-skip"));
assert.ok(overlay.includes('aria-current={state.position === "current" ? "step" : undefined}'));
assert.match(overlay, /state\.completion === "done" \? \([\s\S]*?<Check[\s\S]*?: \([\s\S]*?<Circle/);
assert.ok(overlay.includes("onboardingChecklistVisualClasses.item[state.position]"));
assert.ok(overlay.includes("onboardingChecklistVisualClasses.icon[state.completion]"));
assert.ok(overlay.includes("SUPPORTED_LOCALES.map"));
assert.ok(overlay.includes('aria-label={t("settings.language")}'));
assert.ok(!overlay.includes('"h-1.5 flex-1 rounded-full transition-colors"'));

assert.ok(languageGate.includes('role="dialog"'));
assert.ok(languageGate.includes('aria-modal="true"'));
assert.ok(languageGate.includes("SUPPORTED_LOCALES.map"));
assert.ok(languageGate.includes('data-vrcforge-onboarding-language-gate="true"'));
assert.ok(languageGate.includes("data-vrcforge-onboarding-language-option={locale.code}"));
assert.ok(languageGate.includes("data-vrcforge-onboarding-language-continue"));
assert.ok(languageGate.includes("data-state={selectionState}"));
assert.ok(languageGate.includes('aria-pressed={selectionState === "selected"}'));
assert.ok(languageGate.includes("LANGUAGE_OPTION_VISUAL_CLASSES[selectionState]"));
assert.ok(languageGate.includes("onContinue(selectedLocale)"));

assert.ok(app.includes("resolveOnboardingLaunchState(readOnboardingStoredState(), smokeMode)"));
assert.ok(app.includes("initialOnboardingState.migrateLanguageGateCompletion"));
assert.ok(app.includes("persistOnboardingLanguageGateCompletion();\n    setShowOnboardingLanguageGate(false);"));
assert.ok(app.includes("setShowOnboardingLanguageGate(false);\n    setShowOnboarding(true);"));
assert.ok(app.includes("open={showOnboarding && showOnboardingLanguageGate}"));
assert.ok(app.includes("open={showOnboarding && !showOnboardingLanguageGate}"));

console.log("onboarding checklist/language gate contract: ok");
