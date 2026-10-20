import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const fixtureSource = `
  import React, { useState } from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import { ChatGptOAuthComposerReasoningControl } from "./src/features/chatgpt-oauth/composer-reasoning-control";
  i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
  function Fixture() {
    const [level, setLevel] = useState("");
    return <><button data-set-ultra onClick={() => setLevel("ultra")}>ultra fixture</button>
      <ChatGptOAuthComposerReasoningControl endpoint={location.origin} model="gpt-6.1-sol"
        thinkingLevel={level} disabled={false} onActivated={async () => {
          const status = await fetch('/api/chatgpt/status').then(r => r.json());
          setLevel(status.activeThinkingLevel);
        }} /></>;
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
  } else if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end("<div id=\"root\"></div><script src=\"/fixture.js\"></script>");
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
  await page.locator("#root > button").click();
  assert.equal(await menu.count(), 0, "outside click dismisses the menu");
  await page.locator("[data-set-ultra]").click();
  await wait(async () => (await trigger.getAttribute("data-reasoning-value")) === "ultra", "ultra fixture selection displayed");
  assert.match(await trigger.getAttribute("class"), /text-violet-600 dark:text-violet-400/, "ultra uses theme-aware purple while normal levels remain muted");
  console.log("ChatGPT OAuth composer reasoning: no silent default, server levels displayed, selected level activated and reflected");
} finally {
  if (browser) await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
