import assert from "node:assert/strict";
import Module from "node:module";
import { resolve } from "node:path";
import { execFileSync } from "node:child_process";
import { build } from "esbuild";

const root = resolve(import.meta.dirname, "..");
const fixtureSource = `
  import React from "react";
  import { renderToStaticMarkup } from "react-dom/server";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import { OnboardingOverlay } from "./src/components/onboarding/onboarding-overlay";

  i18n.use(initReactI18next).init({
    lng: "en-US",
    resources: { "en-US": { translation: {
      onboarding: {
        welcome: "Welcome", stepProgress: "Step {{current}} of {{total}}",
        step1Title: "Unity", step1DoneDesc: "Unity connected", step1TodoDesc: "Connect Unity",
        toolsConnected: "{{count}} tools connected", importAndSelectProject: "Select a project",
        keepUnityOpen: "Keep Unity open", connecting: "Connecting", retryConnection: "Retry",
        step2Title: "Provider", step2DoneDesc: "Provider verified", step2TodoDesc: "Configure provider",
        providerSavedPendingDescription: "Provider settings saved; verify the provider to continue.",
        providerSavedPending: "Verification pending",
        step3Title: "Project", step3DoneDesc: "Project selected", step3TodoDesc: "Select project",
        selectProject: "Select project", internalProviderTitle: "Internal", internalProviderDesc: "Internal provider",
        configureInternal: "Configure internal", externalAgentTitle: "External", externalAgentDesc: "External agent",
        configureExternal: "Configure external", done: "Done", actionNeeded: "Action needed", detecting: "Detecting",
        continueOnboarding: "Continue", skipOnboarding: "Skip", prevStep: "Previous", nextStep: "Next",
        startUsing: "Start using"
      }, settings: { language: "Language" }
    } } }, interpolation: { escapeValue: false }
  });

  const callbacks = { onRetryRuntime(){}, onOpenSettings(){}, onOpenExternalSettings(){},
    onOpenProjectPicker(){}, onResume(){}, onFinish(){}, onPreviousStep(){}, onNextStep(){}, onLocaleChange(){} };
  const render = (providerConfigured, providerVerified, externalAgentReady = false) => renderToStaticMarkup(
    React.createElement(OnboardingOverlay, {
      open: true, minimized: false, stepIndex: 0, runtimeConnected: true,
      selectedProjectReady: true, projectType: "non-unity", unityToolsReady: false,
      unityToolsCount: 0, providerConfigured, providerVerified, externalAgentReady,
      loadingRuntime: false, currentLanguage: "en-US", ...callbacks
    })
  );
  export function run() {
    return {
      unconfigured: render(false, false),
      saved: render(true, false),
      verified: render(true, true),
      external: render(false, false, true)
    };
  }
`;

async function compileFixture(overlaySource) {
  const compiled = await build({
    stdin: { resolveDir: root, loader: "tsx", contents: fixtureSource },
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
    jsx: "automatic",
    plugins: overlaySource ? [{
      name: "baseline-overlay",
      setup(pluginBuild) {
        pluginBuild.onLoad({ filter: /onboarding-overlay/ }, () => ({ contents: overlaySource, loader: "tsx" }));
      },
    }] : [],
  });
  const filename = resolve(root, "tests", "onboarding-saved-state-fixture.cjs");
  const fixture = new Module(filename);
  fixture.paths = Module._nodeModulePaths(root);
  fixture._compile(compiled.outputFiles[0].text, filename);
  return fixture.exports.run();
}

const states = await compileFixture();

assert.match(states.unconfigured, /Configure provider/, "unconfigured provider keeps the setup guidance");
assert.doesNotMatch(states.unconfigured, /Verification pending|Provider settings saved/, "unconfigured provider has no saved-state wording");

assert.match(states.saved, /Provider settings saved; verify the provider to continue\./, "saved provider explains verification is still pending");
assert.match(states.saved, /Verification pending/, "saved provider exposes the pending badge");
assert.doesNotMatch(states.saved, /Provider verified/, "saved provider is not presented as verified");
assert.match(states.saved, /data-state="pending" data-position="current"[^>]*aria-current="step"[^>]*aria-label="Provider:/, "saved provider remains pending");
assert.match(states.saved, /disabled="">Next<\/button>/, "saved provider keeps Next disabled");

assert.match(states.verified, /data-state="done" data-position="current"[^>]*aria-current="step"[^>]*aria-label="Provider: Done"/, "verified provider is complete");
assert.match(states.verified, /Provider verified/, "verified provider shows its completed description");
assert.match(states.verified, /<button[^>]*>Next<\/button>/, "verified provider enables Next");
assert.doesNotMatch(states.verified, /disabled="">Next<\/button>/, "verified provider does not disable Next");

assert.match(states.external, /data-state="done" data-position="current"[^>]*aria-current="step"[^>]*aria-label="Provider: Done"/, "external agent completion is complete");
assert.doesNotMatch(states.external, /disabled="">Next<\/button>/, "external agent completion enables Next");

const baselineArg = process.argv.find((arg) => arg.startsWith("--baseline="));
if (baselineArg) {
  const baseline = baselineArg.slice("--baseline=".length).trim();
  assert.match(baseline, /^[A-Za-z0-9._/-]+$/, "baseline ref must be a simple git ref");
  const oldOverlay = execFileSync("git", ["show", `${baseline}:src/components/onboarding/onboarding-overlay.tsx`], {
    cwd: root, encoding: "utf8",
  });
  const oldStates = await compileFixture(oldOverlay);
  assert.doesNotMatch(oldStates.saved, /Provider settings saved; verify the provider to continue\./,
    "baseline is red: it lacks the saved-state guidance");
  assert.match(oldStates.saved, /Configure provider/, "baseline is red: it keeps the old setup action");
  console.log(`baseline ${baseline}: RED / current working tree: GREEN`);
}

console.log("onboarding saved-state regression: old RED / current GREEN");
