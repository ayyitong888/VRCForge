// Real React/browser regression for the ChatGPT OAuth status card.
// The fixture uses synthetic status and provider-test values only; it never
// contacts the OAuth provider.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { build } from "esbuild";
import postcss from "postcss";
import tailwindcss from "tailwindcss";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const fixtureSource = `
  import React from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import zh from "./src/locales/zh-CN.json";
  import { ChatGptOAuthSettings } from "./src/features/chatgpt-oauth/settings";
  const query = new URLSearchParams(location.search);
  i18n.use(initReactI18next).init({ lng: query.get("lang") || "en-US", resources: { "en-US": { translation: en }, "zh-CN": { translation: zh } } });
  function Fixture() {
    return <ChatGptOAuthSettings
      endpoint={location.origin}
      runtimeConnected={true}
      apiProvider="chatgpt"
      apiModel="gpt-6.1-sol"
      savingApiConfig={false}
      testingProvider=""
      providerTestMessage={query.get("verified") === "1"
        ? "text: ok - Provider test succeeded. (gpt-6.1-sol / responses: verified)"
        : "text: error - Provider rejected the request."}
      providerTestPassed={query.get("verified") === "1"}
      developerOptionsEnabled={query.get("developer") === "1"}
      onTestProvider={() => {}}
      onProviderConfigChanged={() => {}}
    />;
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

const stylesheetPath = resolve("src/styles.css");
const stylesheet = await postcss([tailwindcss()]).process(readFileSync(stylesheetPath, "utf8"), { from: stylesheetPath });
let pending = false;
const server = createServer((req, res) => {
  if (req.url?.startsWith("/api/chatgpt/status") && req.method === "GET") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({
      accounts: [{ id: "current-account", label: "Current account", email: "", connected: true }],
      pending,
      error: "",
      activeAccountId: "current-account",
      activeModel: "gpt-6.1-sol",
    }));
    return;
  }
  if (req.url === "/api/chatgpt/models" && req.method === "POST") {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ models: [{ id: "gpt-6.1-sol", name: "gpt-6.1-sol" }] }));
    return;
  }
  if (req.url === "/fixture.css") {
    res.setHeader("Content-Type", "text/css");
    res.end(stylesheet.css);
    return;
  }
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  if (new URL(req.url, "http://fixture.invalid").pathname === "/") {
    pending = new URL(req.url, "http://fixture.invalid").searchParams.get("pending") === "1";
    res.setHeader("Content-Type", "text/html");
    res.end('<!doctype html><link rel="stylesheet" href="/fixture.css"><div id="root" class="p-4"></div><script src="/fixture.js"></script>');
    return;
  }
  res.statusCode = 404;
  res.end();
});
await new Promise((resolveServer) => server.listen(0, "127.0.0.1", resolveServer));

let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage({ viewport: { width: 420, height: 800 } });
  const wait = async (predicate, label) => {
    for (let i = 0; i < 300; i += 1) {
      if (await predicate()) return;
      await new Promise((resolveWait) => setTimeout(resolveWait, 10));
    }
    assert.fail(`timed out: ${label}`);
  };

  await page.goto(`http://127.0.0.1:${server.address().port}/?verified=0&pending=0`);
  await wait(async () => (await page.locator("select").first().inputValue()) === "current-account", "connected account selection");
  const unverifiedBadge = page.locator("[data-chatgpt-active-badge]");
  assert.ok((await unverifiedBadge.getAttribute("class")).includes("amber"), "configured but unverified state stays yellow");
  assert.equal(await page.getByText("Connection test needs to be run again.", { exact: false }).count(), 1);
  assert.equal(await page.getByText("text: ok - Provider test succeeded.", { exact: false }).count(), 0, "technical probe details stay hidden");

  await page.goto(`http://127.0.0.1:${server.address().port}/?verified=1&pending=1`);
  await wait(async () => (await page.locator("[data-chatgpt-active-badge]").count()) === 1, "verified pending badge");
  const pendingVerifiedBadge = page.locator("[data-chatgpt-active-badge]");
  assert.ok((await pendingVerifiedBadge.getAttribute("class")).includes("emerald"), "verified connected state is green while authorization is pending");
  assert.equal(await page.getByText("Connection test passed.", { exact: true }).count(), 1);
  assert.equal(await page.getByText("text: ok - Provider test succeeded.", { exact: false }).count(), 0, "technical probe details stay hidden");
  assert.equal(await page.getByText("Another sign-in is waiting in the browser; the current connection remains usable.", { exact: true }).count(), 1);
  assert.equal(await page.getByRole("button", { name: "Load models" }).isEnabled(), true, "pending authorization does not disable the current connection");
  assert.equal(await page.getByRole("button", { name: "Cancel sign-in" }).count(), 1);
  assert.equal(await page.locator("[data-chatgpt-refresh]").count(), 1, "refresh stays in the header group");

  await page.goto(`http://127.0.0.1:${server.address().port}/?verified=1&pending=0&developer=1`);
  await wait(async () => (await page.locator("[data-chatgpt-active-badge]").count()) === 1, "verified badge");
  const verifiedBadge = page.locator("[data-chatgpt-active-badge]");
  assert.ok((await verifiedBadge.getAttribute("class")).includes("emerald"), "matching verified text test turns the badge green");
  assert.equal(await page.getByText("text: ok - Provider test succeeded.", { exact: false }).count(), 1, "developer options reveal technical probe details");

  for (const language of ["en-US", "zh-CN"]) {
    const labels = language === "zh-CN"
      ? ["重新登录", "添加其他账户", "退出登录", "加载模型", "测试连接"]
      : ["Sign in again", "Add another account", "Sign out", "Load models", "Test connection"];
    await page.goto(`http://127.0.0.1:${server.address().port}/?verified=1&lang=${language}`);
    await wait(async () => await page.getByRole("button", { name: labels[3], exact: true }).isEnabled(), "catalog loaded before layout check");
    for (const width of [900, 420]) {
      await page.setViewportSize({ width, height: 860 });
      const panel = await page.locator("[data-chatgpt-oauth-panel]").boundingBox();
      const account = await page.locator("select").first().boundingBox();
      const refresh = await page.locator("[data-chatgpt-refresh]").boundingBox();
      const model = await page.locator("[data-chatgpt-model-select]").boundingBox();
      assert.ok(panel && account && refresh && model && model.width > 120, "model selector remains visible");
      assert.ok(refresh.y < account.y, "refresh remains above the account actions");
      const rects = [];
      for (const label of labels) {
        const button = page.getByRole("button", { name: label, exact: true });
        const rect = await button.boundingBox();
        assert.ok(rect && rect.x >= panel.x && rect.x + rect.width <= panel.x + panel.width + 1, `${label} fits the panel`);
        assert.ok(await button.evaluate((element) => element.scrollWidth <= element.clientWidth + 1), `${label} does not overflow`);
        rects.push(rect);
      }
      for (const group of [rects.slice(0, 3), rects.slice(3)]) {
        for (const rect of group.slice(1)) {
          assert.ok(Math.abs(rect.width - group[0].width) <= 1, "buttons in each group have equal widths");
          assert.ok(Math.abs((width === 900 ? rect.y : rect.x) - (width === 900 ? group[0].y : group[0].x)) <= 1, "buttons stay aligned across responsive rows");
        }
      }
    }
  }
  console.log("ChatGPT OAuth status card: verification, pending coexistence, details gating, and responsive header passed");
} finally {
  if (browser) await browser.close();
  await new Promise((resolveServer) => server.close(resolveServer));
}
