// Browser contract for the real opt-in dedicated image-routing setting.
// This mounts VisionProfileSetup and useProviderSettings; it is intentionally
// not a checkbox-only fixture so the save payload and persisted config path are covered.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");

const mockModules = {
  i18n: `export default { t: (key, fallback) => fallback || key };`,
  "react-i18next": `export const useTranslation = () => ({ t: (key, fallback) => fallback || key });`,
  api: `export const fetchProviderModels = async () => ({ models: [] });
export const fetchReasoningVariants = async () => null;
export const testProviderCapability = async () => ({ ok: true });
export const updateApiConfig = async () => ({});
export const updateVisionConfig = async (_endpoint, payload) => {
  globalThis.__visionUpdates = [...(globalThis.__visionUpdates || []), payload];
  return { visionConfig: payload };
};`,
  "provider-ui": `export const defaultBaseUrlForProvider = () => "";
export const defaultModelForProvider = () => "gpt-4.1-mini";
export const providerDisplayName = (provider) => provider;
export const providerNeedsApiKey = (provider) => provider === "openai";
export const providerCapabilities = () => [];`,
  "provider-protocol-options": `export const supportedApiTypeOptions = () => [];`,
  "context-compaction": `export const resolveContextLimit = () => ({ known: false });`,
  badge: `import React from "react"; export const Badge = ({ children, ...props }) => <span {...props}>{children}</span>;`,
  button: `import React from "react"; export const Button = ({ children, ...props }) => <button {...props}>{children}</button>;`,
  "lucide-react": `import React from "react"; const Icon = (props) => <span {...props} />;
export const Check=Icon, Eye=Icon, Loader2=Icon, MessageSquare=Icon, RefreshCw=Icon;`,
};

const plugin = {
  name: "vision-test-mocks",
  setup(buildApi) {
    buildApi.onResolve({ filter: /(?:^|[\\/])(?:i18n|react-i18next|api|provider-ui|provider-protocol-options|context-compaction|badge|button|lucide-react)$/ }, (args) => {
      const key = args.path.split(/[\\/]/).pop();
      return { path: key, namespace: "vision-test-mock" };
    });
    buildApi.onLoad({ filter: /.*/, namespace: "vision-test-mock" }, (args) => ({
      contents: mockModules[args.path],
      loader: "tsx",
      resolveDir: process.cwd(),
    }));
  },
};

const entry = `
import React from "react";
import { createRoot } from "react-dom/client";
import { VisionProfileSetup } from "./src/components/settings/provider-settings";
import { useProviderSettings } from "./src/hooks/use-provider-settings";

function Fixture() {
  const [visionConfig, setVisionConfig] = React.useState({
    provider: "", model: "", configured: false, apiKeyPresent: false,
  });
  const [error, setError] = React.useState("");
  const settings = useProviderSettings({
    endpoint: location.origin, runtimeConnected: true,
    apiConfig: { provider: "openai", model: "gpt-4.1-mini", configured: true, apiKeyPresent: true },
    visionConfig,
    startRuntime: async () => location.origin,
    refresh: async () => {}, setError,
  });
  React.useEffect(() => {
    globalThis.__reloadVision = (patch) => setVisionConfig((current) => ({ ...current, ...patch }));
  }, []);
  return <>
    <VisionProfileSetup
      provider={settings.visionProvider} apiKey={settings.visionApiKey}
      baseUrl={settings.visionBaseUrl} model={settings.visionModel}
      enabled={settings.visionEnabled} saving={settings.savingVisionConfig}
      runtimeConnected keySaved configured
      onProviderChange={settings.handleVisionProviderChange}
      onApiKeyChange={settings.setVisionApiKey}
      onBaseUrlChange={settings.setVisionBaseUrl}
      onModelChange={settings.setVisionModel}
      onEnabledChange={settings.setVisionEnabled}
      onSubmit={settings.saveVisionProfile} onClear={settings.clearVisionProfile}
    />
    <pre data-state>{JSON.stringify({ enabled: settings.visionEnabled, error })}</pre>
  </>;
}
createRoot(document.getElementById("root")).render(<Fixture />);
`;
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: "tsx", contents: entry }, bundle: true, write: false, format: "iife", jsx: "automatic", plugins: [plugin] });
const server = createServer((_request, response) => {
  response.writeHead(200, { "content-type": "text/html" });
  response.end(`<div id="root"></div><script>${bundle.outputFiles[0].text}</script>`);
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  const toggle = page.locator('input[type="checkbox"]');
  const state = () => page.locator("[data-state]").textContent();
  await page.waitForFunction(() => document.querySelector("[data-state]")?.textContent.includes('"enabled":false'));
  assert.equal(await toggle.isChecked(), false, "missing enabled defaults off in the real hook");
  assert.equal(await page.locator("select").inputValue(), "", "dedicated switch remains visible before a provider is selected");
  const save = page.getByRole("button", { name: "common.save" });
  const updates = () => page.evaluate(() => globalThis.__visionUpdates || []);
  await save.click();
  await page.waitForFunction(() => (globalThis.__visionUpdates || []).length === 1);
  assert.equal((await updates())[0].enabled, false, "off save persists enabled=false");
  await page.locator("select").selectOption("openai");
  await toggle.check();
  await save.click();
  await page.waitForFunction(() => (globalThis.__visionUpdates || []).length === 2);
  assert.equal((await updates())[1].enabled, true, "on save persists enabled=true");
  await toggle.uncheck();
  await save.click();
  await page.waitForFunction(() => (globalThis.__visionUpdates || []).length === 3);
  assert.equal((await updates())[2].enabled, false, "off save persists enabled=false");
  await page.evaluate(() => globalThis.__reloadVision({ enabled: true, provider: "openai" }));
  await page.waitForFunction(() => document.querySelector("[data-state]")?.textContent.includes('"enabled":true'));
  assert.equal(await toggle.isChecked(), true, "reload preserves an existing enabled=true setting");
  await page.evaluate(() => globalThis.__reloadVision({ enabled: false }));
  await page.waitForFunction(() => document.querySelector("[data-state]")?.textContent.includes('"enabled":false'));
  assert.equal(await toggle.isChecked(), false, "reload preserves an existing enabled=false setting");
  await page.evaluate(() => globalThis.__reloadVision({ provider: "openai", model: "", enabled: false }));
  await page.waitForFunction(() => document.querySelector("select")?.value === "openai");
  await save.click();
  await page.waitForFunction(() => (globalThis.__visionUpdates || []).length === 4);
  assert.deepEqual((await updates())[3], {
    provider: "openai", api_key: "", base_url: "", model: "", enabled: false,
  }, "disabled incomplete profile can still be saved and remains disabled");
  console.log("vision routing browser: real VisionProfileSetup/useProviderSettings default, save, and reload persistence passed");
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
}
