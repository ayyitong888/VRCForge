// Real React/browser regression for ChatGPT OAuth account-button payloads.
// The before mode recreates the pre-fix handler in the in-memory bundle only;
// it never edits the product source and never contacts the OAuth provider.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.VRCFORGE_PLAYWRIGHT_PATH || "playwright");
const beforeFix = process.env.VRCFORGE_OAUTH_TEST_BEFORE === "1";
const sourcePath = resolve("src/features/chatgpt-oauth/settings.tsx");
const fixtureSource = `
  import React from "react";
  import { createRoot } from "react-dom/client";
  import i18n from "i18next";
  import { initReactI18next } from "react-i18next";
  import en from "./src/locales/en-US.json";
  import { ChatGptOAuthSettings } from "./src/features/chatgpt-oauth/settings";
  i18n.use(initReactI18next).init({ lng: "en-US", resources: { "en-US": { translation: en } } });
  window.open = () => ({ opener: null, location: { replace() {} }, close() {} });
  function Fixture() {
    return <ChatGptOAuthSettings
      endpoint={location.origin}
      runtimeConnected={true}
      apiProvider="custom"
      apiModel=""
      savingApiConfig={false}
      testingProvider=""
      providerTestMessage=""
      onTestProvider={() => {}}
      onProviderConfigChanged={() => {}}
    />;
  }
  createRoot(document.getElementById("root")).render(<Fixture />);
`;

const plugins = [{
  name: "oauth-pre-fix-source",
  setup(buildApi) {
    buildApi.onLoad({ filter: /settings\.tsx$/ }, async (args) => {
      if (resolve(args.path) !== sourcePath) return undefined;
      let source = readFileSync(args.path, "utf8").replace(/\r\n/g, "\n");
      const selectionEffect = `useEffect(() => {
    if (!status)`;
      const guardedSelectionEffect = `useEffect(() => {
    if ((window as any).__oauthDeferSelectionEffect) return;
    if (!status)`;
      assert.ok(source.includes(selectionEffect), "fixture could not locate the account-selection effect");
      source = source.replace(selectionEffect, guardedSelectionEffect);
      const fixed = `() => beginChatGptOAuthLogin(
                endpoint,
                status?.accounts.some((account) => account.id === selectedAccountId)
                  ? selectedAccountId
                  : "",
              ),`;
      const legacy = `() => beginChatGptOAuthLogin(endpoint, selectedAccountId),`;
      if (beforeFix) {
        assert.ok(source.includes(fixed), "pre-fix fixture could not locate the current guarded handler");
        source = source.replace(fixed, legacy);
      }
      return { contents: source, loader: "tsx", resolveDir: resolve(args.path, "..") };
    });
  },
}];
const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: fixtureSource },
  bundle: true,
  write: false,
  format: "iife",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"test"' },
  plugins,
});

const requests = [];
let returnEmptyOnNextStatus = false;
const server = createServer((req, res) => {
  if (req.url === "/api/chatgpt/status" && req.method === "GET") {
    const accounts = returnEmptyOnNextStatus ? [] : [{ id: "current-account", label: "Synthetic account", email: "", connected: false }];
    returnEmptyOnNextStatus = false;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ accounts, pending: false, error: "", activeAccountId: "", activeModel: "" }));
  }
  if (req.url === "/api/chatgpt/login" && req.method === "POST") {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      requests.push(JSON.parse(body));
      res.setHeader("Content-Type", "application/json");
      res.end(JSON.stringify({ authorizationUrl: "https://auth.openai.com/api/accounts/authorize?synthetic=1" }));
    });
    return;
  }
  if (req.url === "/fixture/empty" && req.method === "POST") {
    returnEmptyOnNextStatus = true;
    res.statusCode = 204;
    res.end();
    return;
  }
  if (req.url === "/fixture.js") {
    res.setHeader("Content-Type", "text/javascript");
    res.end(bundle.outputFiles[0].text);
    return;
  }
  if (req.url === "/") {
    res.setHeader("Content-Type", "text/html");
    res.end("<div id=\"root\"></div><script src=\"/fixture.js\"></script>");
    return;
  }
  res.statusCode = 404;
  res.end();
});
await new Promise((resolveServer) => server.listen(0, "127.0.0.1", resolveServer));

let browser;
try {
  browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || "msedge" });
  const page = await browser.newPage();
  const wait = async (predicate, label) => {
    for (let i = 0; i < 300; i += 1) {
      if (await predicate()) return;
      await new Promise((resolveWait) => setTimeout(resolveWait, 10));
    }
    assert.fail(`timed out: ${label}`);
  };
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await wait(async () => (await page.locator("select").inputValue()) === "current-account", "initial account selection");

  await page.getByRole("button", { name: "Sign in to ChatGPT" }).click();
  await wait(() => requests.length === 1, "ordinary login request");
  assert.equal(requests[0].account_id, "current-account", "ordinary login uses the selected existing account");
  await page.waitForTimeout(30);

  await page.getByRole("button", { name: "Add another account" }).click();
  await wait(() => requests.length === 2, "add-account request");
  assert.equal(requests[1].account_id, "", "add-account always uses dynamic registration");
  await page.waitForTimeout(30);

  // Keep the selected ID stale after the refreshed status becomes empty so the
  // real rendered button handler is exercised before its selection effect runs.
  await page.evaluate(() => { window.__oauthDeferSelectionEffect = true; });
  await page.evaluate(() => fetch("/fixture/empty", { method: "POST" }));
  await page.evaluate(() => {
    window.__staleClick = new Promise((resolveClick) => {
      const panel = document.querySelector("[data-chatgpt-oauth-panel]");
      const clickBeforeSelectionEffect = () => {
        const select = panel?.querySelector("select");
        const hasCurrentAccountOption = Array.from(select?.options || []).some((option) => option.value === "current-account");
        if (hasCurrentAccountOption || select?.options.length !== 1) return;
        const button = Array.from(panel.querySelectorAll("button")).find((item) => item.textContent?.includes("Sign in to ChatGPT"));
        if (!button) return;
        observer.disconnect();
        button.click();
        resolveClick();
      };
      const observer = new MutationObserver(clickBeforeSelectionEffect);
      observer.observe(panel, { childList: true, subtree: true });
    });
  });
  await page.getByRole("button", { name: "Refresh status" }).click();
  await page.evaluate(() => window.__staleClick);
  await wait(() => requests.length === 3, "stale-selection ordinary login request");
  assert.equal(requests[2].account_id, "", "a selected ID absent from refreshed status becomes dynamic registration");

  console.log(`${beforeFix ? "pre-fix expected failure fixture" : "current source"}: rendered OAuth button payload contract passed`);
} finally {
  if (browser) await browser.close();
  await new Promise((resolveServer) => server.close(resolveServer));
}
