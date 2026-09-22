import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = await readFile(path.join(root, "src/App.tsx"), "utf8");
const providerHook = await readFile(path.join(root, "src/hooks/use-provider-settings.ts"), "utf8");

const effectMatch = app.match(
  /useEffect\(\(\) => \{\s*(if \(showOnboarding && onboardingMinimized && providerSetupGuide && providerReadyForOnboarding\) \{[\s\S]*?\n\s*\})\s*\}, \[showOnboarding, onboardingMinimized, providerSetupGuide, providerReadyForOnboarding\]\);/,
);
assert.ok(effectMatch, "provider setup return effect must remain in App");
const effect = effectMatch[1];
assert.match(effect, /setActiveView\("chat"\)/);
assert.match(effect, /setProviderSetupGuide\(false\)/);
assert.match(effect, /setOnboardingMinimized\(false\)/);
assert.doesNotMatch(effect, /setShowOnboarding|setOnboardingStep|setOnboardingComplete|finish/i);

assert.match(app, /const \{[\s\S]*?providerReadyForOnboarding[\s\S]*?\} = useProviderSettings\(/);
assert.match(app, /providerReadyForOnboarding\}\s*\/\/|providerReadyForOnboarding\s*[,}]/);
assert.match(
  providerHook,
  /providerReadyForOnboarding:\s*providerTestPassed && persistedProviderConfigured && !apiKey\.trim\(\) && providerTestFingerprint === persistedProviderFingerprint/,
);

const runEffect = (overrides = {}) => {
  const state = {
    activeView: "settings",
    providerSetupGuide: true,
    onboardingMinimized: true,
    onboardingStep: 2,
    onboardingComplete: false,
    ...overrides,
  };
  const inputs = {
    showOnboarding: overrides.showOnboarding ?? true,
    onboardingMinimized: overrides.onboardingMinimized ?? state.onboardingMinimized,
    providerSetupGuide: overrides.providerSetupGuide ?? state.providerSetupGuide,
    providerReadyForOnboarding: overrides.providerReadyForOnboarding ?? true,
  };
  const calls = [];
  const set = (key) => (value) => {
    calls.push([key, value]);
    state[key] = value;
  };
  new Function(
    "showOnboarding",
    "onboardingMinimized",
    "providerSetupGuide",
    "providerReadyForOnboarding",
    "setActiveView",
    "setProviderSetupGuide",
    "setOnboardingMinimized",
    effect,
  )(
    inputs.showOnboarding,
    inputs.onboardingMinimized,
    inputs.providerSetupGuide,
    inputs.providerReadyForOnboarding,
    set("activeView"),
    set("providerSetupGuide"),
    set("onboardingMinimized"),
  );
  return { state, calls };
};

const returned = runEffect();
assert.deepEqual(returned.calls, [
  ["activeView", "chat"],
  ["providerSetupGuide", false],
  ["onboardingMinimized", false],
]);
assert.equal(returned.state.onboardingStep, 2);
assert.equal(returned.state.onboardingComplete, false);

for (const gate of ["showOnboarding", "onboardingMinimized", "providerSetupGuide", "providerReadyForOnboarding"]) {
  const result = runEffect({ [gate]: false });
  assert.deepEqual(result.calls, [], `${gate}=false must not return to chat`);
  assert.equal(result.state.activeView, "settings");
  assert.equal(result.state.providerSetupGuide, gate === "providerSetupGuide" ? false : true);
  assert.equal(result.state.onboardingMinimized, gate === "onboardingMinimized" ? false : true);
  assert.equal(result.state.onboardingStep, 2);
  assert.equal(result.state.onboardingComplete, false);
}

console.log("onboarding provider return contract: ok");
