import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import { build } from "esbuild";
import postcss from "postcss";
import tailwind from "tailwindcss";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const css = (await postcss([tailwind("tailwind.config.ts")]).process(
  "@tailwind base; @tailwind utilities;\n" + await readFile("src/theme.css", "utf8"),
  { from: undefined },
)).css;
const fixtureSource = `
  import React, { useState } from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import { Composer } from "./src/components/chat/composer";
  import { AppUpdatePopup } from "./src/components/ui/app-update-popup";
  i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
  function Fixture() {
    const [level, setLevel] = useState("");
    const [updatePrompt, setUpdatePrompt] = useState(null);
    window.deliverStartupUpdate = () => setUpdatePrompt({ source: "startup", result: { status: "update_available" } });
    return <main className="relative flex h-screen flex-col overflow-hidden">
      <div className="flex-1" data-fixture-outside><button data-set-ultra onClick={() => setLevel("ultra")}>ultra fixture</button></div>
      <Composer input="" setInput={() => {}} sending={true} queueAllowed={true}
        onSubmit={() => {}} onSwitchMode={() => {}} providerLabel="ChatGPT" model="gpt-6.1-sol"
        providerReasoningEndpoint={location.origin} providerReasoningLevel={level} onProviderReasoningActivated={async () => {
          const status = await fetch('/api/chatgpt/status').then(r => r.json());
          setLevel(status.activeThinkingLevel);
        }} />
      <AppUpdatePopup prompt={updatePrompt} automaticCheckEnabled onAutomaticCheckEnabledChange={() => {}} onDismiss={() => setUpdatePrompt(null)} />
    </main>;
  }
  createRoot(document.getElementById("root")).render(<Fixture />);
`;
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: fixtureSource },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
});

const activations = [];
let activeLevel = "";
const server = createServer((req, res) => {
  res.setHeader("Content-Type", "application/json");
  if (req.url === "/api/chatgpt/status" && req.method === "GET") {
    res.end(JSON.stringify({ accounts: [{ id: "oauth-a", label: "OAuth", email: "", connected: true }], pending: false, error: "", activeAccountId: "oauth-a", activeModel: "gpt-6.1-sol", activeThinkingLevel: activeLevel }));
  } else if (req.url === "/api/chatgpt/models" && req.method === "POST") {
    res.end(JSON.stringify({ models: [{ id: "gpt-6.1-sol", name: "GPT 6.1 Sol", supportedReasoningLevels: [{ effort: "low" }, { effort: "high" }, { effort: "ultra" }], defaultReasoningLevel: "high" }] }));
  } else if (req.url === "/api/chatgpt/activate" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      activations.push(JSON.parse(body));
      activeLevel = JSON.parse(body).thinking_level;
      res.end(JSON.stringify({ ok: true }));
    });
  } else if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
  } else if (req.url === "/fixture.css") {
    res.setHeader("Content-Type", "text/css");
    res.end(css);
  } else if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end('<link rel="stylesheet" href="/fixture.css"><div id="root"></div><script src="/fixture.js"></script>');
  } else {
    res.statusCode = 404;
    res.end();
  }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const wait = async (predicate, label) => {
  for (let i = 0; i < 300; i += 1) {
    if (await predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail(`timed out: ${label}`);
};
let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const trigger = page.locator("[data-chatgpt-composer-reasoning-trigger]");
  await wait(async () => (await trigger.count()) === 1 && !(await trigger.isDisabled()), "server-supported levels loaded");
  assert.equal(await trigger.getAttribute("aria-label"), "Reasoning effort", "accessible name is retained");
  assert.match(await trigger.getAttribute("title"), /next turn/, "running tasks retain their existing effort while the next turn can be configured");
  assert.match(await trigger.getAttribute("class"), /border-0/);
  assert.match(await trigger.getAttribute("class"), /bg-transparent/);
  assert.equal(await page.locator("select").count(), 0, "reasoning uses the application menu instead of the native select popup");
  assert.equal(await trigger.getAttribute("data-reasoning-value"), "", "model catalog default is not silently applied");
  await trigger.click();
  const menu = page.getByRole("menu", { name: "Reasoning effort" });
  await menu.waitFor();
  assert.equal(await menu.getByRole("menuitemradio").count(), 3, "the server catalog controls available levels");
  assert.equal(await menu.getByRole("menuitemradio", { checked: true }).count(), 0);
  await page.keyboard.press("Escape");
  assert.equal(await menu.count(), 0);
  assert.equal(activations.length, 0, "dismissal does not change the reasoning level");
  assert.equal(await trigger.evaluate(el => el === document.activeElement), true);
  await page.keyboard.press("ArrowDown");
  await menu.waitFor();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await wait(() => activations.length === 1, "selected level activated through existing API");
  assert.deepEqual(activations[0], { account_id: "oauth-a", model: "gpt-6.1-sol", thinking_level: "high" });
  await wait(async () => (await trigger.getAttribute("data-reasoning-value")) === "high", "refreshed composer shows active level");
  await trigger.click();
  assert.equal(await menu.getByRole("menuitemradio", { checked: true }).innerText(), "High");
  await page.locator("[data-fixture-outside]").click();
  assert.equal(await menu.count(), 0, "outside click dismisses the menu");
  await page.locator("[data-set-ultra]").click();
  await wait(async () => (await trigger.getAttribute("data-reasoning-value")) === "ultra", "ultra fixture selection displayed");
  assert.match(await trigger.getAttribute("class"), /text-violet-600 dark:text-violet-400/, "ultra uses theme-aware purple while normal levels remain muted");
  await trigger.click();
  const lowOption = menu.getByRole("menuitemradio", { name: "Low" });
  const optionBox = await lowOption.boundingBox();
  assert.ok(optionBox);
  await page.evaluate(() => window.deliverStartupUpdate());
  const update = page.locator("[data-vrcforge-app-update-popup]");
  await update.waitFor();
  assert.equal(await page.evaluate(({ x, y, width, height }) => (
    Boolean(document.elementFromPoint(x + width / 2, y + height / 2)?.closest("[data-vrcforge-app-update-popup]"))
  ), optionBox), true, "the update modal intercepts clicks at the former option's position");
  assert.equal(await menu.count(), 0, "modal focus dismisses the workspace menu");
  assert.equal(await update.locator("[data-vrcforge-app-update-dismiss]").evaluate(el => el === document.activeElement), true);
  await page.keyboard.press("Enter");
  assert.equal(await update.count(), 0, "keyboard input acts on the update dialog");
  assert.equal(activations.length, 1, "the open menu cannot change a model setting after the modal takes focus");
  console.log("ChatGPT OAuth composer reasoning: no silent default, server levels displayed, selected level activated and reflected");
} finally {
  if (browser) await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
